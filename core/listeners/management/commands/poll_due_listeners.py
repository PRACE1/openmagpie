import logging
import time
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from listeners.services import (
    JudgeProgress,
    ListenerService,
    PollEvent,
    StreamStarted,
    poll_listener,
)

logger = logging.getLogger("listeners")

_TITLE_TRUNCATE = 80
# Heartbeat interval during miss-streaks. Time-based (not count-based)
# because the slow leg is the engine call, and engines have wildly
# different per-judgment latencies (multi-second LLMs vs.
# microsecond-keyword matchers). 60s is the floor where the operator
# stops asking "is it hung?" without flooding the log on fast runs.
_HEARTBEAT_SECONDS = 60


class Command(BaseCommand):
    help = "Poll every active listener whose poll_interval_seconds has elapsed. The scheduler entry point."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--quiet",
            action="store_true",
            help=(
                "Suppress the per-observation progress output entirely. "
                "Useful in production schedulers where only the summary "
                "matters; keep it off in dev so a long cold-start poll "
                "isn't silent."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        quiet = bool(options.get("quiet"))
        now = timezone.now()
        total_listeners = 0
        total_observed = 0
        total_hits = 0
        total_skipped = 0
        total_errored = 0
        for listener in ListenerService.Global.list_due_for_poll(now=now):
            # Listener name first so the operator sees liveness before
            # the connector fetch + first engine call (which together
            # can take 30+ seconds on a cold cycle).
            self.stdout.write(f"\n{listener.name}")
            printer = _ProgressPrinter(self.stdout.write, quiet=quiet)
            started_at = time.monotonic()
            # Recoverable per-stream/per-obs failures are already isolated
            # inside the poll op. This guard is for the non-recoverable
            # rest (a bad engine.kind, a connector missing poll, ...): one
            # malformed listener must not abort the whole scheduler run and
            # starve every listener after it. Log + skip, keep going.
            try:
                result = poll_listener(listener, on_progress=printer)
            except Exception:
                total_errored += 1
                logger.exception(
                    "listener poll aborted listener=%s name=%s",
                    listener.id,
                    listener.name,
                )
                self.stdout.write("  error: poll aborted (see logs); skipping listener")
                continue
            if result is None:
                total_skipped += 1
                self.stdout.write("  skipped (lock held by another process)")
                continue
            total_listeners += 1
            total_observed += result.observed
            total_hits += result.hits
            # Suppress the per-listener tally on cold-start: the
            # stream-line ("cold-start snapshot") already said all
            # there was to say. Print only when we actually judged.
            if result.observed > 0 or result.hits > 0:
                elapsed = time.monotonic() - started_at
                noun = "hit" if result.hits == 1 else "hits"
                self.stdout.write(
                    f"  result: {result.hits} {noun} in {_format_seconds(elapsed)}"
                )

        if total_listeners == 0 and total_skipped == 0 and total_errored == 0:
            self.stdout.write("No listeners due for poll.")
        else:
            self.stdout.write(
                f"\nPolled {total_listeners} listener(s), "
                f"{total_observed} observed, {total_hits} hits, "
                f"{total_skipped} skipped, {total_errored} errored"
            )


class _ProgressPrinter:
    """Renders poll progress with batched output.

    Consumes the two `PollEvent` flavors from `listeners.services`:
      - `StreamStarted`: prints a per-stream header. Captures the
        connector's `expected_max` so subsequent heartbeats can render
        N/total + ETA when available.
      - `JudgeProgress`: per-observation. Hits and errors print
        immediately; misses are silent except for one heartbeat every
        `_HEARTBEAT_SECONDS` of silence.

    State resets at each `StreamStarted` so a multi-stream listener's
    per-stream progress is reported cleanly.
    """

    def __init__(self, write: Any, *, quiet: bool = False) -> None:
        self._write = write
        self._quiet = quiet
        self._reset_stream(expected_max=0)

    def _reset_stream(self, *, expected_max: int) -> None:
        self._judged = 0
        self._hits = 0
        self._errors = 0
        self._expected_max = expected_max
        now = time.monotonic()
        self._stream_started_at = now
        self._last_print_at = now

    def __call__(self, event: PollEvent) -> None:
        if isinstance(event, StreamStarted):
            self._on_stream_started(event)
        else:  # JudgeProgress
            self._on_judged(event)

    def _on_stream_started(self, event: StreamStarted) -> None:
        self._reset_stream(expected_max=event.expected_max)
        if self._quiet:
            return
        # 0 = cold-start snapshot (watermark just set to now, nothing
        # to judge). Anything else is the connector's pre-counted total
        # for this warm cycle.
        if event.expected_max == 0:
            tail = "cold-start snapshot"
        else:
            tail = (
                f"{event.expected_max} items to judge "
                f"| live hits, status every ~{_HEARTBEAT_SECONDS}s"
            )
        self._write(f"  {event.stream_display}  {tail}")

    def _on_judged(self, event: JudgeProgress) -> None:
        self._judged += 1
        if self._quiet:
            return

        title = _format_title(event)

        if event.error is not None:
            self._errors += 1
            self._write(f"    [{self._format_n()}] ERR  {event.error[:60]} :: {title}")
            self._last_print_at = time.monotonic()
            return

        if event.hit:
            self._hits += 1
            score = f"{event.score:.2f}" if event.score is not None else "  --"
            self._write(f"    [{self._format_n()}] HIT  {score} {title}")
            self._last_print_at = time.monotonic()
            return

        # Miss. Stay quiet unless the elapsed-silence floor is reached.
        if time.monotonic() - self._last_print_at >= _HEARTBEAT_SECONDS:
            self._write(f"    [{self._format_n()}] ...  {self._stats_summary()}")
            self._last_print_at = time.monotonic()

    def _format_n(self) -> str:
        # `expected_max` is a best-effort pre-count (count and poll are
        # separate upstream walks; a high-churn feed can grow between
        # them). Mark the denominator `~` once judged overruns it so the
        # display reads as an estimate, not a broken `12/8`.
        denom = (
            f"~{self._expected_max}"
            if self._judged > self._expected_max
            else f"{self._expected_max}"
        )
        return f"{self._judged:>4}/{denom}"

    def _stats_summary(self) -> str:
        """Heartbeat body: percent + counts + throughput + ETA."""
        elapsed = time.monotonic() - self._stream_started_at
        avg = elapsed / self._judged if self._judged else 0.0
        remaining = max(self._expected_max - self._judged, 0)
        eta = remaining * avg
        # Clamp at 100: a best-effort pre-count overrun must not print
        # 137% (see _format_n).
        pct = (
            min(100.0, 100.0 * self._judged / self._expected_max)
            if self._expected_max
            else 0.0
        )
        return (
            f"{pct:.0f}%, "
            f"{self._hits} hit, {self._errors} err, "
            f"avg {_format_seconds(avg)}/item, "
            f"~{_format_seconds(eta)} left"
        )


def _format_title(event: JudgeProgress) -> str:
    title = (event.obs.title or "").strip().replace("\n", " ")
    if len(title) > _TITLE_TRUNCATE:
        title = title[: _TITLE_TRUNCATE - 1] + "…"
    return title


def _format_seconds(seconds: float) -> str:
    """Human duration: `42s`, `7m30s`, `2h15m`. Trims sub-minute precision
    on long durations so the heartbeat line stays compact."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m{int(seconds % 60):02d}s"
    hours = minutes // 60
    return f"{hours}h{minutes % 60:02d}m"

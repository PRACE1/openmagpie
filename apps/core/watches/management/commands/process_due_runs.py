import logging
import time
from typing import Any

from django.utils import timezone

from common.commands import SingleFlightCommand
from watches.operations.drain import WatchDrainOperation
from watches.services import WatchActionRunService

logger = logging.getLogger("watches")


def _fmt_duration(seconds: float) -> str:
    """Coarse h/m/s for the progress line ; sub-minute keeps seconds."""
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    if s >= 60:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s}s"


def _progress(processed: int, total: int, t_start: float) -> str:
    """`[12/345, ~10m04s left]` — ETA = running average wall-time per run
    times the runs still queued. `total` is the due count snapshotted at
    pass start ; if more fell due mid-pass the remaining floors at 0 (ETA
    just reads ~0s near the end) rather than going negative."""
    elapsed = time.monotonic() - t_start
    avg = elapsed / processed if processed else 0.0
    remaining = max(total - processed, 0)
    return f"[{processed}/{total}, ~{_fmt_duration(avg * remaining)} left]"


# Single-flight here is a convenience (don't stack passes on one box), NOT
# a correctness requirement: the CAS claim already makes concurrent drains
# safe ; they split the queue. To scale the drain horizontally across
# machines, drop back to plain BaseCommand so N workers run at once.
class Command(SingleFlightCommand):
    help = (
        "Drain pass: reap stale runs, then claim + execute every due run, advancing the chain. Scheduler entry point."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress per-run progress; print only the summary.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        quiet = bool(options.get("quiet"))
        now = timezone.now()

        # Reap first so a crashed-worker run (stuck RUNNING) rejoins the
        # retry pool before this pass claims, instead of waiting a tick.
        reaped = WatchActionRunService.Global.reap_stale(now=now)
        if reaped and not quiet:
            self.stdout.write(f"reaped {reaped} stale run(s)")

        executed = 0
        infra_failed = 0
        lost_claims = 0
        by_state: dict[str, int] = {}

        # Snapshot the due count up front so the per-run line can show
        # position + ETA. A semantic_filter run is a slow (~120s) LLM call,
        # so a backlog can take many minutes ; the ETA tells the operator
        # whether to wait. Cheap COUNT, no rows materialized.
        total_due = WatchActionRunService.Global.count_due(now=now)
        t_start = time.monotonic()

        for processed, run in enumerate(WatchActionRunService.Global.claim_due(now=now), start=1):
            # Per-run try/except: an UNEXPECTED error (e.g. the commit
            # itself) must not abort the pass. The run stays RUNNING and the
            # next reap retries it; one bad run never starves the queue.
            try:
                outcome = WatchDrainOperation(run, now=now).run()
            except Exception as exc:
                infra_failed += 1
                logger.exception("drain failed run=%s: %s", run.id, exc)
                detail = f"failed: {type(exc).__name__}: {exc}"
            else:
                if outcome is None:
                    # Claim lost: this run was reaped + re-claimed by another
                    # drain mid-judge ; the fresh winner owns the result + the
                    # chain advance, so we drop ours (don't count, don't advance).
                    lost_claims += 1
                    detail = "claim lost (handled by another worker)"
                else:
                    executed += 1
                    detail = outcome.state.value
                    by_state[detail] = by_state.get(detail, 0) + 1
            if not quiet:
                self.stdout.write(f"  run {run.id}: {detail} {_progress(processed, total_due, t_start)}")

        breakdown = ", ".join(f"{n} {s}" for s, n in sorted(by_state.items())) or "none"
        self.stdout.write(
            f"\nReaped {reaped}, executed {executed} run(s) ({breakdown}), "
            f"{lost_claims} claim(s) lost, {infra_failed} infra-failed "
            f"in {_fmt_duration(time.monotonic() - t_start)}"
        )

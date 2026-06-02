import logging
from typing import Any

from django.utils import timezone

from common.commands import SingleFlightCommand
from watches.operations.drain import WatchDrainOperation
from watches.services import WatchActionRunService

logger = logging.getLogger("watches")


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

        for run in WatchActionRunService.Global.claim_due(now=now):
            # Per-run try/except: an UNEXPECTED error (e.g. the commit
            # itself) must not abort the pass. The run stays RUNNING and the
            # next reap retries it; one bad run never starves the queue.
            try:
                outcome = WatchDrainOperation(run, now=now).run()
            except Exception as exc:
                infra_failed += 1
                logger.exception("drain failed run=%s: %s", run.id, exc)
                if not quiet:
                    self.stdout.write(f"  run {run.id}: failed: {type(exc).__name__}: {exc}")
                continue
            if outcome is None:
                # Claim lost: this run was reaped + re-claimed by another
                # drain mid-judge ; the fresh winner owns the result + the
                # chain advance, so we drop ours (don't count, don't advance).
                lost_claims += 1
                if not quiet:
                    self.stdout.write(f"  run {run.id}: claim lost (handled by another worker)")
                continue
            executed += 1
            state = outcome.state.value
            by_state[state] = by_state.get(state, 0) + 1
            if not quiet:
                self.stdout.write(f"  run {run.id}: {state}")

        breakdown = ", ".join(f"{n} {s}" for s, n in sorted(by_state.items())) or "none"
        self.stdout.write(
            f"\nReaped {reaped}, executed {executed} run(s) ({breakdown}), "
            f"{lost_claims} claim(s) lost, {infra_failed} infra-failed"
        )

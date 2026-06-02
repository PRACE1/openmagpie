import logging
from typing import Any

from common.commands import SingleFlightCommand
from watches.operations.trigger import FeedTriggered, WatchTriggerOperation
from watches.services import WatchService

logger = logging.getLogger("watches")


class Command(SingleFlightCommand):
    help = (
        "Trigger pass: enqueue first-action runs for new feed items across every active watch. Scheduler entry point."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress per-watch progress; print only the summary.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        quiet = bool(options.get("quiet"))
        total_watches = 0
        total_enqueued = 0
        total_failed = 0

        def on_progress(ev: FeedTriggered) -> None:
            if quiet:
                return
            self.stdout.write(f"  feed {ev.feed_id}: {ev.enqueued} enqueued")

        for watch in WatchService.Global.iter_active():
            if not quiet:
                self.stdout.write(f"\n{watch.name} | {watch.id}")
            # Per-watch try/except: one watch raising (corrupt path, DB
            # transient) must not starve every watch after it this pass.
            try:
                result = WatchTriggerOperation(watch, on_progress=on_progress).run()
            except Exception as exc:
                total_failed += 1
                logger.exception("trigger failed watch=%s: %s", watch.id, exc)
                if not quiet:
                    self.stdout.write(f"  failed: {type(exc).__name__}: {exc}")
                continue
            total_watches += 1
            total_enqueued += result.runs_enqueued

        self.stdout.write(
            f"\nTriggered {total_watches} watch(es), {total_enqueued} run(s) enqueued, {total_failed} failed"
        )

import logging
from typing import Any

from django.utils import timezone

from common.commands import SingleFlightCommand
from feeds.services import FeedService
from feeds.services.polling import SourcePolled, poll_feed

logger = logging.getLogger("feeds")


class Command(SingleFlightCommand):
    help = "Poll every active feed whose poll_interval_seconds has elapsed. The scheduler entry point."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress per-source progress; print only the summary.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        quiet = bool(options.get("quiet"))
        now = timezone.now()
        total_feeds = 0
        total_observed = 0
        total_recorded = 0
        total_skipped = 0

        def on_progress(ev: SourcePolled) -> None:
            if quiet:
                return
            self.stdout.write(f"  {ev.source_display}: {ev.observed} observed, {ev.recorded} new")

        total_failed = 0
        for feed in FeedService.Global.list_due_for_poll(now=now):
            if not quiet:
                self.stdout.write(f"\n{feed.name} | {feed.id}")
            # Per-feed try/except: a single feed raising a non-recoverable
            # error (unsupported kind, DB transient, connector bug) must NOT
            # starve every feed scheduled after it in this pass.
            try:
                result = poll_feed(feed, on_progress=on_progress)
            except Exception as exc:
                total_failed += 1
                logger.exception("poll_feed failed feed=%s: %s", feed.id, exc)
                if not quiet:
                    self.stdout.write(f"  failed: {type(exc).__name__}: {exc}")
                continue
            if result is None:
                total_skipped += 1
                if not quiet:
                    self.stdout.write("  skipped (lock held by another process)")
                continue
            total_feeds += 1
            total_observed += result.observed
            total_recorded += result.recorded
            if not quiet:
                self.stdout.write(f"  result: {result.recorded} new item(s), {result.pruned} pruned")

        self.stdout.write(
            f"\nPolled {total_feeds} feed(s), {total_observed} observed, "
            f"{total_recorded} recorded, {total_skipped} skipped, {total_failed} failed"
        )

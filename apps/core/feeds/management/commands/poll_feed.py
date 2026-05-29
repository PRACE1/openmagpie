from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from feeds.models import Feed
from feeds.services import FeedService
from feeds.services.polling import FeedPollOperation, SourcePolled


class Command(BaseCommand):
    help = "Poll a single feed by ID. Intended for manual / debugging use (bypasses the due check + lock)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("feed_id", type=str, help="ULID of the feed to poll")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            feed = FeedService.Global.get(options["feed_id"])
        except Feed.DoesNotExist:
            self.stderr.write(f"no feed {options['feed_id']}")
            return

        self.stdout.write(f"{feed.name} | {feed.id}")

        def on_progress(ev: SourcePolled) -> None:
            self.stdout.write(f"  {ev.source_display}: {ev.observed} observed, {ev.recorded} new")

        result = FeedPollOperation(feed, on_progress=on_progress).run()
        self.stdout.write(
            f"\nresult: {result.recorded} new item(s), {result.observed} observed, {result.pruned} pruned"
        )

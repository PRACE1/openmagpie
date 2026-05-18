from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from listeners.services import ListenerService, poll_listener


class Command(BaseCommand):
    help = "Poll a single listener by ID. Intended for manual / debugging use."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "listener_id", type=str, help="ULID of the listener to poll"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        listener = ListenerService.Global.get(options["listener_id"])
        result = poll_listener(listener)
        if result is None:
            self.stdout.write(f"{listener}: skipped (lock held by another process)")
            return
        self.stdout.write(f"{listener}: observed {result.observed}, hits {result.hits}")

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from listeners.services import ListenerService, poll_listener


class Command(BaseCommand):
    help = "Poll every active listener whose poll_interval_seconds has elapsed. The scheduler entry point."

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        total_listeners = 0
        total_observed = 0
        total_hits = 0
        total_skipped = 0
        for listener in ListenerService.Global.list_due_for_poll(now=now):
            result = poll_listener(listener)
            if result is None:
                total_skipped += 1
                self.stdout.write(
                    f"  {listener}: skipped (lock held by another process)"
                )
                continue
            total_listeners += 1
            total_observed += result.observed
            total_hits += result.hits
            self.stdout.write(
                f"  {listener}: observed {result.observed}, hits {result.hits}"
            )

        if total_listeners == 0 and total_skipped == 0:
            self.stdout.write("No listeners due for poll.")
        else:
            self.stdout.write(
                f"\nPolled {total_listeners} listener(s), observed {total_observed}, "
                f"hits {total_hits}, skipped {total_skipped}"
            )

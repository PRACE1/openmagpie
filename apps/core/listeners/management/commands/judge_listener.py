from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from listeners.models import Listener
from listeners.services import ListenerService
from listeners.services.judgment import JudgeListenerOperation, JudgeProgress


class Command(BaseCommand):
    help = "Judge new feed items for a single listener by ID. Manual / debug use (bypasses the lock)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("listener_id", type=str, help="ULID of the listener to judge")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            listener = ListenerService.Global.get(options["listener_id"])
        except Listener.DoesNotExist:
            self.stderr.write(f"no listener {options['listener_id']}")
            return

        self.stdout.write(f"{listener.name} | {listener.id}")

        def on_progress(ev: JudgeProgress) -> None:
            if ev.error:
                self.stdout.write(f"  ERR  {ev.obs.external_id}: {ev.error}")
                return
            mark = "HIT " if ev.hit else "..."
            title = (ev.obs.title or "")[:60]
            self.stdout.write(f"  {mark} {ev.score:.2f} {title}")

        result = JudgeListenerOperation(listener, on_progress=on_progress).run()
        self.stdout.write(f"\nresult: {result.hits} hit(s) in {result.judged} judged")

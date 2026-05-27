from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from common.humanize import ellipsize, humanize_seconds
from listeners.models import Listener
from listeners.services import ListenerService
from listeners.services.judgment import JudgeCycleStarted, JudgeEvent, JudgeListenerOperation


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

        def on_progress(ev: JudgeEvent) -> None:
            if isinstance(ev, JudgeCycleStarted):
                self.stdout.write(f"judging {ev.pending} item(s) (~{humanize_seconds(ev.est_seconds)})")
                # Header row so columns label themselves; widths match
                # the per-item format below (score:>5.2f, title:<60).
                self.stdout.write(f"       score  {'title':<60}  progress")
                return
            # JudgeItemDone
            if ev.error:
                label = ev.external_id or "?"
                self.stdout.write(f"  ERR   {label}: {ev.error}")
                return
            mark = "HIT " if ev.hit else "... "
            title = ellipsize(ev.obs.title or "", 60)
            progress = f"{ev.done}/{ev.total}, ~{humanize_seconds(ev.eta_seconds)} left" if ev.total else ""
            self.stdout.write(f"  {mark} {ev.score:>5.2f}  {title:<60}  {progress}")

        result = JudgeListenerOperation(listener, on_progress=on_progress).run()
        self.stdout.write(f"\nresult: {result.hits} hit(s) in {result.judged} judged")

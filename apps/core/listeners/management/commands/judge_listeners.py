import logging
from typing import Any

from django.core.management.base import BaseCommand

from common.humanize import ellipsize, humanize_seconds
from listeners.services import ListenerService
from listeners.services.judgment import JudgeCycleStarted, JudgeEvent, judge_listener

logger = logging.getLogger("listeners")


class Command(BaseCommand):
    help = "Judge new feed items for every active listener. Run after poll_due_feeds; the judgment entry point."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress per-item progress; print only the summary.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        quiet = bool(options.get("quiet"))
        total_listeners = 0
        total_judged = 0
        total_hits = 0
        total_skipped = 0

        def on_progress(ev: JudgeEvent) -> None:
            if quiet:
                return
            if isinstance(ev, JudgeCycleStarted):
                self.stdout.write(f"  judging {ev.pending} item(s) (~{humanize_seconds(ev.est_seconds)})")
                self.stdout.write(f"         score  {'title':<60}  progress")
                return
            # JudgeItemDone
            if ev.error:
                label = ev.external_id or "?"
                self.stdout.write(f"    ERR   {label}: {ev.error}")
                return
            mark = "HIT " if ev.hit else "... "
            title = ellipsize(ev.obs.title or "", 60)
            progress = f"{ev.done}/{ev.total}, ~{humanize_seconds(ev.eta_seconds)} left" if ev.total else ""
            self.stdout.write(f"    {mark} {ev.score:>5.2f}  {title:<60}  {progress}")

        total_failed = 0
        for listener in ListenerService.Global.list_active():
            if not quiet:
                self.stdout.write(f"\n{listener.name} | {listener.id}")
            # Per-listener try/except: a single listener raising (unsupported
            # kind, malformed data, transient DB error) must NOT starve every
            # listener scheduled after it in this pass.
            try:
                result = judge_listener(listener, on_progress=on_progress)
            except Exception as exc:
                total_failed += 1
                logger.exception("judge_listener failed listener=%s: %s", listener.id, exc)
                if not quiet:
                    self.stdout.write(f"  failed: {type(exc).__name__}: {exc}")
                continue
            if result is None:
                total_skipped += 1
                if not quiet:
                    self.stdout.write("  skipped (lock held by another process)")
                continue
            total_listeners += 1
            total_judged += result.judged
            total_hits += result.hits
            if not quiet:
                self.stdout.write(f"  result: {result.hits} hit(s) in {result.judged} judged")

        self.stdout.write(
            f"\nJudged {total_listeners} listener(s), {total_judged} item(s), "
            f"{total_hits} hits, {total_skipped} skipped, {total_failed} failed"
        )

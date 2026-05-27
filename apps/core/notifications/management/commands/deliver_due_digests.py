import logging
from datetime import datetime
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from listeners import registry as listeners_registry
from listeners.configs import SemanticListenerConfig
from listeners.models import Listener
from listeners.services import ListenerService
from notifications.services import DeliveryService

logger = logging.getLogger("notifications")


class Command(BaseCommand):
    help = "Deliver pending hits for every digest-mode listener whose interval has elapsed."

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        total_listeners = 0
        total_delivered = 0
        total_skipped = 0
        total_failed = 0

        for listener in ListenerService.Global.list_due_for_digest(now=now):
            # Per-listener try/except: a single listener raising
            # (unsupported notifier exception, transient DB error) must
            # NOT starve every listener scheduled after it in this pass.
            # Mirrors the same guard in poll_due_feeds + judge_listeners.
            try:
                delivered = self._deliver_one(listener, now)
            except Exception as exc:
                total_failed += 1
                logger.exception("deliver_digest failed listener=%s: %s", listener.id, exc)
                self.stdout.write(f"  {listener}: failed: {type(exc).__name__}: {exc}")
                continue
            if delivered is None:
                total_skipped += 1
                continue
            total_listeners += 1
            total_delivered += delivered

        if total_listeners == 0 and total_skipped == 0 and total_failed == 0:
            self.stdout.write("No listeners due for digest.")
        else:
            self.stdout.write(
                f"\nDelivered {total_delivered} hit(s) across {total_listeners} listener(s), "
                f"skipped {total_skipped}, {total_failed} failed"
            )

    def _deliver_one(self, listener: Listener, now: datetime) -> int | None:
        """Run one listener's digest cycle. Returns hits delivered, or None
        on any skip (unsupported kind, lock held by another process,
        full-failure retry hold).

        State advance rules: advance `next_digest_at` when delivery made
        progress (anything delivered) OR there was nothing to deliver.
        DO NOT advance on full failure (events were pending and none
        landed) so the very next scheduler tick retries instead of
        waiting a full digest interval.
        """
        config = listeners_registry.load_config(listener)
        if not isinstance(config, SemanticListenerConfig):
            self.stdout.write(f"  {listener}: skipped (unsupported kind={listener.kind})")
            return None

        result = DeliveryService.Global.deliver_digest(listener, config)
        if result is None:
            self.stdout.write(f"  {listener}: skipped (lock held by another process)")
            return None

        if result.all_failed:
            self.stdout.write(
                f"  {listener}: 0/{result.attempted} delivered (all notifiers failed); "
                "leaving next_digest_at, retry next tick"
            )
            return None

        ListenerService(account_id=str(listener.account_id)).update_digest_state(
            listener,
            last_digest_at=now,
            digest_interval_seconds=config.digest_interval_seconds,
        )
        self.stdout.write(f"  {listener}: delivered {result.delivered} hit(s)")
        return result.delivered

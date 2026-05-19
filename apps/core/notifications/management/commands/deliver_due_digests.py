from datetime import datetime
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from listeners import registry as listeners_registry
from listeners.configs import SemanticListenerConfig
from listeners.models import Listener
from listeners.services import ListenerService
from notifications.services import DeliveryService


class Command(BaseCommand):
    help = "Deliver pending hits for every digest-mode listener whose interval has elapsed."

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        total_listeners = 0
        total_delivered = 0
        total_skipped = 0

        for listener in ListenerService.Global.list_due_for_digest(now=now):
            delivered = self._deliver_one(listener, now)
            if delivered is None:
                total_skipped += 1
                continue
            total_listeners += 1
            total_delivered += delivered

        if total_listeners == 0 and total_skipped == 0:
            self.stdout.write("No listeners due for digest.")
        else:
            self.stdout.write(
                f"\nDelivered {total_delivered} hit(s) across {total_listeners} listener(s), skipped {total_skipped}"
            )

    def _deliver_one(self, listener: Listener, now: datetime) -> int | None:
        """Run one listener's digest cycle. Returns hits delivered, or None
        on any skip (unsupported kind, lock held by another process)."""
        config = listeners_registry.load_config(listener)
        if not isinstance(config, SemanticListenerConfig):
            self.stdout.write(f"  {listener}: skipped (unsupported kind={listener.kind})")
            return None

        delivered = DeliveryService.Global.deliver_digest(listener, config)
        if delivered is None:
            self.stdout.write(f"  {listener}: skipped (lock held by another process)")
            return None

        ListenerService(account_id=str(listener.account_id)).update_digest_state(
            listener,
            last_digest_at=now,
            digest_interval_seconds=config.digest_interval_seconds,
        )
        self.stdout.write(f"  {listener}: delivered {delivered} hit(s)")
        return delivered

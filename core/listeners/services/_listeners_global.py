"""Cross-tenant Listener operations.

Do NOT import from this file directly — use `ListenerService.Global.<op>(...)`.
The leading underscore signals "implementation detail of listeners.py"; the
`.Global` namespace on the service class is the stable public surface.

Reach for these sparingly — scheduler entry points + admin / debug commands.
"""

from collections.abc import Iterator
from datetime import datetime

from django.db.models import Q
from listeners.models import Listener


class ListenerGlobal:
    """Static methods only. Span all accounts."""

    @staticmethod
    def get(id: str) -> Listener:
        """Look up a Listener regardless of account. Raises DoesNotExist if missing.
        Use only in system-level contexts (scheduler, management commands)."""
        return Listener.objects.get(id=id)

    @staticmethod
    def list_due_for_poll(
        *, now: datetime, chunk_size: int = 100
    ) -> Iterator[Listener]:
        """Active Listeners whose next_poll_at has elapsed (or is unset).
        Spans all accounts — scheduler entry point."""
        return (
            Listener.objects.filter(is_active=True)
            .filter(Q(next_poll_at__isnull=True) | Q(next_poll_at__lte=now))
            .iterator(chunk_size=chunk_size)
        )

    @staticmethod
    def list_due_for_digest(
        *, now: datetime, chunk_size: int = 100
    ) -> Iterator[Listener]:
        """Active digest-mode Listeners whose digest interval has elapsed.
        Spans all accounts — scheduler entry point."""
        return (
            Listener.objects.filter(
                is_active=True, delivery_mode=Listener.DeliveryMode.DIGEST
            )
            .filter(Q(next_digest_at__isnull=True) | Q(next_digest_at__lte=now))
            .iterator(chunk_size=chunk_size)
        )

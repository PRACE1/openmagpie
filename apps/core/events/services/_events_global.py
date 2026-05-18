"""Cross-tenant Event operations.

Do NOT import from this file directly, use `EventService.Global.<op>(...)`.
The leading underscore signals "implementation detail of events.py"; the
`.Global` namespace on the service class is the stable public surface.

Reach for these sparingly, admin / debug commands and system-level sweeps.
"""

from collections.abc import Iterator

from events.models import Event


class EventGlobal:
    """Static methods only. Span all accounts."""

    @staticmethod
    def get(id: str) -> Event:
        """Look up an Event regardless of account. Raises DoesNotExist if missing.
        Use only in system-level contexts (admin / debug)."""
        return Event.objects.get(id=id)

    @staticmethod
    def iter_pending(chunk_size: int = 500) -> Iterator[Event]:
        """Every undelivered Event across all accounts.

        Use for system-wide audit or retry sweeps. Per-listener digest delivery
        should go through `EventService(account_id=…).list_pending_for_listener`
        instead, this is intentionally unscoped.
        """
        return Event.objects.filter(delivered_at__isnull=True).iterator(
            chunk_size=chunk_size
        )

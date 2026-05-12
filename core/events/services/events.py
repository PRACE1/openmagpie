"""Event service. Only module that touches Event.objects directly.

The pipeline is hit-only: an Event row exists if and only if a Listener's engine
judged its observation relevant. `EventService.persist_hit` is the single write
path. Each Event is owned by exactly one Listener (`Event.listener_id`).

EventService is account-scoped: construct with `EventService(account_id=X)` and
every query / write is automatically filtered to that account. Cross-account
misuse raises ValueError at the seam.

Cross-tenant operations live under `EventService.Global` — body in
`_events_global.py`, attached as a class attribute.
"""

import logging
from collections.abc import Iterator
from datetime import datetime

from django.db import IntegrityError, transaction
from events.models import Event
from events.observations import Observation
from listeners.models import Listener

from ._events_global import EventGlobal

logger = logging.getLogger("events")


class EventService:
    """Account-scoped service for Event reads and writes."""

    Global = EventGlobal

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("EventService requires account_id")
        self.account_id = account_id

    def _assert_scope(self, account_id: str, what: str) -> None:
        if account_id != self.account_id:
            raise ValueError(
                f"{what} account_id mismatch: {account_id!r} not in scope {self.account_id!r}"
            )

    def persist_hit(self, observation: Observation, listener: Listener) -> Event | None:
        """Persist a confirmed hit, idempotently.

        Returns the newly created Event, or None if (listener_id, source, external_id)
        already has an Event row. The unique constraint enforces dedup at the DB; we
        catch the IntegrityError so re-polled observations don't abort the caller.

        The INSERT is wrapped in an explicit `transaction.atomic()` (with savepoint
        semantics) so the IntegrityError catch doesn't leave a broken transaction
        on PostgreSQL. Commit happens *before* return so callers (notably the
        instant-mode delivery path) see a durable row before firing webhooks.
        """
        self._assert_scope(observation.account_id, "observation")
        self._assert_scope(str(listener.account_id), "listener")

        event = Event(
            user_id=observation.user_id,
            account_id=self.account_id,
            listener_id=str(listener.id),
            source=observation.source,
            kind=observation.kind,
            external_id=observation.external_id,
            occurred_at=observation.occurred_at,
            data=observation.model_dump(mode="json"),
        )
        try:
            with transaction.atomic():
                event.save()
        except IntegrityError:
            logger.info(
                "persist_hit dedup listener=%s source=%s external_id=%s",
                listener.id,
                observation.source,
                observation.external_id,
            )
            return None
        return event

    def list_pending_for_listener(
        self, *, listener_id: str, chunk_size: int = 100
    ) -> Iterator[Event]:
        """Undelivered Events for a listener (digest scheduler uses this)."""
        return Event.objects.filter(
            account_id=self.account_id,
            listener_id=listener_id,
            delivered_at__isnull=True,
        ).iterator(chunk_size=chunk_size)

    def list_recent_for_listener(
        self, *, listener_id: str, limit: int = 25
    ) -> list[Event]:
        """Most recent Events for a listener, ordered by occurred_at desc.
        Materialized as a list (capped) — ad-hoc spot-check use only."""
        return list(
            Event.objects.filter(
                account_id=self.account_id, listener_id=listener_id
            ).order_by("-occurred_at")[:limit]
        )

    def mark_delivered(self, *, event_ids: list[str], delivered_at: datetime) -> None:
        """Bulk-mark Events as delivered. Scoped to this service's account."""
        Event.objects.filter(account_id=self.account_id, id__in=event_ids).update(
            delivered_at=delivered_at
        )

    def mark_one_delivered(self, event: Event, /, *, delivered_at: datetime) -> None:
        """Mark a single Event as delivered. Used by instant-mode delivery."""
        self._assert_scope(str(event.account_id), "event")
        event.delivered_at = delivered_at
        event.save(update_fields=["delivered_at", "updated_at"])

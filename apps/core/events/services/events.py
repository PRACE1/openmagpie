"""Event service. Only module that touches Event.objects directly.

The pipeline is hit-only today: an Event row exists if and only if a Listener's
engine judged its observation relevant. The service surface is kind-generic
(`persist`, `list_pending_for_listener`, `list_recent_for_listener` all take
`kind=`) so a second event kind (`feed_change`, `delivery`, ...) doesn't grow
a parallel method-per-kind. Callers pass `kind=EventKind.HIT` for hits today.

EventService is account-scoped: construct with `EventService(account_id=X)` and
every query / write is automatically filtered to that account. Cross-account
misuse raises ValueError at the seam.

Cross-tenant operations live under `EventService.Global`, body in
`_events_global.py`, attached as a class attribute.
"""

import logging
from collections.abc import Iterator
from datetime import datetime

from django.db import IntegrityError, transaction

from events.models import Event
from feeds.models import FeedItem
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
            raise ValueError(f"{what} account_id mismatch: {account_id!r} not in scope {self.account_id!r}")

    def persist(
        self,
        feed_item: FeedItem,
        listener: Listener,
        *,
        kind: str,
        score: float | None = None,
    ) -> Event | None:
        """Persist an event, idempotently.

        Returns the newly created Event, or None if this listener already
        recorded an event of this kind for this FeedItem. The unique
        constraint (kind, listener_id, feed_item_id) enforces dedup at the
        DB; we catch the IntegrityError so re-running doesn't abort the
        caller.

        The Event keeps its own `data` snapshot of the FeedItem so
        delivery survives FeedItem retention pruning. INSERT is wrapped in
        `transaction.atomic()` so the IntegrityError catch doesn't leave a
        broken transaction on PostgreSQL; commit happens before return so
        the instant-delivery path sees a durable row before firing.
        """
        self._assert_scope(str(listener.account_id), "listener")
        self._assert_scope(str(feed_item.account_id), "feed_item")

        event = Event(
            user_id=str(listener.user_id),
            account_id=self.account_id,
            listener_id=str(listener.id),
            kind=kind,
            source=str(feed_item.source),
            external_id=str(feed_item.external_id),
            feed_item_id=str(feed_item.id),
            score=score,
            data=feed_item.data,
        )
        try:
            with transaction.atomic():
                event.save()
        except IntegrityError:
            logger.info(
                "persist dedup kind=%s listener=%s source=%s external_id=%s",
                kind,
                listener.id,
                feed_item.source,
                feed_item.external_id,
            )
            return None
        return event

    def list_pending_for_listener(self, *, kind: str, listener_id: str, chunk_size: int = 100) -> Iterator[Event]:
        """Undelivered events of `kind` for a listener (digest scheduler uses this)."""
        return Event.objects.filter(
            account_id=self.account_id,
            listener_id=listener_id,
            kind=kind,
            delivered_at__isnull=True,
        ).iterator(chunk_size=chunk_size)

    def list_recent_for_listener(self, *, kind: str, listener_id: str, limit: int = 25) -> list[Event]:
        """Most recent events of `kind` for a listener, newest-first by ULID pk.
        Materialized (capped), ad-hoc spot-check use only."""
        return list(
            Event.objects.filter(account_id=self.account_id, listener_id=listener_id, kind=kind).order_by("-id")[:limit]
        )

    def mark_delivered(self, *, event_ids: list[str], delivered_at: datetime) -> None:
        """Bulk-mark Events as delivered. Scoped to this service's account."""
        Event.objects.filter(account_id=self.account_id, id__in=event_ids).update(delivered_at=delivered_at)

    def mark_one_delivered(self, event: Event, /, *, delivered_at: datetime) -> None:
        """Mark a single Event as delivered. Used by instant-mode delivery."""
        self._assert_scope(str(event.account_id), "event")
        event.delivered_at = delivered_at
        event.save(update_fields=["delivered_at", "updated_at"])

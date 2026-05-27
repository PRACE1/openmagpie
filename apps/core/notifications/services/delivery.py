"""Delivery service, fire notifiers for hits, manage `Event.delivered_at`.

Account-scoped: `DeliveryService(account_id=X)`. Internally constructs the
EventService it needs (same account scope).

Cross-tenant operations live under `DeliveryService.Global`, body in
`_delivery_global.py`, attached here as a class attribute. The Global helpers
lazy-import `DeliveryService` to avoid a circular import at module load.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

from events.models import Event
from events.observations import Observation
from events.registry import UnhydrateableObservation
from events.registry import hydrate as hydrate_event
from events.services import EventKind, EventService
from listeners.configs import SemanticListenerConfig
from listeners.models import Listener
from notifications import registry as notifiers_registry
from notifications.notifiers.base import HitBatch, NotificationResult

from ._delivery_global import DeliveryGlobal

logger = logging.getLogger("notifications")

# Per-batch cap for digest delivery. The full pending set is streamed via
# EventService's iterator; we collect at most this many before firing one
# webhook, then continue. Bounds memory + the webhook body size regardless
# of how backlogged a listener is.
DIGEST_BATCH_HITS = 100


@dataclass(frozen=True)
class DigestResult:
    """Outcome of one Listener's digest cycle.

    `delivered`: hits actually marked delivered_at this cycle.
    `attempted`: pending events the call tried to send this cycle.

    The scheduler uses `all_failed` to decide whether to advance the
    listener's next_digest_at. "Nothing pending" (attempted == 0) and
    "everything failed" (attempted > 0, delivered == 0) both return
    delivered == 0 today; without this distinction the scheduler can't
    tell them apart and ends up delaying retry by a full interval on
    full failure.
    """

    delivered: int
    attempted: int

    @property
    def all_failed(self) -> bool:
        return self.attempted > 0 and self.delivered == 0


class DeliveryService:
    """Account-scoped delivery: fires notifiers and updates Event.delivered_at."""

    Global = DeliveryGlobal

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("DeliveryService requires account_id")
        self.account_id = account_id
        self._events = EventService(account_id=account_id)

    def _assert_scope(self, account_id: str, what: str) -> None:
        if account_id != self.account_id:
            raise ValueError(f"{what} account_id mismatch: {account_id!r} not in scope {self.account_id!r}")

    def _log_result(self, listener: Listener, result: NotificationResult) -> None:
        log = logger.info if result.delivered else logger.warning
        log(
            "notifier delivery listener=%s kind=%s delivered=%s latency_ms=%d error=%r",
            listener.id,
            result.notifier_kind,
            result.delivered,
            result.latency_ms,
            result.error,
        )

    def _fire_all(
        self,
        batch: HitBatch,
        config: SemanticListenerConfig,
        listener: Listener,
    ) -> bool:
        """Fire every configured notifier on this batch. Returns True iff all succeeded."""
        if not config.notifiers:
            return True  # Nothing to deliver to, treat as already-delivered.
        all_ok = True
        for spec in config.notifiers:
            notifier = notifiers_registry.get(spec.kind)
            result = notifier.deliver(batch, spec)
            self._log_result(listener, result)
            all_ok = all_ok and result.delivered
        return all_ok

    def deliver_instant(
        self,
        event: Event,
        observation: Observation,
        listener: Listener,
        config: SemanticListenerConfig,
    ) -> None:
        """Fire notifiers for one hit. Set Event.delivered_at on full success.

        At-least-once contract:
          1. Event row was already committed by `EventService.persist` (its
             own `transaction.atomic()` block, needed there for the IntegrityError
             savepoint pattern), so the hit is durable before we notify.
          2. Notifiers fire here, external side effects, not transactional.
          3. `mark_one_delivered` flips `delivered_at` (single autocommit UPDATE).

        Retry path: if notifiers fail (or the process dies between 2 and 3),
        the Event stays at delivered_at=NULL. The next `judge_listener` cycle for
        this Listener picks it up via the stuck-pending retry sweep at the top
        of judgment.py and re-attempts delivery. Webhook receivers should dedupe
        on `(source, external_id)` since retries are at-least-once.
        """
        self._assert_scope(str(event.account_id), "event")
        self._assert_scope(str(listener.account_id), "listener")
        batch = HitBatch(
            listener=listener,
            hits=[observation],
            period_start=None,
            period_end=timezone.now(),
        )
        if self._fire_all(batch, config, listener):
            self._events.mark_one_delivered(event, delivered_at=timezone.now())

    def deliver_digest(self, listener: Listener, config: SemanticListenerConfig) -> DigestResult:
        """Stream pending Events for this listener and fire notifiers in
        fixed-size batches. Returns a DigestResult so the scheduler can
        distinguish "nothing pending" (advance state) from "everything
        failed" (do NOT advance state, retry next tick).

        Memory is bounded to `DIGEST_BATCH_HITS` events at a time; the
        EventService iterator pages the underlying query. A failed batch
        leaves its events pending (delivered_at stays NULL); successful
        earlier batches stay marked delivered. Partial failure still
        advances digest state — the remainder gets picked up by the next
        scheduler tick without re-sending what already landed.
        """
        self._assert_scope(str(listener.account_id), "listener")
        now = timezone.now()
        delivered = 0
        attempted = 0
        batch: list[Event] = []
        for event in self._events.list_pending_for_listener(kind=EventKind.HIT, listener_id=str(listener.id)):
            batch.append(event)
            if len(batch) >= DIGEST_BATCH_HITS:
                attempted += len(batch)
                delivered += self._deliver_digest_batch(batch, listener, config, now)
                batch = []
        if batch:
            attempted += len(batch)
            delivered += self._deliver_digest_batch(batch, listener, config, now)
        return DigestResult(delivered=delivered, attempted=attempted)

    def _deliver_digest_batch(
        self,
        events: list[Event],
        listener: Listener,
        config: SemanticListenerConfig,
        now: datetime,
    ) -> int:
        """Fire notifiers for one digest batch; mark delivered on success.
        Returns hits delivered (0 on failure: events stay pending for retry).

        Un-hydrateable events (connector renamed/removed; `(source, kind)`
        no longer in the registry) are dropped from the batch and marked
        delivered so they don't wedge every future digest cycle on the
        same poison row. Mirrors the per-item skip the judgment path
        applies via `UnhydrateableObservation`.
        """
        observations: list[Observation] = []
        deliverable: list[Event] = []
        poisoned: list[str] = []
        for e in events:
            try:
                observations.append(hydrate_event(e))
                deliverable.append(e)
            except UnhydrateableObservation as exc:
                logger.warning(
                    "digest skipping un-hydrateable event listener=%s event=%s: %s",
                    listener.id,
                    e.id,
                    exc,
                )
                poisoned.append(str(e.id))
        # Quarantine the un-hydrateable rows so they don't return next
        # cycle as still-pending and re-poison the batch every time.
        if poisoned:
            self._events.mark_delivered(event_ids=poisoned, delivered_at=now)
        if not deliverable:
            return 0
        batch = HitBatch(
            listener=listener,
            hits=observations,
            period_start=listener.last_digest_at,
            period_end=now,
        )
        if self._fire_all(batch, config, listener):
            self._events.mark_delivered(event_ids=[str(e.id) for e in deliverable], delivered_at=now)
            return len(deliverable)
        return 0

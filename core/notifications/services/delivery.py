"""Delivery service, fire notifiers for hits, manage `Event.delivered_at`.

Account-scoped: `DeliveryService(account_id=X)`. Internally constructs the
EventService it needs (same account scope).

Cross-tenant operations live under `DeliveryService.Global`, body in
`_delivery_global.py`, attached here as a class attribute. The Global helpers
lazy-import `DeliveryService` to avoid a circular import at module load.
"""

import logging

from django.utils import timezone
from events.models import Event
from events.observations import Observation
from events.registry import hydrate as hydrate_event
from events.services import EventService
from listeners.configs import SemanticListenerConfig
from listeners.models import Listener
from notifications import registry as notifiers_registry
from notifications.notifiers.base import HitBatch, NotificationResult

from ._delivery_global import DeliveryGlobal

logger = logging.getLogger("notifications")


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
            raise ValueError(
                f"{what} account_id mismatch: {account_id!r} not in scope {self.account_id!r}"
            )

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
          1. Event row was already committed by `EventService.persist_hit` (its
             own `transaction.atomic()` block, needed there for the IntegrityError
             savepoint pattern), so the hit is durable before we notify.
          2. Notifiers fire here, external side effects, not transactional.
          3. `mark_one_delivered` flips `delivered_at` (single autocommit UPDATE).

        Retry path: if notifiers fail (or the process dies between 2 and 3),
        the Event stays at delivered_at=NULL. The next `poll_listener` cycle for
        this Listener picks it up via the stuck-pending retry sweep at the top
        of polling.py and re-attempts delivery. Webhook receivers should dedupe
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

    def deliver_digest(self, listener: Listener, config: SemanticListenerConfig) -> int:
        """Build batch from pending Events for this listener and fire notifiers.

        Returns the number of hits successfully delivered (0 on partial / total failure
        so the next scheduler pass re-batches the same pending Events).
        """
        self._assert_scope(str(listener.account_id), "listener")
        pending = list(
            self._events.list_pending_for_listener(listener_id=str(listener.id))
        )
        if not pending:
            return 0

        observations = [hydrate_event(e) for e in pending]
        now = timezone.now()
        batch = HitBatch(
            listener=listener,
            hits=observations,
            period_start=listener.last_digest_at,
            period_end=now,
        )
        if self._fire_all(batch, config, listener):
            self._events.mark_delivered(
                event_ids=[str(e.id) for e in pending], delivered_at=now
            )
            return len(pending)
        return 0

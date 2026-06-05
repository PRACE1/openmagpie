"""WatchActionDeliveryService: account-scoped reads + writes for the outbound
HTTP-call audit (one WatchActionDelivery row per attempt).

`record` persists one attempt ; `find_succeeded` is the flush's dedup lookup
(skip a re-POST when this exact batch already landed) ; `list_for_action`
backs the deliveries CLI / API.
"""

from __future__ import annotations

import builtins
from datetime import datetime

from django.utils import timezone

# The cadence enum (instant|digest) is aliased to avoid colliding with the
# WatchActionDelivery MODEL, which this module is centered on.
from openmagpie_schema.watch_enums import WatchActionDelivery as DeliveryCadence
from openmagpie_schema.watch_enums import WatchActionDeliveryState, WatchActionRunState
from watches.actions.protocol import DeliveryCall
from watches.models import WatchActionDelivery

_SUCCEEDED = WatchActionDeliveryState.SUCCEEDED.value

# A run outcome maps to the delivery state of the call that produced it. Only
# the three terminal delivery outcomes occur (a config-invalid run makes no
# call, so it never reaches here) ; anything else is treated as ERRORED.
_OUTCOME_TO_DELIVERY = {
    WatchActionRunState.SUCCEEDED: WatchActionDeliveryState.SUCCEEDED,
    WatchActionRunState.ERRORED: WatchActionDeliveryState.ERRORED,
    WatchActionRunState.FAILED: WatchActionDeliveryState.FAILED,
}


class WatchActionDeliveryService:
    """Account-scoped delivery-log surface."""

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("WatchActionDeliveryService requires account_id")
        self.account_id = account_id

    def record(
        self,
        *,
        watch_id: str,
        action_id: str,
        delivery: DeliveryCadence,
        call: DeliveryCall,
        outcome_state: WatchActionRunState,
        error: str,
        attempt: int,
        now: datetime | None = None,
    ) -> WatchActionDelivery:
        """Persist one HTTP attempt as a terminal WatchActionDelivery row (the
        call already happened, so it lands terminal in one write)."""
        ts = now or timezone.now()
        state = _OUTCOME_TO_DELIVERY.get(outcome_state, WatchActionDeliveryState.ERRORED)
        return WatchActionDelivery.objects.create(
            account_id=self.account_id,
            watch_id=watch_id,
            action_id=action_id,
            delivery=delivery.value,
            method=call.method,
            request_key=call.request_key,
            target_host=call.target_host,
            state=state.value,
            http_status=call.http_status,
            item_count=call.item_count,
            attempt=attempt,
            request_payload=call.request_payload,
            error="" if state == WatchActionDeliveryState.SUCCEEDED else error,
            started_at=ts,
            completed_at=ts,
        )

    def find_succeeded(self, *, action_id: str, request_key: str) -> WatchActionDelivery | None:
        """The most recent SUCCEEDED delivery for this action + request_key, or
        None. The flush calls it before a digest POST: a hit means this exact
        batch already landed (a crash-after-POST replay), so skip the re-send.
        A blank key (never expected for a real batch) never matches."""
        if not request_key:
            return None
        return (
            WatchActionDelivery.objects.filter(
                account_id=self.account_id, action_id=action_id, request_key=request_key, state=_SUCCEEDED
            )
            .order_by("-id")
            .first()
        )

    def list_for_action(
        self,
        action_id: str,
        /,
        *,
        after: str | None = None,
        limit: int = 50,
        state: str | None = None,
    ) -> builtins.list[WatchActionDelivery]:
        """This account's deliveries for one action, newest-first (ULID pk).
        Cursor-paginated for the audit CLI ; `state` filters by delivery state."""
        qs = WatchActionDelivery.objects.filter(account_id=self.account_id, action_id=action_id)
        if state:
            qs = qs.filter(state=state)
        if after:
            qs = qs.filter(id__lt=after)
        return builtins.list(qs.order_by("-id")[:limit])

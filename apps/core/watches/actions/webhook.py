"""WebhookAction: deliver one feed item to an HTTP endpoint (kind=`webhook`).

POSTs the item (optionally field-filtered) to the configured URL. A 2xx
SUCCEEDS the run ; a transient failure (5xx, timeout, connect error)
propagates so the drain marks it retryable-FAILED ; a permanent one (a
blocked destination, a 3xx redirect, a 4xx client error) ERRORS the run.
Redirects are NOT followed (a redirect Location is an unvetted SSRF hop).
Ports the v1 webhook notifier into the per-item v2 action interface
(batching returns with digest delivery).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from django.conf import settings

from common.ssrf import destination_block_reason
from openmagpie_schema.watch_actions import WebhookConfig, WebhookResult
from openmagpie_schema.watch_enums import WatchActionKind, WatchActionRunState
from watches import run_messages
from watches.models import WatchAction

from ._config import load_typed
from .protocol import ActionOutcome

logger = logging.getLogger("watches")

# 4xx statuses that ARE retryable despite being client errors: request
# timeout and rate-limit. Every other 4xx is a permanent misconfiguration
# (bad URL / auth / payload) the receiver won't accept on retry.
_RETRYABLE_4XX = frozenset({408, 429})


class WebhookAction:
    """POSTs items to a URL ; gates the run on the HTTP response. Instant
    delivery POSTs one item (`run`) ; digest POSTs a batch (`run_batch`)."""

    kind = WatchActionKind.WEBHOOK.value

    def run(self, action: WatchAction, *, item_data: dict) -> ActionOutcome:
        config = load_typed(action, WebhookConfig, log_label="webhook")
        if config is None:
            return ActionOutcome(state=WatchActionRunState.ERRORED, error=run_messages.CONFIG_INVALID)
        payload = {"action_id": str(action.id), "item": _filtered(item_data, config.include_fields)}
        return self._deliver(action, config, payload=payload, idempotency_key=_item_key(item_data))

    def run_batch(self, action: WatchAction, *, items: list[dict]) -> ActionOutcome:
        config = load_typed(action, WebhookConfig, log_label="webhook")
        if config is None:
            return ActionOutcome(state=WatchActionRunState.ERRORED, error=run_messages.CONFIG_INVALID)
        # Each batch item carries its own identity `key` (source:external_id)
        # in the body, so the receiver dedups PER ITEM — a digest retry
        # re-gathers all still-pending runs and may mix in new arrivals, so a
        # batch-level key would rarely match. `key` survives include_fields
        # (which can strip identity from `item`). No batch Idempotency-Key
        # header: per-item is the robust contract.
        payload = {
            "action_id": str(action.id),
            "items": [{"key": _item_key(i), "item": _filtered(i, config.include_fields)} for i in items],
        }
        return self._deliver(action, config, payload=payload, idempotency_key=None)

    def _deliver(
        self, action: WatchAction, config: WebhookConfig, *, payload: dict, idempotency_key: str | None
    ) -> ActionOutcome:
        # Send-time SSRF re-check (resolve_dns=True): the write-time policy
        # only caught IP literals ; a hostname resolving to an internal
        # address is caught here. A blocked destination is permanent -> ERRORED.
        reason = destination_block_reason(
            config.url,
            require_https=settings.WEBHOOK_REQUIRE_HTTPS,
            block_private_ips=settings.WEBHOOK_BLOCK_PRIVATE_IPS,
            resolve_dns=True,
        )
        if reason:
            logger.warning("webhook: blocked destination for action=%s: %s", action.id, reason)
            return ActionOutcome(state=WatchActionRunState.ERRORED, error=run_messages.WEBHOOK_BLOCKED)

        headers = dict(config.headers)
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = httpx.post(config.url, json=payload, headers=headers, timeout=settings.WEBHOOK_TIMEOUT_SECONDS)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # follow_redirects stays False (a redirect Location is an unvetted
            # SSRF hop), so raise_for_status raises on 3xx too. A redirect is a
            # permanent misconfig (endpoint moved / wrong URL) — retrying the
            # same URL just re-redirects — so ERROR it like a permanent 4xx
            # instead of burning the retry budget.
            status = exc.response.status_code
            if 300 <= status < 400:
                logger.warning("webhook: action=%s redirect %s (not followed)", action.id, status)
                return ActionOutcome(
                    state=WatchActionRunState.ERRORED,
                    result=WebhookResult(http_status=status).model_dump(mode="json"),
                    error=run_messages.WEBHOOK_REDIRECT,
                )
            if 400 <= status < 500 and status not in _RETRYABLE_4XX:
                # Permanent client error (bad url / auth / payload): no retry.
                logger.warning("webhook: action=%s permanent %s", action.id, status)
                return ActionOutcome(
                    state=WatchActionRunState.ERRORED,
                    result=WebhookResult(http_status=status).model_dump(mode="json"),
                    error=run_messages.WEBHOOK_REJECTED,
                )
            # Transient (5xx / 408 / 429): raise a URL-free error so the raw
            # httpx str (which carries the url, a secret carrier) never reaches
            # the drain's log. `from None` drops the chained exception too.
            raise RuntimeError(f"webhook transient status {status}") from None
        except httpx.HTTPError as exc:
            # Connect / timeout / etc: transient, and also kept URL-free.
            raise RuntimeError(f"webhook transient {type(exc).__name__}") from None

        return ActionOutcome(
            state=WatchActionRunState.SUCCEEDED,
            result=WebhookResult(http_status=response.status_code).model_dump(mode="json"),
        )


def _item_key(item_data: dict) -> str:
    """An item's stable identity for the Idempotency-Key (source:external_id)."""
    return f"{item_data.get('source', '')}:{item_data.get('external_id', '')}"


def _filtered(item_data: dict, include_fields: list[str]) -> dict[str, Any]:
    """The item dict, narrowed to `include_fields` (empty = send all).
    Unknown field names are silently skipped (the whitelist is advisory)."""
    if not include_fields:
        return item_data
    return {k: item_data[k] for k in include_fields if k in item_data}

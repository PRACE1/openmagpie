"""WebhookAction: deliver one feed item to an HTTP endpoint (kind=`webhook`).

POSTs the item (optionally field-filtered) to the configured URL. A 2xx
SUCCEEDS the run ; a transient failure (5xx, timeout, connect error)
propagates so the drain marks it retryable-FAILED ; a permanent one (a
blocked destination, a 4xx client error) ERRORS the run. Ports the v1
webhook notifier into the per-item v2 action interface (batching returns
with digest delivery).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from django.conf import settings
from pydantic import ValidationError

from common.ssrf import destination_block_reason
from openmagpie_schema.watch_actions import WebhookConfig, WebhookResult
from openmagpie_schema.watch_enums import WatchActionKind, WatchActionRunState
from watches import run_messages
from watches.models import WatchAction
from watches.registry import load_config

from .protocol import ActionOutcome

logger = logging.getLogger("watches")

# 4xx statuses that ARE retryable despite being client errors: request
# timeout and rate-limit. Every other 4xx is a permanent misconfiguration
# (bad URL / auth / payload) the receiver won't accept on retry.
_RETRYABLE_4XX = frozenset({408, 429})


class WebhookAction:
    """POSTs one item to a URL ; gates the run on the HTTP response."""

    kind = WatchActionKind.WEBHOOK.value

    def run(self, action: WatchAction, *, item_data: dict) -> ActionOutcome:
        try:
            config = load_config(action)
        except ValidationError as exc:
            logger.exception("webhook: invalid config for action=%s: %s", action.id, exc)
            return ActionOutcome(state=WatchActionRunState.ERRORED, error=run_messages.CONFIG_INVALID)
        assert isinstance(config, WebhookConfig)  # registry guarantees by kind

        # Send-time SSRF re-check (resolve_dns=True): the write-time policy
        # only caught IP literals, so a hostname that resolves to an
        # internal address is caught here. A blocked destination is a
        # permanent config defect -> ERRORED, not a retryable FAILED.
        reason = destination_block_reason(
            config.url,
            require_https=settings.WEBHOOK_REQUIRE_HTTPS,
            block_private_ips=settings.WEBHOOK_BLOCK_PRIVATE_IPS,
            resolve_dns=True,
        )
        if reason:
            logger.warning("webhook: blocked destination for action=%s: %s", action.id, reason)
            return ActionOutcome(state=WatchActionRunState.ERRORED, error=run_messages.WEBHOOK_BLOCKED)

        payload = {"action_id": str(action.id), "item": _filtered(item_data, config.include_fields)}
        # Stable per-item idempotency key (NOT per-attempt): a retry re-POSTs
        # the same key, so a receiver can dedupe at-least-once delivery.
        headers = {
            **config.headers,
            "Idempotency-Key": f"{item_data.get('source', '')}:{item_data.get('external_id', '')}",
        }
        try:
            response = httpx.post(config.url, json=payload, headers=headers, timeout=settings.WEBHOOK_TIMEOUT_SECONDS)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
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


def _filtered(item_data: dict, include_fields: list[str]) -> dict[str, Any]:
    """The item dict, narrowed to `include_fields` (empty = send all).
    Unknown field names are silently skipped (the whitelist is advisory)."""
    if not include_fields:
        return item_data
    return {k: item_data[k] for k in include_fields if k in item_data}

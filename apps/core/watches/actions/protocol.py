"""The Action interface: what it means to RUN one WatchAction against a
FeedItem.

Distinct from the CONFIG layer (`watches.registry`, kind -> Pydantic
config class, validation). This is the EXECUTION layer: kind -> runnable
impl. A `WatchActionRun` in the drain is dispatched to the `Action` for
its `kind`, which does the work (judge / POST / log) and returns an
`ActionOutcome` saying how the run + the chain should proceed.

Module per capability (not a catch-all base): `protocol.py` (this),
`registry.py` (kind -> impl), `semantic_filter.py` (the first impl). The
`Action` interface is a NEUTRAL base shared by every kind ; a filter is
not the parent of a webhook.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from openmagpie_schema.watch_enums import WatchActionDelivery, WatchActionRunState
from watches.models import WatchAction


@dataclass(frozen=True)
class ActionOutcome:
    """How a single action run resolved. The drain persists `state` +
    `result` onto the WatchActionRun and advances the chain IFF
    `state == SUCCEEDED`.

    - `state`: the terminal run state an impl returns ; SUCCEEDED
      (advance the chain), GATED (clean stop, e.g. a filter pass=false),
      ERRORED (a permanent defect the impl detected, e.g. unhydrateable
      item / invalid config / unknown engine / blocked destination), or
      FAILED (a TRANSIENT failure the impl already classified, e.g. a 5xx /
      connect error from a delivery; retryable). Filters never return FAILED
      (the drain sets it on an UNEXPECTED raise) ; delivery impls DO, so the
      failed attempt can still be logged with its DeliveryCall. SKIPPED is
      reserved (deliberate non-run) and unused today.
    - `result`: the kind-specific result blob (validated per kind), stored
      on the run for the audit log. A semantic filter writes
      `SemanticFilterResult`.
    - `error`: operator-facing note carried on any non-clean terminal
      state (ERRORED today ; a future SKIPPED). Empty for SUCCEEDED / GATED.
      Sanitized — the raw cause goes to the logs, see `watches.run_messages`.
    """

    state: WatchActionRunState
    result: dict = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class DeliveryItem:
    """One enriched item handed to a delivery action: the FeedItem's stored
    `data` dump, its stable `key` (source:external_id), the originating source
    (`source_label` / `source_kind` from the FeedItem row, e.g. `r/ClaudeAI` /
    `reddit_subreddit`), and the upstream semantic-filter `score` (None when no
    filter ran ahead of this delivery). NOT the wire body: the action narrows
    `data` by `include_fields` into the WebhookItem it sends."""

    data: dict
    key: str
    source_label: str
    source_kind: str
    score: float | None = None


@dataclass(frozen=True)
class DeliveryContext:
    """Call-level context shared by every item in one delivery: which watch
    (id + name, so a receiver can label the listener), the cadence, and the
    digest window bounds (both None for instant)."""

    watch_id: str
    watch_name: str
    delivery: WatchActionDelivery
    window_since: datetime | None = None
    window_until: datetime | None = None


@dataclass(frozen=True)
class DeliveryCall:
    """The record of ONE outbound HTTP attempt, returned by a delivery action
    so the operations layer can persist a WatchActionDelivery row. Present for
    every webhook attempt (success, permanent error, transient failure) ; None
    for the local log action (no HTTP call). `request_payload` is the exact
    body sent (no headers)."""

    request_key: str
    target_host: str
    method: str
    http_status: int | None
    item_count: int
    request_payload: dict


@dataclass(frozen=True)
class DeliveryResult:
    """What `deliver()` returns: the run `outcome` (persisted on every run in
    the batch + drives chain advance / retry) and the optional `call` record
    (persisted as a WatchActionDelivery when present)."""

    outcome: ActionOutcome
    call: DeliveryCall | None = None


class Action(Protocol):
    """The NEUTRAL base every action kind satisfies: it just declares its
    `kind` (matches a `WatchActionKind` value). A filter is NOT the parent of
    a webhook ; `FilterAction` and `DeliveryAction` are siblings that each
    extend this base. The registry is keyed on it (`dict[str, Action]`) ; the
    drain narrows to the concrete protocol to dispatch."""

    kind: str


@runtime_checkable
class FilterAction(Action, Protocol):
    """A FILTER action (e.g. semantic_filter): judges one item and gates the
    chain. `run` receives the `WatchAction` row (carries the config blob) and
    the FeedItem's stored `data` dump (the connector SourcePayload it judges).
    Returns an `ActionOutcome` ; raises only on UNEXPECTED failure (the drain
    maps that to a retryable FAILED)."""

    def run(self, action: WatchAction, *, item_data: dict) -> ActionOutcome: ...


@runtime_checkable
class DeliveryAction(Action, Protocol):
    """A DELIVERY action (webhook, log): emits one or more enriched items to a
    sink and returns a `DeliveryResult`. Instant is a one-item batch ; digest
    is N items (one window). Unifies the instant + digest paths on one method
    so the wire payload has ONE shape. `deliver` returns FAILED (not raises)
    on a TRANSIENT failure so the failed attempt is still recorded ; it raises
    only on an UNEXPECTED bug (the operations layer maps that to retryable
    FAILED). A filter is never digested, so it stays a plain `FilterAction`."""

    def deliver(
        self, action: WatchAction, *, items: list[DeliveryItem], context: DeliveryContext
    ) -> DeliveryResult: ...


def item_key(data: dict) -> str:
    """An item's stable identity (source:external_id), the per-item dedup key
    carried in the body and the instant Idempotency-Key header."""
    return f"{data.get('source', '')}:{data.get('external_id', '')}"


def delivery_request_key(items: list[DeliveryItem]) -> str:
    """The dedup identity of one delivery ATTEMPT: a single item's key for
    instant, a stable hash of the sorted keys for a digest batch. Deterministic
    so a crash-after-POST replay (same items) computes the same key and the
    flush can short-circuit on a prior SUCCEEDED delivery."""
    keys = sorted(i.key for i in items)
    if len(keys) == 1:
        return keys[0]
    digest = hashlib.sha256("\n".join(keys).encode()).hexdigest()
    return f"digest:{digest}"

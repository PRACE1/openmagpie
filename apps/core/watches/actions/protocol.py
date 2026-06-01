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

from dataclasses import dataclass, field
from typing import Protocol

from openmagpie_schema.watch_enums import WatchActionRunState
from watches.models import WatchAction


@dataclass(frozen=True)
class ActionOutcome:
    """How a single action run resolved. The drain persists `state` +
    `result` onto the WatchActionRun and advances the chain IFF
    `state == SUCCEEDED`.

    - `state`: the terminal run state an impl returns ; one of SUCCEEDED
      (advance the chain), GATED (clean stop, e.g. a filter pass=false), or
      ERRORED (a permanent defect the impl detected, e.g. unhydrateable
      item / invalid config / unknown engine). The drain itself sets FAILED
      on an UNEXPECTED raise (retryable) ; impls don't return FAILED.
      SKIPPED is reserved (deliberate non-run) and unused today.
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


class Action(Protocol):
    """A runnable action kind. Each impl declares its `kind` (matches a
    `WatchActionKind` value) and runs one execution against one feed item.

    `run` receives the typed-but-opaque-to-the-drain pieces: the
    `WatchAction` row (carries the config blob) and the FeedItem's stored
    `data` dump (the connector SourcePayload, for filter kinds that judge
    the item). Delivery kinds (webhook/log, later) ignore the payload
    shape they don't need. Returns an `ActionOutcome` ; raises only on
    UNEXPECTED failure (the drain maps that to a retryable FAILED).
    """

    kind: str

    def run(self, action: WatchAction, *, item_data: dict) -> ActionOutcome: ...

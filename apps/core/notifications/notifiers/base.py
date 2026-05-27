"""Notifier contract, same shape pattern as connectors and engines.

`HitBatch` is what every notifier receives: a Listener + a list of `Hit`s
(each pairs an Observation with the engine's relevance score that judged
it). Instant mode delivers a batch of 1 per hit. Digest mode delivers a
batch of N covering all pending hits for the listener since `last_digest_at`.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from events.observations import Observation
from listeners.models import Listener


@dataclass(frozen=True)
class Hit:
    """One delivered hit: the Observation + the engine score that judged it.

    `relevance_score` is surfaced separately from the Observation dump so
    it doesn't collide with source-side `score` fields (e.g. Reddit's
    upvote count). Webhook / log payloads expose it as `relevance_score`.
    None for non-hit event kinds (future: feed_change, delivery).
    """

    obs: Observation
    relevance_score: float | None = None


@dataclass(frozen=True)
class HitBatch:
    """One or more hits to deliver. Instant: len(hits) == 1. Digest: len(hits) == N."""

    listener: Listener
    hits: list[Hit]
    period_start: datetime | None  # None for instant
    period_end: datetime


@dataclass(frozen=True)
class NotificationResult:
    notifier_kind: str
    delivered: bool
    latency_ms: int
    error: str = ""


class Notifier(Protocol):
    """A pluggable side effect. Each impl declares its `kind`, a side-
    effectful `deliver`, a pure `render`, and a human-readable target.

    `render` returns whatever the notifier WOULD emit for the batch
    without firing it (webhook: the JSON dict POSTed; log: the text
    written to stdout). This is what payload-sample previews — it's the
    real per-notifier code path, just with the ship-step skipped.
    `deliver` implementations should call `self.render(batch, spec)` so
    the preview can't drift from production.
    """

    kind: str

    def render(self, batch: HitBatch, spec: Any) -> Any:
        """Build the per-notifier output for the batch WITHOUT side
        effects. Return type is notifier-defined: webhook returns a
        dict (the POST body), log returns a str (the stdout block).
        New notifiers pick the shape that matches their wire format."""
        ...

    def target_for(self, spec: Any) -> str | None:
        """Human-readable destination for the preview header. Webhook
        returns its URL; log returns None (writes to a fixed sink)."""
        ...

    def deliver(self, batch: HitBatch, spec: Any) -> NotificationResult:
        """Send the batch via this notifier. `spec` is the kind-specific Pydantic config."""
        ...

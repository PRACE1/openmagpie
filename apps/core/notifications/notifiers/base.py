"""Notifier contract, same shape pattern as connectors and engines.

`HitBatch` is what every notifier receives: a Listener + a list of Observations.
Instant mode delivers a batch of 1 per hit. Digest mode delivers batch of N
covering all pending hits for the listener since `last_digest_at`.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from events.observations import Observation
from listeners.models import Listener


@dataclass(frozen=True)
class HitBatch:
    """One or more hits to deliver. Instant: len(hits) == 1. Digest: len(hits) == N."""

    listener: Listener
    hits: list[Observation]
    period_start: datetime | None  # None for instant
    period_end: datetime


@dataclass(frozen=True)
class NotificationResult:
    notifier_kind: str
    delivered: bool
    latency_ms: int
    error: str = ""


class Notifier(Protocol):
    """A pluggable side effect. Each impl declares its `kind` and a deliver method."""

    kind: str

    def deliver(self, batch: HitBatch, spec: Any) -> NotificationResult:
        """Send the batch via this notifier. `spec` is the kind-specific Pydantic config."""
        ...

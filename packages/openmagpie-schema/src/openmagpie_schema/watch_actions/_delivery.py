"""Shared base for the DELIVERY kinds (webhook, log): cadence + digest
window length. The FILTER kind (semantic_filter) doesn't deliver, so it
doesn't carry these."""

from __future__ import annotations

from pydantic import Field, model_validator

from openmagpie_schema.watch_enums import WatchActionDelivery

from .base import WatchActionConfigBase


class DeliveryConfigBase(WatchActionConfigBase):
    """`delivery` is the cadence: INSTANT fires per item, DIGEST batches a
    fixed window (anchored at the first item) into one emission.
    `digest_interval_seconds` is that window's length.

    Two-layer validation: the PRESENCE rule (DIGEST requires a positive
    interval) is a pure structural invariant enforced here, so the CLI gets
    local feedback without a server round-trip. The MAGNITUDE bound (the
    min/max seconds) is settings-coupled and lives in server policy
    (`watches.policy._enforce_digest_interval`)."""

    delivery: WatchActionDelivery = WatchActionDelivery.INSTANT
    digest_interval_seconds: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _digest_requires_interval(self) -> DeliveryConfigBase:
        # Asymmetric on purpose: DIGEST needs a positive interval, but INSTANT
        # with a nonzero interval is TOLERATED (the field is ignored when
        # instant), so flipping digest->instant doesn't force clearing it.
        if self.delivery == WatchActionDelivery.DIGEST and self.digest_interval_seconds <= 0:
            raise ValueError("digest_interval_seconds must be > 0 when delivery is digest")
        return self

    def is_digest(self) -> bool:
        return self.delivery == WatchActionDelivery.DIGEST

    def delivery_label(self) -> str:
        """Human cadence for the CLI summary."""
        return f"digest/{self.digest_interval_seconds}s" if self.is_digest() else "instant"

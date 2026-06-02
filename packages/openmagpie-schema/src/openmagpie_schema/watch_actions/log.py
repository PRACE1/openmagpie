"""The log kind: server-log delivery config + result. No secrets."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from openmagpie_schema.watch_enums import WatchActionDelivery

from .base import WatchActionConfigBase, WatchActionConfigSummary


class LogConfig(WatchActionConfigBase):
    """Config for a WatchAction with kind == 'log'.

    Writes the item to the server log under `prefix`. `include_fields`
    whitelists item fields (empty = all). `delivery` cadence; INSTANT only
    today (policy rejects DIGEST). No secrets, so the dump is plain and an
    edit replaces wholesale."""

    CONFIG_KIND: ClassVar[str] = "log"

    prefix: str = "[watch]"
    include_fields: list[str] = Field(default_factory=list)
    delivery: WatchActionDelivery = WatchActionDelivery.INSTANT

    model_config = {"extra": "ignore"}

    def redacted_dump(self) -> dict[str, Any]:
        """No secrets in a log action, so a plain dump is safe."""
        return self.model_dump(mode="json")

    def summary(self) -> WatchActionConfigSummary:
        return WatchActionConfigSummary(detail=f"log {self.prefix} ({self.delivery.value})")

    def merge_preserving(self, prior: WatchActionConfigBase) -> LogConfig:
        """Nothing to carry forward: no secrets, the submitted config wins."""
        return self


class LogResult(BaseModel):
    """Result a log run writes: the line it emitted (kept for the audit)."""

    rendered: str = ""

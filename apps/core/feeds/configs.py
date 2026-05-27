"""Re-export of the shared, pure Feed config models.

The models live ONCE in the `openmagpie-schema` workspace package
(imported by both core and the magpie CLI). This module is the stable
in-core import path (`from feeds.configs import ...`).

Django/settings-coupled *policy* (no future watermark, retention
bounds) is NOT here - it lives in `feeds.policy` and runs at the
validation seam. Mirrors `listeners.configs`.
"""

from openmagpie_schema.configs import StreamWatch
from openmagpie_schema.feed import (
    CuratedFeedConfig,
    FeedConfig,
    FeedConfigSummary,
)

__all__ = [
    "CuratedFeedConfig",
    "FeedConfig",
    "FeedConfigSummary",
    "StreamWatch",
]

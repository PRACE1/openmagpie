"""Base Observation, the typed in-memory event the engine consumes.

Concrete subclasses live next to their connector (e.g. `sources/connectors/reddit/observations.py`).
On a hit, the full `observation.model_dump()` is what gets stored in `Event.data`.
"""

from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel


class Observation(BaseModel):
    """Base typed observation. Subclasses canonicalize source-specific data."""

    EVENT_KIND: ClassVar[str]

    external_id: str
    kind: str  # equals EVENT_KIND of the concrete subclass
    occurred_at: datetime
    source: str  # connector kind, e.g. "reddit_subreddit"

    # Canonical engine input fields (subclasses map source-native fields → these)
    title: str = ""
    content: str = ""
    url: str = ""
    parent_external_id: str = ""

    model_config = {"frozen": True}

    def stream_slug(self) -> str | None:
        """The stream-within-source identifier for this observation.

        Subclasses override when their source has a meaningful per-stream
        identifier (Reddit subreddit, GitHub `owner/repo`, Slack channel, ...).
        Used by notifier batching to group hits by stream.
        """
        return None

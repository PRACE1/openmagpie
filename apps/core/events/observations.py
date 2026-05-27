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

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        """No subclass may declare a field named `relevance_score`.

        Notifier batching layers the engine's `relevance_score` onto the
        observation dump AFTER `model_dump()`, so a same-named source
        field would be silently clobbered. Runs in
        `__pydantic_init_subclass__` (post-field-collection) so this
        catches subclass-declared fields, which `__init_subclass__` runs
        too early to see.
        """
        super().__pydantic_init_subclass__(**kwargs)
        if "relevance_score" in cls.model_fields:
            raise TypeError(
                f"{cls.__name__} declares a field named 'relevance_score', which collides with "
                "the engine's relevance_score that notifier batching layers onto the payload. "
                "Rename the source field."
            )

    def stream_slug(self) -> str | None:
        """The stream-within-source identifier for this observation.

        Subclasses override when their source has a meaningful per-stream
        identifier (Reddit subreddit, GitHub `owner/repo`, Slack channel, ...).
        Used by notifier batching to group hits by stream.
        """
        return None

    @classmethod
    def sample(cls, variant: int = 0) -> "Observation":
        """Return a synthetic instance for payload previews
        (`magpie listener payload-sample`).

        `variant` lets digest-mode previews show N distinct hits in the
        same stream group — each connector decides how to vary
        observably (different external_id, url, title). Subclasses MUST
        produce a distinct observation for each variant index a caller
        passes; the payload-sample view today asks for variants 0 and 1.

        Each connector's Observation must implement this so operators
        wiring up a webhook can see the exact shape their receiver will
        get BEFORE any real hits land. No safe default: a passthrough
        would surface as a Pydantic validation error on the required
        fields a subclass adds.
        """
        raise NotImplementedError(f"{cls.__name__} must implement sample() for payload-sample support")

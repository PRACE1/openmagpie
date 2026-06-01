"""Pure typed source specs, keyed by kind.

SHARED, zero-Django source of truth (imported by core *and* the magpie
CLI). Carries only *shape* + pure transforms. The Django/settings-coupled
*policy* (SSRF / https rules, default engine kind, ...) is NOT here ; it
lives in `core` and runs at the server's validation seam. Splitting shape
from policy is what lets this module be a dependency-free shared package.
"""

from typing import Annotated, ClassVar, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

# ── Source specs (discriminated union over kind) ──────────────────────────


class RedditSubredditSourceSpec(BaseModel):
    """Identity of one subreddit source. Bound to RedditSubRedditConnector."""

    SOURCE_KIND: ClassVar[str] = "reddit_subreddit"

    kind: Literal["reddit_subreddit"] = "reddit_subreddit"
    subreddit: str

    def display(self) -> str:
        return f"r/{self.subreddit}"


class RssSourceSpec(BaseModel):
    """Identity of one RSS/Atom source by URL. Bound to a generic RSS connector."""

    SOURCE_KIND: ClassVar[str] = "rss"

    kind: Literal["rss"] = "rss"
    url: str
    name: str = ""

    @field_validator("url")
    @classmethod
    def _validate_url_structural(cls, value: str) -> str:
        """Structural check only (http/https scheme + host present).
        Connector-side reachability / feed-format validation runs at poll
        time. An empty URL slips through plain `str` typing and silently
        produces a blank source_label downstream; reject it here."""
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"}:
            raise ValueError(f"rss URL scheme must be http or https, got {parts.scheme!r}")
        if not parts.netloc:
            raise ValueError(f"rss URL missing host: {value!r}")
        return value

    def display(self) -> str:
        return self.name or self.url


SourceSpec = Annotated[
    RedditSubredditSourceSpec | RssSourceSpec,
    Field(discriminator="kind"),
]

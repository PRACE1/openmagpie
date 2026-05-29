"""Observation produced by the generic RSS/Atom connector.

`title` / `url` / `external_id` / `content` / `author` are mapped from
feedparser's normalized entry dict via a precedence chain per field,
overridable per source via `field_map`. See `RssEntryObservation.from_feedparser_entry`."""

import html
import re
import time
from datetime import UTC, datetime
from typing import Any, ClassVar

from events.observations import Observation
from openmagpie_schema.configs import RssSourceSpec

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Default precedence chains. The `field_map` overrides the first entry,
# subsequent entries are fallbacks. A connector that drops `published`
# from the chain and gets nothing skips the entry rather than fabricate
# a timestamp (the poll watermark must reflect real publish time).
DEFAULT_FIELD_PRECEDENCE: dict[str, tuple[str, ...]] = {
    "external_id": ("id", "guid", "link"),
    "content": ("content", "summary", "description"),
    "author": ("author", "dc_creator"),
    "published": ("published_parsed", "updated_parsed"),
}


def _html_to_text(value: str) -> str:
    """Strip HTML tags + collapse whitespace + unescape entities. RSS
    publishers vary widely on whether the body is plain text, escaped
    HTML, or CDATA-wrapped HTML; normalize so the engine never sees raw
    markup."""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value))).strip()


def _resolve(entry: Any, canonical: str, field_map: dict[str, str]) -> Any:
    """Walk the (override -> default precedence) chain for one canonical
    field and return the first truthy value found, or None.

    `entry` is a feedparser FeedParserDict ; attribute access returns
    "" for missing keys, so a falsy check skips empty strings, missing
    dict keys, AND missing struct_time fields uniformly."""
    chain: list[str] = []
    override = field_map.get(canonical)
    if override:
        chain.append(override)
    chain.extend(k for k in DEFAULT_FIELD_PRECEDENCE.get(canonical, ()) if k != override)
    for key in chain:
        value = entry.get(key)
        if value:
            return value
    return None


def _coerce_content(value: Any) -> str:
    """Feedparser's `content` key is a list of FeedParserDicts (Atom can
    carry multiple representations); `summary` / `description` are
    strings. Pick the first list element if a list, then strip HTML.
    Anything else gets `str()` + strip."""
    if isinstance(value, list) and value:
        value = value[0].get("value", "") if isinstance(value[0], dict) else value[0]
    return _html_to_text(str(value))


def _coerce_published(value: Any) -> datetime | None:
    """feedparser parses RFC-822/ISO timestamps into `time.struct_time`
    in UTC, exposed as the `*_parsed` keys. Convert to aware datetime."""
    if isinstance(value, time.struct_time):
        return datetime.fromtimestamp(time.mktime(value), tz=UTC)
    return None


class RssEntryObservation(Observation):
    """One entry from an RSS / Atom feed."""

    EVENT_KIND: ClassVar[str] = "rss_entry"

    author: str = ""
    feed_url: str = ""
    categories: list[str] = []

    model_config = {"frozen": True, "extra": "ignore"}

    def source_slug(self) -> str:
        return self.feed_url

    @classmethod
    def sample(cls, variant: int = 0) -> "RssEntryObservation":
        n = variant + 1
        return cls(
            external_id=f"https://example.com/news/article-{n}",
            kind=cls.EVENT_KIND,
            occurred_at=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
            source=RssSourceSpec.SOURCE_KIND,
            title=f"Example RSS headline {n}",
            content="The first paragraph of the article body, after the connector strips HTML.",
            url=f"https://example.com/news/article-{n}",
            parent_external_id="",
            author="Staff Writer",
            feed_url="https://example.com/rss.xml",
            categories=["news"],
        )

    @classmethod
    def from_feedparser_entry(
        cls,
        entry: Any,
        spec: RssSourceSpec,
        field_map: dict[str, str],
    ) -> "RssEntryObservation | None":
        """Project one feedparser entry to an `RssEntryObservation`.

        Returns None if no resolvable `published` timestamp exists (the
        watermark must be real; fabricating one would either gate every
        future poll on the wrong timestamp or, if we used `now()`, mark
        a backfilled entry as just-published). The polling op drops
        None-yielded entries via the `_ObservedSource` walk."""
        published = _coerce_published(_resolve(entry, "published", field_map))
        if published is None:
            return None

        external_id_raw = _resolve(entry, "external_id", field_map)
        external_id = str(external_id_raw or "").strip()
        if not external_id:
            return None  # FeedItem dedupe key must be non-empty

        content = _coerce_content(_resolve(entry, "content", field_map) or "")
        author_raw = _resolve(entry, "author", field_map)
        author = str(author_raw or "").strip()

        # `tags` is feedparser's normalized form of `<category>` / Atom
        # `<category>` ; each item is a FeedParserDict with `term`.
        categories = [t.get("term", "") for t in entry.get("tags", []) if isinstance(t, dict)]

        return cls(
            external_id=external_id,
            kind=cls.EVENT_KIND,
            occurred_at=published,
            source=spec.kind,
            title=str(entry.get("title", "")).strip(),
            content=content,
            url=str(entry.get("link", "")).strip(),
            parent_external_id="",
            author=author,
            feed_url=spec.url,
            categories=[c for c in categories if c],
        )

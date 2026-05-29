"""Generic RSS / Atom connector.

One GET per cycle (RSS feeds don't paginate), parsed by `feedparser`,
yielding `RssEntryObservation` for entries with `occurred_at > since`.
Unlike Reddit's connector this works against any feed URL ; the per-
publisher quirks (which key holds the body, which holds the author)
are absorbed by the `field_map` override threaded through from the
Source row + feed default."""

import logging
from collections.abc import Iterator
from datetime import datetime

import feedparser
import httpx

from events.observations import Observation
from events.registry import register
from openmagpie_schema.configs import RssSourceSpec

from ..base import BaseConnector, ConnectorParseError
from .observations import RssEntryObservation

logger = logging.getLogger("sources")

# Polite default UA ; some publishers 403 the bare `python-httpx/...` UA.
# Identify the project so a publisher can correlate traffic if they look.
RSS_USER_AGENT = "openmagpie-rss/1.0 (+https://github.com/obris-dev/openmagpie)"

# Cap how many bytes we read from a single feed in one cycle. RSS feeds
# are typically <100KB; a >5MB body is either a misconfigured endpoint
# (serving the full archive) or a hostile target. Treat as a parse
# failure rather than chew RAM.
MAX_BODY_BYTES = 5 * 1024 * 1024


class RssConnector(BaseConnector):
    """Polls a single RSS or Atom feed URL.

    Live-mode semantics: every cycle yields entries newer than `since`,
    where `since` is the Source row's `last_event_at`. `feeds.policy`
    stamps `last_event_at = now()` at save time so the first cycle just
    returns whatever's been published since the source was created.

    Backfill is opt-in at source creation via `SourceInput.last_event_at`
    (the operator can pin a past datetime). There's no walk-the-archive
    mode ; RSS feeds typically don't expose history past the latest N
    items anyway, so a true backfill needs a different mechanism
    (sitemap, archive-only feeds, ...) that doesn't belong in this
    connector.

    `field_map` recognised keys: `external_id`, `content`, `author`,
    `published`. Each is the feedparser-entry key to read in place of
    the default precedence chain (see
    `observations.DEFAULT_FIELD_PRECEDENCE`). Unknown keys are silently
    dropped so a future schema extension is forward-compatible."""

    kind = RssSourceSpec.SOURCE_KIND
    observations: list[type[Observation]] = [RssEntryObservation]

    def poll(
        self,
        spec: RssSourceSpec,
        since: datetime | None,
        field_map: dict[str, str] | None = None,
    ) -> Iterator[RssEntryObservation]:
        field_map = field_map or {}

        try:
            response = httpx.get(
                spec.url,
                headers={"User-Agent": RSS_USER_AGENT},
                timeout=15.0,
                follow_redirects=True,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            # Propagated; `_RECOVERABLE_ERRORS` covers it so one bad
            # feed doesn't abort the whole cycle.
            raise

        body = response.content
        if len(body) > MAX_BODY_BYTES:
            raise ConnectorParseError(f"rss feed {spec.url} returned {len(body)} bytes (>{MAX_BODY_BYTES} cap)")

        parsed = feedparser.parse(body)
        # `bozo` flags malformed XML but feedparser still recovers
        # most entries; only fail the cycle if we got zero entries
        # AND a hard parse failure. A bozo=1 with N entries means
        # "some publishers ship invalid XML" - their entries still
        # came through.
        if parsed.bozo and not parsed.entries:
            exc = parsed.get("bozo_exception")
            raise ConnectorParseError(f"rss feed {spec.url} returned an unparseable body: {type(exc).__name__}: {exc}")

        for entry in parsed.entries:
            obs = RssEntryObservation.from_feedparser_entry(entry, spec, field_map)
            if obs is None:
                # Missing published or external_id ; logged once per
                # entry at debug so an operator chasing "why aren't
                # these landing" can flip to DEBUG and see the rows
                # without spamming production logs.
                logger.debug(
                    "rss: skipped entry on %s (missing published or external_id): %r",
                    spec.url,
                    entry.get("title", "<no title>"),
                )
                continue
            if since is not None and obs.occurred_at <= since:
                continue
            yield obs


# Register observations for hydration of Event.data, single source of truth via the class attrs.
register(RssConnector.kind, RssConnector.observations)

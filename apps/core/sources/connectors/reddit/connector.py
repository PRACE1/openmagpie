import logging
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx

from events.observations import Observation
from events.registry import register
from openmagpie_schema.configs import RedditSubredditSourceSpec

from ..base import BaseConnector, ConnectorParseError, read_response_capped
from .observations import NewRedditPostObservation

logger = logging.getLogger("sources")

# Reddit's anonymous `.json` endpoint is gated by a TLS / HTTP-2 fingerprint
# check, only a real browser handshake gets through (cookies, browser-shaped
# UA, Referer, and `Sec-Fetch-*` headers all fail from Python). The `.rss`
# endpoint serves the same /new listing as Atom XML with no fingerprint check,
# so we use it as the anonymous transport. For richer payloads (score,
# comments, upvote_ratio) switch to authenticated PRAW against
# oauth.reddit.com with a registered Reddit app
# (https://www.reddit.com/prefs/apps).
REDDIT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Reddit's anonymous max page size is 100. Cap pagination at MAX_PAGES so a
# Listener that's been silent for weeks doesn't fetch unbounded history on
# wake; with PAGE_SIZE=100, MAX_PAGES=10 covers the latest ~1000 posts.
PAGE_SIZE = 100
MAX_PAGES = 10

# Per-page body cap. Reddit's .rss is typically <100KB ; one corrupted
# / oversize response shouldn't chew RAM. Streamed + capped via
# `read_response_capped` so we never buffer past the cap. Higher than
# the RSS connector's 5MB ceiling because Reddit pages 100 posts each ;
# left generous for any rich Atom payloads.
MAX_BODY_BYTES = 5 * 1024 * 1024


def _entry_published(entry: Any) -> datetime | None:
    """feedparser exposes Atom `<published>` as `published_parsed`
    struct_time ALREADY IN UTC. Read the year/month/day/hour/minute/
    second fields straight into the datetime constructor ;
    `time.mktime` would interpret the struct as local wall-clock and
    shift every timestamp by the host's UTC offset on any non-UTC
    deploy (Reddit's old custom-ET path used `datetime.fromisoformat`,
    which was correct ; this path must preserve that)."""
    parsed = entry.get("published_parsed")
    if isinstance(parsed, time.struct_time):
        return datetime(
            parsed.tm_year,
            parsed.tm_mon,
            parsed.tm_mday,
            parsed.tm_hour,
            parsed.tm_min,
            parsed.tm_sec,
            tzinfo=UTC,
        )
    return None


class RedditSubRedditConnector(BaseConnector):
    """Polls a single subreddit's `/r/<slug>/new/.rss` Atom feed.

    Live-mode semantics: every cycle is "yield posts newer than `since`".
    `since` is the source's `last_event_at`, which feed-config policy
    initializes to wall-clock now at save time (see `feeds/policy.py`), so
    in production `since` is always non-None and the first poll just sees
    the few posts published since the source was created.

    The `since=None` path (no watermark) is a dev / test entry only, and
    walks up to `MAX_PAGES * PAGE_SIZE` posts from the head of /new
    (Reddit caps anonymous /new at ~1000 items total).

    There is no backfill. Future Reddit variants (user feed, search, comments,
    ...) get their own connector class + kind. If one of them grows a real
    "backfill N days" requirement, that's a separate feature with its own
    state machine (cursor + horizon + completion flag), not a cursor smuggled
    in here.
    """

    kind = RedditSubredditSourceSpec.SOURCE_KIND
    observations: list[type[Observation]] = [NewRedditPostObservation]

    # `count` is the universal poll-walk default from BaseConnector: it
    # re-walks the page fetch (~10 GETs to /new.rss) discarding each
    # observation. Reddit has no cheaper exact-count path, so we don't
    # override it.

    def poll(
        self,
        spec: RedditSubredditSourceSpec,
        since: datetime | None,
        field_map: dict[str, str] | None = None,
    ) -> Iterator[NewRedditPostObservation]:
        # Reddit Atom carries fixed, non-overridable fields ; the
        # connector ignores `field_map` (the Connector contract
        # accepts it for the RSS variant + future per-source
        # overrides). Documented as a no-op rather than silently
        # dropped so a future Reddit field-map use case (e.g. body
        # vs title-only) lands here intentionally.
        del field_map
        subreddit = spec.subreddit
        if not subreddit:
            raise ValueError(f"RedditSubredditSourceSpec missing subreddit: {spec}")

        # `/new` is sorted newest -> oldest. Reddit has no server-side `since`
        # filter; the early-return on strict `obs.occurred_at < since` works
        # only because of that ordering, once we see a post strictly older
        # than `since`, every remaining post on this page and every later
        # page is older too. The `after` cursor walks pages newest -> oldest
        # in the same order.
        url = f"https://www.reddit.com/r/{subreddit}/new/.rss"
        after: str | None = None

        # One Client across all pages: shares the connection pool, so
        # `?after=` pagination reuses the keep-alive instead of
        # handshaking per page. Reddit doesn't strictly need the body
        # cap (fixed host, bounded pages), but routing through the
        # shared `read_response_capped` puts the cap policy in one
        # place ; an unexpected oversize 200 from a CDN edge surfaces
        # as a parse error instead of an OOM.
        with httpx.Client(
            timeout=15.0,
            headers={"User-Agent": REDDIT_USER_AGENT},
        ) as client:
            for _ in range(MAX_PAGES):
                params: dict[str, str | int] = {"limit": PAGE_SIZE}
                if after:
                    params["after"] = after

                with client.stream("GET", url, params=params) as response:
                    response.raise_for_status()
                    body = read_response_capped(response, max_bytes=MAX_BODY_BYTES, url_label=url)

                parsed = feedparser.parse(body)
                # Gate on `not version`: real feeds set `version`
                # ('atom10' for Reddit ; HTML pages come back as ''.
                # Reddit's anti-bot rate-limit / login page is a 200 with
                # HTML body ; without this gate it silently reads as "no
                # new posts" and never surfaces the block.
                #
                # Bozo is intentionally NOT a fail trigger. feedparser
                # raises bozo=1 with SAXParseException for non-fatal
                # quirks (undeclared namespace prefix, etc.) AND for hard
                # parse failures ; we can't reliably discriminate without
                # matching exception messages. The trade-off: a truncated
                # body that recovers 0 entries reads as "empty page" and
                # the loop returns ; next poll picks up when Reddit
                # recovers. The Reddit-specific concern (the .rss
                # endpoint being our anon channel) is fully covered by
                # the version gate alone.
                # `getattr(..., "")` because some inputs (empty body,
                # non-XML) cause feedparser to return a FeedParserDict
                # that raises AttributeError on `.version` access
                # instead of returning empty string.
                if not parsed.entries and not getattr(parsed, "version", ""):
                    raise ConnectorParseError(
                        f"reddit /r/{subreddit}/new/.rss returned an unexpected payload "
                        "(no feed format detected; likely the anti-bot HTML page)"
                    )

                if not parsed.entries:
                    return  # empty page, nothing more to consume

                last_atom_id: str | None = None
                for entry in parsed.entries:
                    published = _entry_published(entry)
                    if published is None:
                        # Reddit Atom always carries <published>; a missing one
                        # is a Reddit-side schema change. Skip the row instead
                        # of dropping the whole page (fail loud only on bozo
                        # + zero entries above).
                        continue
                    obs = NewRedditPostObservation.from_feedparser_entry(entry, spec, published)
                    last_atom_id = entry.get("id") or last_atom_id
                    # Strict `<`, not `<=`: two posts can share `<published>`
                    # to the second (batch import; same-second submissions),
                    # and dropping on tie permanently loses the second one
                    # because the watermark already crossed its second.
                    # The downstream `external_id` dedup on FeedItem is
                    # idempotent, so re-yielding the boundary post is
                    # suppressed at the recorder seam. The early-return
                    # remains safe: once we see a post strictly older than
                    # `since`, every remaining post on this and later pages
                    # is older too.
                    if since is not None and obs.occurred_at < since:
                        return
                    yield obs

                # Atom has no Reddit-style `after` cursor in the envelope, but
                # `?after=t3_xxx&limit=N` still works against `.rss`. Use the
                # last entry's thing-id (already `t3_<post-id>`) as the cursor.
                if not last_atom_id:
                    return  # nothing to page from
                after = last_atom_id


# Register observations for hydration of Event.data, single source of truth via the class attrs.
register(RedditSubRedditConnector.kind, RedditSubRedditConnector.observations)

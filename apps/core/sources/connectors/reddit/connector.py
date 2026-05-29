import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import datetime

import httpx
from pydantic import ValidationError

from events.observations import Observation
from events.registry import register
from openmagpie_schema.configs import RedditSubredditSourceSpec

from ..base import BaseConnector, ConnectorParseError
from .observations import NewRedditPostObservation
from .payloads import RedditAtomEntry

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

ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _parse_atom(xml_text: str) -> list[RedditAtomEntry]:
    """Project Reddit's `.rss` Atom XML into our `RedditAtomEntry` list."""
    root = ET.fromstring(xml_text)
    entries: list[RedditAtomEntry] = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        link_el = entry.find(f"{ATOM_NS}link")
        cat_el = entry.find(f"{ATOM_NS}category")
        entries.append(
            RedditAtomEntry(
                atom_id=entry.findtext(f"{ATOM_NS}id", default=""),
                title=entry.findtext(f"{ATOM_NS}title", default=""),
                published=entry.findtext(f"{ATOM_NS}published", default=""),
                content_html=entry.findtext(f"{ATOM_NS}content", default=""),
                link=link_el.get("href", "") if link_el is not None else "",
                author_name=entry.findtext(f"{ATOM_NS}author/{ATOM_NS}name", default=""),
                subreddit=cat_el.get("term", "") if cat_el is not None else "",
            )
        )
    return entries


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
    ) -> Iterator[NewRedditPostObservation]:
        subreddit = spec.subreddit
        if not subreddit:
            raise ValueError(f"RedditSubredditSourceSpec missing subreddit: {spec}")

        # `/new` is sorted newest -> oldest. Reddit has no server-side `since`
        # filter; the early-return on `obs.occurred_at <= since` works only
        # because of that ordering, once we see a post older than `since`,
        # every remaining post on this page and every later page is older too.
        # The `after` cursor walks pages from newest to oldest in the same order.
        url = f"https://www.reddit.com/r/{subreddit}/new/.rss"
        after: str | None = None

        for _ in range(MAX_PAGES):
            params: dict[str, str | int] = {"limit": PAGE_SIZE}
            if after:
                params["after"] = after

            response = httpx.get(
                url,
                params=params,
                headers={"User-Agent": REDDIT_USER_AGENT},
                timeout=15.0,
            )
            response.raise_for_status()
            try:
                entries = _parse_atom(response.text)
            except (ET.ParseError, ValidationError) as exc:
                # 200 with non-XML, a Reddit-side schema change, or a missing
                # required field on an entry should fail this source's poll
                # cycle, not the whole scheduler.
                raise ConnectorParseError(
                    f"reddit /r/{subreddit}/new/.rss returned an unexpected payload: {type(exc).__name__}: {exc}"
                ) from exc

            if not entries:
                return  # empty page, nothing more to consume

            last_atom_id: str | None = None
            for entry in entries:
                obs = NewRedditPostObservation.from_atom_entry(entry, spec)
                last_atom_id = entry.atom_id or last_atom_id
                if since is not None and obs.occurred_at <= since:
                    # /new is reverse-chronological, all remaining items on
                    # this and later pages are older. We've caught up.
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

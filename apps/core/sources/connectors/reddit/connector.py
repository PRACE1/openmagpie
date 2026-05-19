import json
import logging
from collections.abc import Iterator
from datetime import datetime

import httpx
from pydantic import ValidationError

from events.observations import Observation
from events.registry import register
from listeners.configs import RedditSubredditStreamSpec
from listeners.models import Listener

from ..base import BaseConnector, ConnectorParseError
from .observations import NewRedditPostObservation
from .payloads import RedditListing

logger = logging.getLogger("sources")

# Reddit's anonymous JSON endpoint (`<any-web-url>.json`) is the website's
# "render this page as JSON" side-effect, not the documented API. Filters out
# custom/library User-Agents (returns 503); browser-shaped UAs get through.
# For production / heavy use, switch to authenticated PRAW with a registered
# Reddit app (https://www.reddit.com/prefs/apps), the real API lives at
# oauth.reddit.com.
REDDIT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Reddit's anonymous max page size is 100. Cap pagination at MAX_PAGES so a
# Listener that's been silent for weeks doesn't fetch unbounded history on
# wake; with PAGE_SIZE=100, MAX_PAGES=10 covers the latest ~1000 posts.
PAGE_SIZE = 100
MAX_PAGES = 10


class RedditSubRedditConnector(BaseConnector):
    """Polls a single subreddit's `/r/<slug>/new/.json` feed.

    Cold-start semantics: the *first* poll for a brand-new Listener (StreamWatch
    with `last_event_at=None`) yields whatever a single poll cycle returns,
    up to `MAX_PAGES x PAGE_SIZE` posts from the head of /new. Every subsequent
    cycle is pure live mode: only posts newer than `last_event_at` are yielded.

    There is no backfill. Posts older than the moment we cold-started are
    out of scope for this connector; treat the first poll as "everything
    starts from now-ish." Reddit's anonymous /new caps at ~1000 items total
    regardless, so the cold-start window is bounded API-side too.

    Future Reddit variants (user feed, search, comments, ...) get their own
    connector class + kind. If one of them grows a real "backfill N days"
    requirement, that's a separate feature with its own state machine
    (cursor + horizon + completion flag), not a cursor smuggled in here.
    """

    kind = "reddit_subreddit"
    observations: list[type[Observation]] = [NewRedditPostObservation]

    # `count` is the universal poll-walk default from BaseConnector: it
    # re-walks the page fetch (~10 GETs to /new.json) discarding each
    # observation. Reddit has no cheaper exact-count path, so we don't
    # override it.

    def poll(
        self,
        spec: RedditSubredditStreamSpec,
        listener: Listener,
        since: datetime | None,
    ) -> Iterator[NewRedditPostObservation]:
        subreddit = spec.subreddit
        if not subreddit:
            raise ValueError(f"RedditSubredditStreamSpec missing subreddit: {spec}")

        # `/new` is sorted newest → oldest. Reddit has no server-side `since`
        # filter; the early-return on `obs.occurred_at <= since` works only
        # because of that ordering, once we see a post older than `since`,
        # every remaining post on this page and every later page is older too.
        # The `after` cursor walks pages from newest to oldest in the same order.
        # Trailing slash before .json matters, `/new.json` returns 503, `/new/.json` returns 200.
        url = f"https://www.reddit.com/r/{subreddit}/new/.json"
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
                listing = RedditListing.model_validate(response.json())
            except (json.JSONDecodeError, ValidationError) as exc:
                # 200 with an HTML body, a Reddit-side schema change, or a
                # missing required field on a post should fail this stream's
                # poll cycle, not the whole scheduler.
                raise ConnectorParseError(
                    f"reddit /r/{subreddit}/new/.json returned an unexpected payload: {type(exc).__name__}: {exc}"
                ) from exc

            after = listing.data.after
            if not listing.data.children:
                return  # empty page, nothing more to consume

            for child in listing.data.children:
                obs = NewRedditPostObservation.from_reddit_blob(child.data, listener, spec)
                if since is not None and obs.occurred_at <= since:
                    # /new is reverse-chronological, all remaining items on
                    # this and later pages are older. We've caught up.
                    return
                yield obs

            if not after:
                return  # end of feed


# Register observations for hydration of Event.data, single source of truth via the class attrs.
register(RedditSubRedditConnector.kind, RedditSubRedditConnector.observations)

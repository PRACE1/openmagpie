"""Feed poll orchestrator: fetch each stream, persist items, prune.

The Feed owns polling (Listeners judge the resulting items). For each
stream in the feed's config this fetches via the source connector,
persists new items as FeedItems (idempotent), advances the per-stream
watermark, and prunes the item log to the retention window.

Per-stream `last_event_at` is non-None by invariant: feed-config policy
fills it with wall-clock now at save time so the first poll fetches
real items (no cold-start "set watermark, fetch nothing" trip). An
operator who wants historical context passes an explicit past datetime
at create.

`FeedPollOperation` is a one-shot operation; build with a Feed and call
`.run()` once.
"""

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property

import httpx
from django.utils import timezone
from pydantic import ValidationError

from common.locks import poll_lock
from events.observations import Observation
from feeds.configs import CuratedFeedConfig
from feeds.models import Feed
from feeds.registry import load_config
from listeners.services import ListenerService
from sources import registry as source_registry
from sources.connectors.base import ConnectorParseError

from .feeds import FeedService

logger = logging.getLogger("feeds")

# Per-stream failures we expect and recover from (one bad stream must not
# abort the whole feed cycle). Anything else is a bug and should propagate.
_RECOVERABLE_ERRORS = (
    httpx.HTTPError,
    ValidationError,
    ConnectionError,
    ConnectorParseError,
)


@dataclass(frozen=True)
class StreamPolled:
    """Per-stream progress, emitted once after each stream is polled."""

    feed: Feed
    stream_display: str
    observed: int
    recorded: int


FeedPollProgressCallback = Callable[[StreamPolled], None]


@dataclass(frozen=True)
class FeedPollResult:
    observed: int
    recorded: int
    pruned: int


class _ObservedStream:
    """Connector iterator wrapped so the caller can read `count` + `newest`
    occurred_at after `record_items` has consumed it.

    Keeps the poll loop streaming (no list materialization) while still
    surfacing the count + watermark the caller needs for progress + the
    per-stream watermark advance. A recoverable error while pulling the
    next observation (one malformed payload, a paging hiccup) skips that
    one item — the rest of the stream's items still flow through to
    record_items.
    """

    def __init__(self, source: Iterator[Observation], *, initial_newest: datetime) -> None:
        self._source = source
        self.newest = initial_newest
        self.observed = 0
        self.dropped = 0

    def __iter__(self) -> Iterator[Observation]:
        source = iter(self._source)
        while True:
            try:
                obs = next(source)
            except StopIteration:
                return
            except _RECOVERABLE_ERRORS as exc:
                # Per-observation guard: a single malformed payload (or a
                # transient HTTP hiccup mid-pagination) skips one item
                # instead of aborting the rest of the stream this cycle.
                self.dropped += 1
                logger.warning(
                    "dropped observation: %s: %s",
                    type(exc).__name__,
                    exc,
                )
                continue
            self.observed += 1
            if obs.occurred_at > self.newest:
                self.newest = obs.occurred_at
            yield obs


class FeedPollOperation:
    """One-shot: poll a single Feed's streams, persist + prune its items."""

    def __init__(self, feed: Feed, *, on_progress: FeedPollProgressCallback | None = None) -> None:
        config = load_config(feed)
        if not isinstance(config, CuratedFeedConfig):
            raise NotImplementedError(f"Unsupported feed kind: {feed.kind}")
        self.feed = feed
        self.config = config
        self.account_id = str(feed.account_id)
        self.on_progress: FeedPollProgressCallback = on_progress or (lambda _: None)

    @cached_property
    def feed_svc(self) -> FeedService:
        return FeedService(account_id=self.account_id)

    def run(self) -> FeedPollResult:
        started_at = timezone.now()
        observed = 0
        recorded = 0
        streams_succeeded = 0

        for watch in self.config.streams:
            try:
                s_observed, s_recorded = self._poll_stream(watch)
                observed += s_observed
                recorded += s_recorded
                streams_succeeded += 1
            except _RECOVERABLE_ERRORS as exc:
                logger.warning(
                    "stream polling failed feed=%s spec=%s err=%s: %s",
                    self.feed.id,
                    watch.spec.display(),
                    type(exc).__name__,
                    exc,
                )

        # Persist poll cadence. On a full-outage cycle (no stream
        # succeeded, no watermark advanced), pass data=None so we don't
        # write the pre-cycle config snapshot back — that would silently
        # revert any concurrent operator edit. last_polled_at /
        # next_poll_at still advance so the scheduler respects the
        # operator's cadence during the outage instead of tight-looping.
        total_streams = len(self.config.streams)
        full_outage = total_streams > 0 and streams_succeeded == 0
        self.feed_svc.update_poll_state(
            self.feed,
            last_polled_at=started_at,
            data=None if full_outage else self.config.model_dump(mode="json"),
        )

        # Skip prune on the same full-outage signal. A sustained connector
        # outage would otherwise erode the retention window with no fresh
        # data flowing in — by day retention+1 the listener cursors would
        # be pointing at pruned ids with nothing to judge once it clears.
        if full_outage:
            logger.warning(
                "feed=%s: all %d streams failed this cycle; skipping retention prune",
                self.feed.id,
                total_streams,
            )
            pruned = 0
        else:
            # Floor prune by the slowest active subscriber's cursor so a
            # lagging listener can't silently lose items pruned out from
            # under it. None = no active subscribers (prune at retention).
            min_cursor = ListenerService.Global.min_cursor_for_feed(self.feed)
            pruned = self.feed_svc.prune_items(
                self.feed,
                retention_days=self.config.retention_days,
                now=started_at,
                min_subscriber_cursor=min_cursor,
            )
        return FeedPollResult(observed=observed, recorded=recorded, pruned=pruned)

    def _poll_stream(self, watch) -> tuple[int, int]:
        """Poll one stream. Returns (observed, recorded).

        `watch.last_event_at` is non-None by invariant (feed-config
        policy fills it at save time). The connector treats it as a
        since-cursor; items with `occurred_at <= since` are skipped."""
        # Stream the connector iterator straight into record_items so a busy
        # stream doesn't pile every observation into memory before any write.
        # `_ObservedStream` tallies the count + max occurred_at as items flow.
        connector = source_registry.get(watch.spec.kind)
        stream = _ObservedStream(
            connector.poll(watch.spec, since=watch.last_event_at),
            initial_newest=watch.last_event_at,
        )
        recorded = self.feed_svc.record_items(
            self.feed,
            stream_label=watch.spec.display(),
            observations=stream,
        )
        # Advance the watermark to the newest item seen this cycle.
        watch.last_event_at = stream.newest
        self.on_progress(StreamPolled(self.feed, watch.spec.display(), stream.observed, recorded))
        return stream.observed, recorded


def poll_feed(feed: Feed, *, on_progress: FeedPollProgressCallback | None = None) -> FeedPollResult | None:
    """Locked entry point for a single Feed's poll cycle. Returns None if
    another process holds `poll_lock(feed.id)` (caller records a skip)."""
    with poll_lock(str(feed.id)) as acquired:
        if not acquired:
            return None
        return FeedPollOperation(feed, on_progress=on_progress).run()

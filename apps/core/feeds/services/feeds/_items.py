"""FeedItemService: account-scoped reads + writes on the items log.

The Feed row itself is owned by FeedService (CRUD); this service owns
everything about the items that accumulate under a Feed: recording new
items on poll, pruning to the retention window, and the read queries
the judge cycle and detail views need.
"""

import builtins
import logging
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta

from django.utils import timezone

from common.fields import min_ulid_at
from events.observations import Observation
from feeds.models import Feed, FeedItem

logger = logging.getLogger("feeds")


class FeedItemService:
    """Account-scoped service for FeedItem reads + writes against a Feed."""

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("FeedItemService requires account_id")
        self.account_id = account_id

    def _assert_scope(self, account_id: str, what: str) -> None:
        if account_id != self.account_id:
            raise ValueError(f"{what} account_id mismatch: {account_id!r} not in scope {self.account_id!r}")

    def record_items(
        self,
        feed: Feed,
        /,
        *,
        stream_label: str,
        observations: Iterable[Observation],
        chunk_size: int = 200,
    ) -> int:
        """Persist a stream's polled items as FeedItems. Idempotent on
        (feed_id, source, external_id); returns the count of NEW rows.

        Streams the iterable in fixed-size chunks so memory stays
        O(chunk_size). Per chunk: one SELECT to find existing keys, one
        bulk_create for the new rows; O(N / chunk_size) round trips
        instead of O(N). The feed's poll_lock is held during the whole
        cycle, so the SELECT->INSERT window has no race risk.
        """
        self._assert_scope(str(feed.account_id), "feed")
        created = 0
        chunk: list[Observation] = []
        for obs in observations:
            chunk.append(obs)
            if len(chunk) >= chunk_size:
                created += self._record_chunk(feed, stream_label=stream_label, chunk=chunk)
                chunk = []
        if chunk:
            created += self._record_chunk(feed, stream_label=stream_label, chunk=chunk)
        return created

    def _record_chunk(self, feed: Feed, /, *, stream_label: str, chunk: list[Observation]) -> int:
        """Persist one chunk: SELECT existing keys, bulk_create the rest.
        Returns the count of new rows in this chunk."""
        external_ids = [obs.external_id for obs in chunk]
        existing = set(
            FeedItem.objects.filter(
                account_id=self.account_id, feed_id=feed.id, external_id__in=external_ids
            ).values_list("source", "external_id")
        )
        rows = [
            FeedItem(
                account_id=self.account_id,
                feed_id=feed.id,
                source=obs.source,
                external_id=obs.external_id,
                stream_label=stream_label,
                occurred_at=obs.occurred_at,
                data=obs.model_dump(mode="json"),
            )
            for obs in chunk
            if (obs.source, obs.external_id) not in existing
        ]
        if not rows:
            return 0
        # ignore_conflicts as a safety belt: SELECT-then-INSERT is race-free
        # under the feed's poll_lock, but ignore_conflicts means a future
        # bug or lock regression silently dedups instead of aborting the
        # whole chunk. Accurate "new rows" count comes from `rows` filtering.
        FeedItem.objects.bulk_create(rows, ignore_conflicts=True)
        return len(rows)

    def prune_items(
        self,
        feed: Feed,
        /,
        *,
        retention_days: int,
        now: datetime | None = None,
        min_subscriber_cursor: str | None = None,
    ) -> int:
        """Delete FeedItems older than retention (by ULID-ms cutoff).

        Uses `id < min_ulid_at(cutoff)` instead of `created_at < cutoff` so
        the delete plan hits the `(account_id, feed_id, id)` index as a
        tight range scan. Returns the count deleted.

        `min_subscriber_cursor`, when supplied, floors the prune so items
        a lagging listener hasn't judged yet aren't silently cut. The
        caller (FeedPollOperation) computes this via
        ListenerService.Global.min_cursor_for_feed. A lagging listener
        will hold the retention window open until it catches up; if it's
        permanently broken, this trades disk for visibility; a warning
        log fires when the cap is applied.
        """
        self._assert_scope(str(feed.account_id), "feed")
        cutoff = (now or timezone.now()) - timedelta(days=retention_days)
        cutoff_ulid = min_ulid_at(cutoff)
        if min_subscriber_cursor is not None and min_subscriber_cursor < cutoff_ulid:
            logger.warning(
                "feed=%s prune capped by lagging listener (cursor=%s < retention cutoff=%s); "
                "items older than retention are preserved until the listener catches up",
                feed.id,
                min_subscriber_cursor,
                cutoff_ulid,
            )
            prune_below = min_subscriber_cursor
        else:
            prune_below = cutoff_ulid
        deleted, _ = FeedItem.objects.filter(account_id=self.account_id, feed_id=feed.id, id__lt=prune_below).delete()
        return deleted

    def newest_item_id(self, feed: Feed, /) -> str | None:
        """ULID pk of the newest FeedItem in this feed, or None if empty.
        Used as the snapshot upper bound for a judge cycle."""
        self._assert_scope(str(feed.account_id), "feed")
        return (
            FeedItem.objects.filter(account_id=self.account_id, feed_id=feed.id)
            .order_by("-id")
            .values_list("id", flat=True)
            .first()
        )

    def count_items_in_window(
        self,
        feed: Feed,
        /,
        *,
        after_id: str,
        through_id: str,
    ) -> int:
        """How many FeedItems sit in `(after_id, through_id]` for this feed.

        Cheap COUNT query the judge cycle uses to size up work before the
        slow leg (one LLM call per item). Lets the management command
        print a "judging N items (~Ns)" heads-up so the operator knows
        what they're in for. Same window shape as `iter_items_in_window`."""
        self._assert_scope(str(feed.account_id), "feed")
        return FeedItem.objects.filter(
            account_id=self.account_id,
            feed_id=feed.id,
            id__gt=after_id,
            id__lte=through_id,
        ).count()

    def iter_items_in_window(
        self,
        feed: Feed,
        /,
        *,
        after_id: str,
        through_id: str,
        chunk_size: int = 200,
    ) -> Iterator[FeedItem]:
        """Yield FeedItems in `(after_id, through_id]` for this feed,
        chronological by ULID pk.

        Slice-based pagination (each chunk a fresh LIMIT query) instead of
        `.iterator()`, so early `break` is safe; no server-side cursor
        to dangle. Memory stays O(chunk_size) regardless of window size;
        the underlying set is also feed-retention-bounded.
        """
        self._assert_scope(str(feed.account_id), "feed")
        last_seen = after_id
        while True:
            chunk = builtins.list(
                FeedItem.objects.filter(
                    account_id=self.account_id,
                    feed_id=feed.id,
                    id__gt=last_seen,
                    id__lte=through_id,
                ).order_by("id")[:chunk_size]
            )
            if not chunk:
                return
            for item in chunk:
                yield item
                last_seen = str(item.id)
            if len(chunk) < chunk_size:
                return

    def list_recent_items(self, feed: Feed, /, *, limit: int) -> builtins.list[FeedItem]:
        # builtins.list: the method name shadows the builtin in this
        # class's annotation scope.
        """Recent FeedItems newest-first (by ULID pk). The 'sort by new
        and go' surface; all of the feed's streams interleaved."""
        self._assert_scope(str(feed.account_id), "feed")
        return builtins.list(
            FeedItem.objects.filter(account_id=self.account_id, feed_id=feed.id).order_by("-id")[:limit]
        )

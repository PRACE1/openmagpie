"""Feed service.

Account-scoped: `FeedService(account_id=X)`. Cross-tenant ops live under
`FeedService.Global`. Mirrors `listeners.services.listeners` (build/create,
build_update/update, dry-run via the unsaved build*). Adds the item-log
operations the Feed owns: record_items, prune_items, list_recent_items.
"""

import builtins
import logging
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from common.fields import min_ulid_at
from events.observations import Observation
from feeds.models import Feed, FeedItem
from feeds.policy import PolicyError, enforce_policy
from feeds.registry import load_config, parse_config, validate_config

from ._feeds_global import FeedGlobal

logger = logging.getLogger("feeds")


class FeedService:
    """Account-scoped service for Feed reads, writes, and the item log."""

    Global = FeedGlobal

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("FeedService requires account_id")
        self.account_id = account_id

    def _assert_scope(self, account_id: str, what: str) -> None:
        if account_id != self.account_id:
            raise ValueError(f"{what} account_id mismatch: {account_id!r} not in scope {self.account_id!r}")

    def get(self, id: str) -> Feed:
        """Raises Feed.DoesNotExist if missing (or owned by another account)."""
        return Feed.objects.get(id=id, account_id=self.account_id)

    def list(self, *, after: str | None = None, limit: int = 50) -> list[Feed]:
        """This account's feeds, newest first (by ULID pk).

        Cursor-paginated: pass `after=<id>` to fetch rows whose id is
        strictly less than that (ULIDs sort by creation, so "less than"
        = "older than"). `limit` caps the page size."""
        qs = Feed.objects.filter(account_id=self.account_id)
        if after:
            qs = qs.filter(id__lt=after)
        return list(qs.order_by("-id")[:limit])

    def build(
        self,
        *,
        user_id: str,
        name: str,
        kind: str,
        poll_interval_seconds: int,
        data: dict[str, Any],
    ) -> Feed:
        """Validate inputs and return an UNSAVED Feed (dry-run preview)."""
        validated = validate_config(kind, data)
        normalized_data = validated.model_dump(mode="json")
        return Feed(
            user_id=user_id,
            account_id=self.account_id,
            kind=kind,
            name=name,
            poll_interval_seconds=poll_interval_seconds,
            data=normalized_data,
        )

    def create(
        self,
        *,
        user_id: str,
        name: str,
        kind: str,
        poll_interval_seconds: int,
        data: dict[str, Any],
    ) -> Feed:
        feed = self.build(
            user_id=user_id,
            name=name,
            kind=kind,
            poll_interval_seconds=poll_interval_seconds,
            data=data,
        )
        feed.save()
        return feed

    def build_update(
        self,
        feed: Feed,
        /,
        *,
        name: str,
        poll_interval_seconds: int,
        data: dict[str, Any],
    ) -> Feed:
        """Validate an edit, apply to the EXISTING feed (unsaved). `kind`
        is immutable (changing it would swap the config schema)."""
        self._assert_scope(str(feed.account_id), "feed")

        # parse_config = shape only. Policy runs on the MERGE OUTPUT (what
        # persists); merge_preserving carries forward per-stream watermarks.
        submitted = parse_config(str(feed.kind), data)
        prior = load_config(feed)
        try:
            merged = submitted.merge_preserving(prior)
        except ValueError as exc:
            # merge refusal (shouldn't happen for curated feeds w/o secrets,
            # but the contract is shared) -> 400, never a 500.
            raise PolicyError(str(exc)) from exc
        merged = enforce_policy(merged)

        feed.name = name
        feed.poll_interval_seconds = poll_interval_seconds
        feed.data = merged.model_dump(mode="json")
        return feed

    def update(
        self,
        feed: Feed,
        /,
        *,
        name: str,
        poll_interval_seconds: int,
        data: dict[str, Any],
    ) -> Feed:
        feed = self.build_update(feed, name=name, poll_interval_seconds=poll_interval_seconds, data=data)
        feed.save(update_fields=["name", "poll_interval_seconds", "data", "updated_at"])
        return feed

    def delete(self, feed: Feed, /) -> None:
        """Delete a Feed and its FeedItems. Wrapped in transaction.atomic()
        so a failure between the cascade and the row delete can't orphan
        items (FeedItem.feed_id is a plain CharField, not a FK; no DB-level
        cascade — the service owns the cleanup)."""
        self._assert_scope(str(feed.account_id), "feed")
        with transaction.atomic():
            FeedItem.objects.filter(account_id=self.account_id, feed_id=feed.id).delete()
            feed.delete()

    # ── poll-state + item log (the Feed owns these) ──────────────────────

    def update_poll_state(
        self,
        feed: Feed,
        /,
        *,
        last_polled_at: datetime,
        data: dict | None,
    ) -> None:
        """Persist the poll cadence + (optionally) the config blob.

        `data=None` means "don't touch `feed.data`" — used on full-outage
        cycles where no stream ran, so there are no advanced watermarks to
        persist and writing the pre-cycle snapshot back would just clobber
        any concurrent operator edit. We still advance last_polled_at /
        next_poll_at so the scheduler respects the operator's cadence
        instead of tight-looping the outage.

        Detect-and-log the poller-vs-editor race on `feed.data`: if the
        row was updated by a PUT between the start of this poll cycle and
        this save, the operator's edit is about to be reverted (we're
        writing the pre-edit shape + advanced watermarks). One single-row
        SELECT per poll cycle; observable without changing behavior, so
        we can decide whether to add a poll_lock to the PUT path later.
        """
        self._assert_scope(str(feed.account_id), "feed")
        if data is not None:
            try:
                db_updated_at = Feed.objects.only("updated_at").get(id=feed.id).updated_at
            except Feed.DoesNotExist:
                db_updated_at = feed.updated_at
            if db_updated_at and feed.updated_at and db_updated_at > feed.updated_at:
                logger.warning(
                    "feed=%s was updated mid-poll (db=%s, snapshot=%s); operator's edit may be "
                    "reverted by this save — investigate adding poll_lock to FeedService.update "
                    "if this fires in practice",
                    feed.id,
                    db_updated_at.isoformat(),
                    feed.updated_at.isoformat(),
                )
        feed.last_polled_at = last_polled_at
        feed.next_poll_at = last_polled_at + timedelta(seconds=int(feed.poll_interval_seconds))
        update_fields = ["last_polled_at", "next_poll_at", "updated_at"]
        if data is not None:
            feed.data = data
            update_fields.insert(2, "data")
        feed.save(update_fields=update_fields)

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
        bulk_create for the new rows — O(N / chunk_size) round trips
        instead of O(N). The feed's poll_lock is held during the whole
        cycle, so the SELECT→INSERT window has no race risk.
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
        permanently broken, this trades disk for visibility — a warning
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
        `.iterator()`, so early `break` is safe — there's no server-side
        cursor to dangle. Memory stays O(chunk_size) regardless of window
        size; the underlying set is also feed-retention-bounded.
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
        # builtins.list: the method named `list` above shadows the builtin
        # in this class's annotation scope (same as the CLI's list()).
        """Recent FeedItems newest-first (by ULID pk). The 'sort by new
        and go' surface; all of the feed's streams interleaved."""
        self._assert_scope(str(feed.account_id), "feed")
        return list(FeedItem.objects.filter(account_id=self.account_id, feed_id=feed.id).order_by("-id")[:limit])

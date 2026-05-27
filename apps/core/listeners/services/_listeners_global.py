"""Cross-tenant Listener operations.

Do NOT import from this file directly, use `ListenerService.Global.<op>(...)`.
The leading underscore signals "implementation detail of listeners.py"; the
`.Global` namespace on the service class is the stable public surface.

Reach for these sparingly, scheduler entry points + admin / debug commands.
"""

from collections.abc import Iterator
from datetime import datetime

from django.db.models import Q

from feeds.models import Feed
from listeners.models import Listener


class ListenerGlobal:
    """Static methods only. Span all accounts."""

    @staticmethod
    def get(id: str) -> Listener:
        """Look up a Listener regardless of account. Raises DoesNotExist if missing.
        Use only in system-level contexts (scheduler, management commands)."""
        return Listener.objects.get(id=id)

    @staticmethod
    def list_active(*, chunk_size: int = 100) -> Iterator[Listener]:
        """Every active Listener across all accounts. The judgment entry
        point: each listener consumes new FeedItems since its cursor, so
        judging all of them is cheap when none have new items (just a
        cursor query). No per-listener judge cadence - judgment rides the
        Feed's poll cadence (new items appear when the Feed polls)."""
        return Listener.objects.filter(is_active=True).iterator(chunk_size=chunk_size)

    @staticmethod
    def list_due_for_digest(*, now: datetime, chunk_size: int = 100) -> Iterator[Listener]:
        """Active digest-mode Listeners whose digest interval has elapsed.
        Spans all accounts, scheduler entry point."""
        return (
            Listener.objects.filter(is_active=True, delivery_mode=Listener.DeliveryMode.DIGEST)
            .filter(Q(next_digest_at__isnull=True) | Q(next_digest_at__lte=now))
            .iterator(chunk_size=chunk_size)
        )

    @staticmethod
    def min_cursor_for_feed(feed: Feed) -> str | None:
        """Smallest non-empty `last_judged_item_id` across active listeners
        subscribed to this feed. Used by `FeedService.prune_items` to floor
        the retention cutoff so a lagging listener doesn't silently lose
        items pruned out from under it.

        Empty cursors (just-created listeners whose first judge cycle
        hasn't run yet) are excluded so a broken listener can't block
        prune indefinitely. Practical consequence: a fresh listener's
        "judges the retention window on first cycle" promise applies to
        whatever survives prune AT FIRST-JUDGE TIME, not at create time.
        If a prune cycle fires between listener create and the listener's
        first judge tick (typically <60s), items past retention are gone.
        Operators who care about a deterministic "what was there at
        create time" snapshot should poll the feed before creating the
        listener, or use `seed_cursor=latest` to opt out of retention
        backfill entirely.

        Returns None when there are no active subscribers OR all have
        empty cursors."""
        # In-account scope: feed and its subscribers always share an
        # account. JSON access for feed_id; no schema column for it.
        candidates = Listener.objects.filter(account_id=str(feed.account_id), is_active=True).only(
            "data", "last_judged_item_id"
        )
        cursors = [
            c.last_judged_item_id
            for c in candidates
            if c.last_judged_item_id and (c.data or {}).get("feed_id") == str(feed.id)
        ]
        return min(cursors) if cursors else None

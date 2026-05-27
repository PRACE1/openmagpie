"""Rolling hit-rate stats for listeners.

A listener's recent hit rate = hits / items the feed saw, over a trailing
window. Both come from existing timestamped, retention-bounded data (hit
Events' created_at; FeedItems' created_at kept for the feed's
retention_days) — no stored counters, no per-miss records.

`compute_hit_rates` is BATCHED to a constant number of queries regardless
of how many listeners are passed (no N+1): one Event aggregate, one
FeedItem aggregate, one Feed fetch. The list view passes all listeners;
the detail view passes one.
"""

import logging
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from events.models import Event
from events.services import EventKind
from feeds.models import FeedItem
from listeners.models import Listener
from listeners.registry import load_config

logger = logging.getLogger("listeners")

# Trailing window for the rolling rate. Kept <= typical feed retention so the
# denominator (FeedItems in the window) isn't truncated by pruning. If a feed
# retains less than this, its denominator is incomplete and the rate reads
# slightly high — acceptable; the window is reported so it's legible.
DEFAULT_WINDOW_DAYS = 7


def compute_hit_rates(
    listeners: list[Listener],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict[str, tuple[int, int]]:
    """Return {listener_id: (hits, items)} over the trailing window.

    Batched: 3 queries total regardless of len(listeners). Listeners are
    assumed already account-scoped by the caller."""
    if not listeners:
        return {}

    cutoff = timezone.now() - timedelta(days=window_days)
    listener_ids = [str(item.id) for item in listeners]
    account_ids = {str(item.account_id) for item in listeners}

    # Per-listener config (just feed_id) — in-memory, no query. Per-row
    # fallback: a listener whose `data` no longer validates against the
    # current schema (drift, manual edit) gets a None sentinel here and
    # falls through to (0, 0) below, instead of 500-ing the whole list
    # endpoint. Mirrors the per-row resilience in feeds/serializers.py's
    # _redacted_data.
    configs: dict[str, object | None] = {}
    for item in listeners:
        try:
            configs[str(item.id)] = load_config(item)
        except Exception:
            logger.exception("listener %s data failed validation; reporting (0, 0)", item.id)
            configs[str(item.id)] = None
    feed_ids = {fid for c in configs.values() if c is not None and (fid := getattr(c, "feed_id", None))}

    # Numerator: hits per listener in the window. 1 query.
    hit_counts = {
        row["listener_id"]: row["c"]
        for row in (
            Event.objects.filter(
                account_id__in=account_ids,
                listener_id__in=listener_ids,
                kind=EventKind.HIT,
                created_at__gte=cutoff,
            )
            .values("listener_id")
            .annotate(c=Count("id"))
        )
    }

    # Denominator: items per feed in the window. 1 query.
    items_by_feed: dict[str, int] = {
        row["feed_id"]: row["c"]
        for row in (
            FeedItem.objects.filter(
                account_id__in=account_ids,
                feed_id__in=feed_ids,
                created_at__gte=cutoff,
            )
            .values("feed_id")
            .annotate(c=Count("id"))
        )
    }

    out: dict[str, tuple[int, int]] = {}
    for item in listeners:
        lid = str(item.id)
        config = configs[lid]
        if config is None:
            out[lid] = (0, 0)
            continue
        feed_id = getattr(config, "feed_id", None)
        if not feed_id:
            out[lid] = (0, 0)
            continue
        out[lid] = (hit_counts.get(lid, 0), items_by_feed.get(feed_id, 0))
    return out

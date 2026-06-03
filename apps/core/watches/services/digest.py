"""WatchDigestWindowService: the per-action digest-window coordinator.

A digest delivery action batches a FIXED window of items into one emission:
the first arrival opens it (close_at = now + interval) and later arrivals
join WITHOUT extending close_at, so it's anchored at first arrival, not a
sliding/rolling window. The window's only persistent state is
`WatchActionDigestWindow.close_at` (when the open window closes, null when
none). Membership is NOT stored ; the action's pending runs ARE the batch
(the drain excludes a digest action's runs, so its only pending runs are
the un-emitted batch).

Coordination is `select_for_update` on the window row, so it composes with
the CALLER's transaction (the drain's completion txn, the flush's close
txn) — unlike a cache lock, which can't live inside a transaction. Methods
here MUST be called inside `transaction.atomic()`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta

from django.utils import timezone

from openmagpie_schema.watch_enums import WatchActionRunState
from watches.models import WatchActionDigestWindow, WatchActionRun

_PENDING = WatchActionRunState.PENDING.value


class WatchDigestWindowGlobal:
    """Cross-tenant window reads for the flush cron. Static methods."""

    @staticmethod
    def iter_due(*, now: datetime | None = None) -> Iterator[WatchActionDigestWindow]:
        """Open windows whose close time has elapsed (the flush's worklist).
        order_by(close_at) for deterministic oldest-first flushing ; the
        single-flight cron already rules out starvation, this is just cheap
        determinism."""
        ts = now or timezone.now()
        return (
            WatchActionDigestWindow.objects.filter(close_at__isnull=False, close_at__lte=ts)
            .order_by("close_at")
            .iterator(chunk_size=100)
        )


class WatchDigestWindowService:
    """Account-scoped window coordination. Call inside a transaction."""

    Global = WatchDigestWindowGlobal

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("WatchDigestWindowService requires account_id")
        self.account_id = account_id

    def open_window(self, action_id: str, *, interval_seconds: int, now: datetime | None = None) -> datetime:
        """Ensure an open window for the action and return its close time.
        Opens a fresh window (now + interval) only when none is open, so
        arrivals within an open window join the same close time.

        get_or_create UNDER select_for_update in ONE step: ensuring and locking
        the row aren't split across two queries, so a concurrent
        _clear_digest_windows DELETE (remove / digest->instant, no lock against
        this caller) can't land in a gap and make a second read raise
        DoesNotExist — which would roll back the caller's advance txn. Once this
        returns we hold the row lock, so any such DELETE blocks until our
        commit. The lock also serializes against the flush close."""
        ts = now or timezone.now()
        window, _ = WatchActionDigestWindow.objects.select_for_update().get_or_create(
            account_id=self.account_id, action_id=action_id, defaults={"close_at": None}
        )
        if window.close_at is None:
            window.close_at = ts + timedelta(seconds=interval_seconds)
            window.save(update_fields=["close_at", "updated_at"])
        return window.close_at

    def close_if_drained(self, action_id: str) -> bool:
        """After a flush emitted a window's batch: close it (close_at=null,
        so the next arrival reopens) iff no pending runs remain. Returns
        whether it closed. The pending check runs UNDER the row lock, which
        serializes against an arrival's open_window (whose run-enqueue is
        committed while it holds the lock) — so a straggler that joined during
        the emit is either visible here (leave open, re-flush) or its
        open_window runs after the close and reopens a fresh window. Either
        way: no orphan.

        Tolerates the window already being gone: a concurrent mutate-away
        (_clear_digest_windows, on remove or digest->instant) DELETEs the row
        with no lock against the flush, and this runs inside the flush's
        terminal txn (alongside complete_batch). A bare .get() would raise
        DoesNotExist and roll back the just-committed run completions, leaving
        them PENDING for re-delivery. No row => nothing to close => drained."""
        window = (
            WatchActionDigestWindow.objects.select_for_update()
            .filter(account_id=self.account_id, action_id=action_id)
            .first()
        )
        if window is None:
            return True
        if WatchActionRun.objects.filter(account_id=self.account_id, action_id=action_id, state=_PENDING).exists():
            return False
        window.close_at = None
        window.save(update_fields=["close_at", "updated_at"])
        return True

"""WatchActionRunService: enqueue + claim + complete watch-action runs.

The stateful queue under the trigger/drain crons:
  - the TRIGGER (`process_due_watches`) calls `enqueue` to create the
    first PENDING run for a new feed item.
  - the DRAIN (`process_due_runs`) calls `Global.reap_stale`, then
    `Global.claim_due` (a CAS that flips PENDING/FAILED -> RUNNING and
    burns an attempt), runs the action, and calls `complete` (terminal
    state + result) ; on SUCCEEDED it `enqueue`s the next chain action.

Both `claim_due` and `complete` are compare-and-swap UPDATEs keyed on
(state, attempts), so the row IS the lock at BOTH ends: overlapping drains
can't double-claim, and a drain whose claim was reaped + re-taken mid-judge
can't double-complete (its stale `complete` matches no row and returns
None, so it never advances the chain). A run that crashes mid-flight is
left RUNNING and recovered by `reap_stale`. Retries are bounded by
`attempts < WATCH_RUN_MAX_ATTEMPTS`.
"""

from __future__ import annotations

import builtins
import itertools
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Count, Exists, F, OuterRef
from django.utils import timezone

from openmagpie_schema.watch_enums import WatchActionRunState
from watches import run_messages
from watches.models import WatchActionDigestWindow, WatchActionRun

from ._run_batches import DigestBatchMixin

_PENDING = WatchActionRunState.PENDING.value
_RUNNING = WatchActionRunState.RUNNING.value
_FAILED = WatchActionRunState.FAILED.value

# The states a stalled/failed run can be re-claimed from. ERRORED and
# SKIPPED are terminal (permanent defect / deliberate) ; GATED + SUCCEEDED
# are clean terminals. Only PENDING (never run) and FAILED (transient,
# retryable) are claimable.
_CLAIMABLE = (_PENDING, _FAILED)

# Trigger-enqueue chunk: feed-item ids per SELECT-have + bulk_create round.
# Bounds BOTH the in-memory footprint (the `have` set + row list) AND the
# INSERT size — same unit of work, so ONE constant (splitting invites drift).
# Chunk <= this, so bulk_create needs no own `batch_size`: one chunk = one
# INSERT. A module constant (internal perf knob, no per-deployment meaning).
_ENQUEUE_CHUNK = 500


def _due_runs(ts: datetime):
    """Runs eligible to drain at `ts`: claimable state, under the attempts
    cap, scheduled time elapsed, NOT a digest action's (those are the flush's,
    derived from the action's window row). SINGLE source of truth for "due"
    so `count_due` can't drift from what `claim_due` yields. Unordered."""
    has_window = Exists(
        WatchActionDigestWindow.objects.filter(account_id=OuterRef("account_id"), action_id=OuterRef("action_id"))
    )
    return WatchActionRun.objects.filter(
        state__in=_CLAIMABLE,
        attempts__lt=settings.WATCH_RUN_MAX_ATTEMPTS,
        scheduled_at__lte=ts,
    ).filter(~has_window)


class WatchActionRunGlobal:
    """Cross-tenant run operations for the drain cron. Static methods."""

    @staticmethod
    def reap_stale(*, now: datetime | None = None) -> int:
        """Reset runs stuck in RUNNING past WATCH_RUN_STALE_SECONDS to
        FAILED (presumed crashed worker). Their attempt was already burned
        at claim, so this can't loop forever. Returns the count reaped.
        Runs first in each drain pass so a crashed run rejoins the retry
        pool (or hits the attempts cap and stays FAILED terminally).

        Branches on the attempts cap so the message tells the truth: a run
        crashed on its FINAL attempt won't be re-claimed (claim_due needs
        attempts < MAX), so it gets a terminal message, not 'will retry'."""
        ts = now or timezone.now()
        cutoff = ts - timedelta(seconds=settings.WATCH_RUN_STALE_SECONDS)
        max_attempts = settings.WATCH_RUN_MAX_ATTEMPTS
        stale = WatchActionRun.objects.filter(state=_RUNNING, started_at__lt=cutoff)
        # Exhausted: timed out with no retries left -> terminal. completed_at
        # set (it's truly done), honest message (claim_due skips it).
        exhausted = stale.filter(attempts__gte=max_attempts).update(
            state=_FAILED,
            error=run_messages.TIMED_OUT_EXHAUSTED,
            completed_at=ts,
        )
        # Retryable: attempts left -> rejoins the pool. completed_at cleared
        # (not done yet), 'will retry' message. Disjoint from `exhausted` on
        # attempts, so order is irrelevant.
        retryable = stale.filter(attempts__lt=max_attempts).update(
            state=_FAILED,
            error=run_messages.TIMED_OUT,
            completed_at=None,
        )
        return exhausted + retryable

    @staticmethod
    def claim_due(*, now: datetime | None = None) -> Iterator[WatchActionRun]:
        """Yield runs due now, each already CLAIMED (CAS to RUNNING).

        "Due" = claimable state (pending / retryable-failed), under the
        attempts cap, with scheduled_at elapsed. Each candidate is claimed
        by a conditional UPDATE keyed on its still-claimable state ; only
        a row we actually flipped (updated == 1) is yielded, so two
        concurrent drains never both execute the same run. The candidate
        snapshot is read up front but the CAS re-checks state at flip time,
        so a row another drain grabbed in between is simply skipped."""
        ts = now or timezone.now()
        max_attempts = settings.WATCH_RUN_MAX_ATTEMPTS
        # `_due_runs` carries the "due" filter (incl. the digest-action
        # anti-join: a correlated ~Exists on the window table — one SQL
        # statement, no ids pulled into Python). Ordered oldest-first and
        # streamed (chunk_size) so a big backlog never materializes.
        candidates = _due_runs(ts).order_by("scheduled_at").iterator(chunk_size=100)
        for run in candidates:
            claimed = WatchActionRun.objects.filter(id=run.id, state=run.state, attempts__lt=max_attempts).update(
                state=_RUNNING, started_at=ts, attempts=F("attempts") + 1
            )
            if claimed:
                run.refresh_from_db()
                yield run

    @staticmethod
    def count_due(*, now: datetime | None = None) -> int:
        """How many runs `claim_due` would yield at `now` (same filter, no
        claim). Used only to size the drain's progress/ETA line ; it's a
        pre-pass snapshot, so the live count can drift slightly (concurrent
        drains claiming rows, or rows falling due mid-pass)."""
        return _due_runs(now or timezone.now()).count()


class WatchActionRunService(DigestBatchMixin):
    """Account-scoped run reads + writes (enqueue, complete). The digest-batch
    surface (digest_batch / complete_batch / fail_batch) is the mixin."""

    Global = WatchActionRunGlobal

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("WatchActionRunService requires account_id")
        self.account_id = account_id

    def enqueue(
        self,
        *,
        watch_id: str,
        action_id: str,
        feed_item_id: str,
        scheduled_at: datetime,
        prior_run_id: str = "",
    ) -> WatchActionRun | None:
        """Create a PENDING run for (watch, action, feed_item). Idempotent
        on that triple (unique constraint) ; returns the new run, or None if
        one already exists (so the trigger can re-scan a window safely and a
        completed run is never re-queued). `scheduled_at` is when it's
        relevant (now for instant ; the window close for a digest action).

        Uses get_or_create, not a bare exists()+create: that was TOCTOU (a
        concurrent enqueue of the same triple between the check and the
        insert would raise IntegrityError). The drain calls this INSIDE its
        completion transaction, where a raised IntegrityError would roll the
        whole completion back ; get_or_create wraps the insert in a
        savepoint and absorbs the conflict (re-fetching the winner), so the
        outer transaction survives. Same race-safety enqueue_many gets from
        ignore_conflicts."""
        run, created = WatchActionRun.objects.get_or_create(
            account_id=self.account_id,
            watch_id=watch_id,
            action_id=action_id,
            feed_item_id=feed_item_id,
            defaults={"state": _PENDING, "scheduled_at": scheduled_at, "prior_run_id": prior_run_id},
        )
        return run if created else None

    def enqueue_many(
        self,
        *,
        watch_id: str,
        action_id: str,
        feed_item_ids: Iterable[str],
        scheduled_at: datetime,
    ) -> int:
        """Batch-enqueue PENDING runs of one action over a STREAM of feed
        item ids (the trigger window). Returns the count of NEW runs.

        Consumes the iterable in `_ENQUEUE_CHUNK`-sized chunks so memory
        stays O(chunk) regardless of window size (the caller passes an
        id-only keyset iterator), and `batched` re-chunks ANY iterable, so
        even a caller that hands in a fully-materialized list is still
        processed a chunk at a time. Per chunk: one SELECT of the ids that
        already have a run for this action (idempotent re-scan), one
        bulk_create of the rest with `ignore_conflicts` as a race belt
        against a concurrent trigger ; the unique
        (account, watch, action, feed_item) constraint makes a
        double-insert a silent no-op. The 'new' count comes from the
        pre-filter; under a real race it may over-count by the few rows the
        DB silently dropped, which is fine for a progress total."""
        created = 0
        # strict=False: the final chunk is intentionally short (a window is
        # rarely an exact multiple of the chunk size). strict=True would
        # raise on the remainder.
        for chunk in itertools.batched(feed_item_ids, _ENQUEUE_CHUNK, strict=False):
            created += self._enqueue_chunk(
                watch_id=watch_id, action_id=action_id, feed_item_ids=chunk, scheduled_at=scheduled_at
            )
        return created

    def _enqueue_chunk(
        self,
        *,
        watch_id: str,
        action_id: str,
        feed_item_ids: tuple[str, ...],
        scheduled_at: datetime,
    ) -> int:
        """One chunk: SELECT existing ids, bulk_create the rest. Returns
        the count of new rows.

        Precondition: len(feed_item_ids) <= _ENQUEUE_CHUNK ; the chunk
        feeds an `IN (...)` whose bind-param count must stay under the
        backend ceiling (SQLite caps at 999). `enqueue_many` guarantees
        this via batched() ; the guard catches a direct/out-of-band caller
        before the DB throws a cryptic 'too many SQL variables'. Because
        the chunk is bounded, one bulk_create is one INSERT ; no
        `batch_size` split needed."""
        if len(feed_item_ids) > _ENQUEUE_CHUNK:
            raise ValueError(f"chunk of {len(feed_item_ids)} exceeds _ENQUEUE_CHUNK={_ENQUEUE_CHUNK}")
        have = set(
            WatchActionRun.objects.filter(
                account_id=self.account_id,
                watch_id=watch_id,
                action_id=action_id,
                feed_item_id__in=feed_item_ids,
            ).values_list("feed_item_id", flat=True)
        )
        rows = [
            WatchActionRun(
                account_id=self.account_id,
                watch_id=watch_id,
                action_id=action_id,
                feed_item_id=fid,
                state=_PENDING,
                scheduled_at=scheduled_at,
            )
            for fid in feed_item_ids
            if fid not in have
        ]
        if not rows:
            return 0
        WatchActionRun.objects.bulk_create(rows, ignore_conflicts=True)
        return len(rows)

    def complete(
        self,
        run: WatchActionRun,
        /,
        *,
        state: WatchActionRunState,
        result: dict | None = None,
        error: str = "",
        now: datetime | None = None,
    ) -> WatchActionRun | None:
        """Write a terminal state + result onto a claimed (RUNNING) run.
        The drain calls this after the action returns ; `state` is the
        outcome (succeeded / gated / errored / skipped) or failed.

        `state` is the enum, not a bare str: the column has no `choices=`,
        so a typo'd / non-terminal value would persist silently and match no
        claim/reap filter (an orphaned row). The service is the enforcement
        point ("no state magic strings") ; `.value` is taken inside.

        Guarded CAS, not a blind save: writes only if the row is STILL
        RUNNING under THIS claim (state == RUNNING AND attempts == the value
        this claim stamped). Returns the run if it won, else None ; None
        means the claim was LOST: the run sat in RUNNING past the stale
        timeout, the reaper flipped it to FAILED, and another drain
        re-claimed (attempts++) and is handling it while this one was still
        judging. The stale completer must then NOT advance the chain (the
        fresh winner does), or it would clobber the authoritative result and
        enqueue the next action a second time (double delivery). The attempts
        match makes the FRESH claim win and the stale one lose deterministically.
        `.update()` bypasses auto_now, so updated_at is set explicitly."""
        if str(run.account_id) != self.account_id:
            raise ValueError(f"run account_id mismatch: {run.account_id!r} not in scope {self.account_id!r}")
        ts = now or timezone.now()
        won = WatchActionRun.objects.filter(id=run.id, state=_RUNNING, attempts=run.attempts).update(
            state=state.value, result=result or {}, error=error, completed_at=ts, updated_at=ts
        )
        if not won:
            return None
        run.state = state.value
        run.result = result or {}
        run.error = error
        run.completed_at = ts
        return run

    def list_for_action(
        self,
        action_id: str,
        /,
        *,
        watch_id: str | None = None,
        after: str | None = None,
        limit: int = 50,
        state: str | None = None,
    ) -> builtins.list[WatchActionRun]:
        """This account's runs for one action, newest-first (ULID pk).
        Cursor-paginated for the audit CLI ; `state` filters by run state.
        Pass `watch_id` to scope the query to that watch (the runs table
        denormalizes it), so cross-watch isolation holds in the query, not
        only the caller's guard."""
        qs = WatchActionRun.objects.filter(account_id=self.account_id, action_id=action_id)
        if watch_id:
            qs = qs.filter(watch_id=watch_id)
        if state:
            qs = qs.filter(state=state)
        if after:
            qs = qs.filter(id__lt=after)
        return builtins.list(qs.order_by("-id")[:limit])

    def summary_for_action(
        self,
        action_id: str,
        /,
        *,
        since: datetime,
        until: datetime | None = None,
    ) -> tuple[dict[str, int], int, int]:
        """Activity for one action: `(evaluated, pending, running)`.
        `evaluated` is a per-terminal-state `{state: count}` of runs JUDGED in
        [since, until) — windowed on `completed_at` (evaluation time, NOT
        enqueue). pending/running have no `completed_at`, so they're returned
        as the current (un-windowed) backlog. `since` required (no all-time
        scan). GROUP BY + two counts, no blobs read."""
        base = WatchActionRun.objects.filter(account_id=self.account_id, action_id=action_id)
        evaluated_qs = base.filter(completed_at__gte=since)
        if until is not None:
            evaluated_qs = evaluated_qs.filter(completed_at__lt=until)
        evaluated = {r["state"]: r["n"] for r in evaluated_qs.values("state").annotate(n=Count("id"))}
        pending = base.filter(state=_PENDING).count()
        running = base.filter(state=_RUNNING).count()
        return evaluated, pending, running

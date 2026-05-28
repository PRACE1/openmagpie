"""JudgeListenerOperation: the one-shot cycle that judges new FeedItems
for a Listener, persists hits, fires instant delivery, and advances
the cursor.

Locked entry point is `judge_listener(listener, on_progress=...)`;
tests bypass the lock via `JudgeListenerOperation(...).run()`.
"""

import logging
from dataclasses import dataclass
from functools import cached_property

import httpx
from pydantic import ValidationError

from common.locks import poll_lock
from engine import registry as engine_registry
from engine.engines import Engine
from events.observations import Observation
from events.registry import UnhydrateableObservation, hydrate_data
from events.registry import hydrate as hydrate_event
from events.services import EventKind, EventService
from feeds.models import Feed, FeedItem
from feeds.services import FeedItemService, FeedService
from listeners import registry as listeners_registry
from listeners.models import Listener
from notifications.services import DeliveryService

from ..listeners import ListenerService
from ._cursor import _CursorSaver
from ._eta import _est_seconds_per_item, _record_judge_latency, _running_eta_seconds
from ._events import JudgeCycleStarted, JudgeItemDone, JudgeProgressCallback, JudgeResult

logger = logging.getLogger("listeners")

# Operational failures we expect and recover from. Anything outside this set
# is a programming bug and should propagate. (No connector calls here; the
# Feed does the fetching, so the set is hydrate/judge/deliver failures.)
_RECOVERABLE_ERRORS = (
    httpx.HTTPError,
    ValidationError,
    ConnectionError,
)


@dataclass(frozen=True)
class _ItemOutcome:
    """Internal carrier from `_judge_item` to `run()`. The orchestrator
    needs the engine result to update running stats before emitting the
    `JudgeItemDone` event, so the per-item method returns data instead
    of emitting directly."""

    obs: Observation
    score: float
    hit: bool
    latency_ms: int


class JudgeListenerOperation:
    """One-shot: judge a single Listener's new FeedItems, persist, deliver."""

    def __init__(self, listener: Listener, *, on_progress: JudgeProgressCallback | None = None) -> None:
        self.listener = listener
        self.config = listeners_registry.load_semantic_config(listener)
        self.account_id = str(listener.account_id)
        self.is_instant = listener.delivery_mode == Listener.DeliveryMode.INSTANT
        self.on_progress: JudgeProgressCallback = on_progress or (lambda _: None)

    @cached_property
    def listener_svc(self) -> ListenerService:
        return ListenerService(account_id=self.account_id)

    @cached_property
    def feed_svc(self) -> FeedService:
        return FeedService(account_id=self.account_id)

    @cached_property
    def feed_item_svc(self) -> FeedItemService:
        return FeedItemService(account_id=self.account_id)

    @cached_property
    def event_svc(self) -> EventService:
        return EventService(account_id=self.account_id)

    @cached_property
    def delivery_svc(self) -> DeliveryService:
        return DeliveryService(account_id=self.account_id)

    @cached_property
    def engine(self) -> Engine:
        return engine_registry.get(self.config.engine.kind)

    def run(self) -> JudgeResult:
        """Judge new FeedItems for this listener."""
        if self.is_instant and self.config.notifiers:
            self._retry_stuck_pending()

        try:
            feed = self.feed_svc.get(self.config.feed_id)
        except Feed.DoesNotExist:
            logger.warning(
                "listener %s references missing feed %s; skipping",
                self.listener.id,
                self.config.feed_id,
            )
            return JudgeResult(judged=0, hits=0)

        cursor = self.listener.last_judged_item_id or ""

        # Snapshot the newest item id NOW so items arriving mid-cycle are
        # picked up next time. On a clean cycle we advance the cursor to it.
        # On a RECOVERABLE failure we hold the cursor at the last success
        # instead, so the failed item and everything after it retry next
        # cycle rather than being skipped. FeedItems are persisted, so retry
        # is possible (the old in-memory pipeline couldn't). Trade-off: a
        # permanently-failing item blocks progress past it (loud + rare);
        # bounded retry is a follow-up.
        latest = self.feed_item_svc.newest_item_id(feed)
        if latest is None or latest <= cursor:
            return JudgeResult(judged=0, hits=0)

        # Size up the cycle before the slow leg. One cheap COUNT against
        # the same `(cursor, latest]` window the loop will iterate, so
        # callers can render "judging N items (~Ns)" up front. ETA uses
        # the per-listener EWMA so the number tracks reality across runs.
        pending = self.feed_item_svc.count_items_in_window(feed, after_id=cursor, through_id=latest)
        if pending > 0:
            est_seconds = max(1, round(pending * _est_seconds_per_item(self.listener)))
            self.on_progress(JudgeCycleStarted(listener=self.listener, pending=pending, est_seconds=est_seconds))

        judged = 0
        hits = 0
        processed = 0  # success-or-error count; drives the progress display
        last_success = cursor
        failed = False
        # Running in-cycle latency. Used to refine the ETA off the actual
        # mean for THIS cycle (more accurate than the cross-cycle EWMA
        # once any data lands; accounts for model warm-up, host load, etc).
        cycle_latency_ms = 0
        # Batched cursor saves so Ctrl-C / SIGINT / mid-cycle crash keeps
        # the work it has already done. The `finally` below flushes the
        # high-water mark regardless of how the loop exits.
        saver = _CursorSaver(self.listener_svc, self.listener)
        try:
            for item in self.feed_item_svc.iter_items_in_window(feed, after_id=cursor, through_id=latest):
                # Hydrate up front so the error reporting below has obs when
                # possible and falls back to item.external_id when not.
                try:
                    obs = hydrate_data(item.data)
                except UnhydrateableObservation as exc:
                    # PERMANENT skip: a renamed/removed connector can't ever
                    # hydrate this item. Advance past it so we don't loop on
                    # the same poison row forever; surface it on-screen so
                    # the operator sees the dead row without grepping logs.
                    logger.warning(
                        "skipping un-hydrateable item listener=%s feed_item=%s: %s",
                        self.listener.id,
                        item.id,
                        exc,
                    )
                    processed += 1
                    last_success = str(item.id)
                    saver.advance(last_success)
                    self.on_progress(
                        JudgeItemDone(
                            listener=self.listener,
                            external_id=item.external_id,
                            obs=None,
                            error=f"un-hydrateable: {exc}",
                            done=processed,
                            total=pending,
                            eta_seconds=_running_eta_seconds(
                                pending, processed, judged, cycle_latency_ms, self.listener
                            ),
                        )
                    )
                    continue

                try:
                    outcome = self._judge_item(item, obs)
                except _RECOVERABLE_ERRORS as exc:
                    logger.warning(
                        "item judgment failed listener=%s feed_item=%s err=%s: %s; "
                        "holding cursor, will retry from here next cycle",
                        self.listener.id,
                        item.id,
                        type(exc).__name__,
                        exc,
                    )
                    processed += 1
                    self.on_progress(
                        JudgeItemDone(
                            listener=self.listener,
                            external_id=item.external_id,
                            obs=obs,
                            error=f"{type(exc).__name__}: {exc}",
                            done=processed,
                            total=pending,
                            eta_seconds=_running_eta_seconds(
                                pending, processed, judged, cycle_latency_ms, self.listener
                            ),
                        )
                    )
                    failed = True
                    break

                if outcome.hit:
                    hits += 1
                judged += 1
                processed += 1
                cycle_latency_ms += outcome.latency_ms
                last_success = str(item.id)
                saver.advance(last_success)
                _record_judge_latency(self.listener, outcome.latency_ms)
                self.on_progress(
                    JudgeItemDone(
                        listener=self.listener,
                        external_id=item.external_id,
                        obs=outcome.obs,
                        score=outcome.score,
                        hit=outcome.hit,
                        latency_ms=outcome.latency_ms,
                        done=processed,
                        total=pending,
                        eta_seconds=_running_eta_seconds(pending, processed, judged, cycle_latency_ms, self.listener),
                    )
                )

            # Natural cycle completion without recoverable error: jump
            # cursor to the snapshotted `latest` so cycles with no
            # in-window items (cursor already trailed past everything
            # judgeable) still bring the cursor up to date.
            if not failed:
                saver.advance(str(latest))
        finally:
            saver.flush()
        return JudgeResult(judged=judged, hits=hits)

    def _retry_stuck_pending(self) -> None:
        """Re-fire instant delivery for any hit Event left undelivered by a
        previous failed cycle (instant listeners don't run the digest sweep)."""
        for stuck in self.event_svc.list_pending_for_listener(kind=EventKind.HIT, listener_id=str(self.listener.id)):
            try:
                obs = hydrate_event(stuck)
                self.delivery_svc.deliver_instant(stuck, obs, self.listener, self.config)
            except UnhydrateableObservation as exc:
                # Permanent: the connector was renamed/removed; this stuck
                # Event can never hydrate. Skip and continue; operator
                # can clean up the orphaned row.
                logger.warning(
                    "stuck-pending un-hydrateable listener=%s event=%s: %s",
                    self.listener.id,
                    stuck.id,
                    exc,
                )
            except _RECOVERABLE_ERRORS as exc:
                logger.warning(
                    "stuck-pending retry failed listener=%s event=%s err=%s: %s",
                    self.listener.id,
                    stuck.id,
                    type(exc).__name__,
                    exc,
                )

    def _judge_item(self, item: FeedItem, obs: Observation) -> _ItemOutcome:
        """Judge one already-hydrated observation; persist + (if instant)
        deliver on a hit.

        Takes `obs` from the caller so `run()` can hydrate up front and
        report errors uniformly when hydration fails vs when the engine
        call fails. Returns the data the caller needs to update running
        cycle stats and emit progress; doesn't fire `on_progress`
        itself (run() does, so the progress line carries done / total /
        ETA computed against the running cumulative latency).

        The `hit` field means "a new Event landed," not just "the
        engine scored above threshold." When the unique constraint
        refuses a dedup re-emit the line shouldn't claim HIT, otherwise
        the cycle's hits counter, the HIT markers, and the new Event
        rows would disagree."""
        # `config.engine.model or None`: empty string in the listener config
        # means "use the engine's server-side default" (settings.OLLAMA_DEFAULT_MODEL),
        # so collapse "" -> None before handing to the engine.
        result = self.engine.judge(obs, self.listener, model=self.config.engine.model or None)
        is_hit = result.score >= self.config.hit_threshold

        new_event_persisted = False
        if is_hit:
            event = self.event_svc.persist(item, self.listener, kind=EventKind.HIT, score=result.score)
            if event is not None:
                new_event_persisted = True
                if self.is_instant and self.config.notifiers:
                    self.delivery_svc.deliver_instant(event, obs, self.listener, self.config)

        return _ItemOutcome(obs=obs, score=result.score, hit=new_event_persisted, latency_ms=result.latency_ms)


def judge_listener(
    listener: Listener,
    *,
    on_progress: JudgeProgressCallback | None = None,
) -> JudgeResult | None:
    """Locked entry point for a single Listener's judgment cycle.

    Acquires `poll_lock(listener.id)`; returns None if another process holds
    it (caller records a skip), else the `JudgeResult`. Tests/debug paths
    that want to bypass the lock call `JudgeListenerOperation(...).run()`.
    """
    with poll_lock(str(listener.id)) as acquired:
        if not acquired:
            return None
        return JudgeListenerOperation(listener, on_progress=on_progress).run()

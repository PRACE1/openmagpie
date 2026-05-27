"""Judgment orchestrator: a Listener judges new FeedItems with its engine.

The Feed polls and persists every item; the Listener is an attention over
that Feed. This drives the listener leg: read FeedItems the listener
hasn't judged yet (id > its cursor) across every stream in the Feed,
judge each with the engine, and on a hit persist an Event (kind="hit")
and (for instant-mode listeners) fire the notifier.

A per-listener cursor (`Listener.last_judged_item_id`, a ULID) is what
keeps misses from being re-judged: items are processed in id order and the
cursor advances to the snapshot max each cycle. Judgment has no cadence of
its own - it rides the Feed's poll cadence (new items appear when the Feed
polls); a cycle with no new items is a cheap cursor query, no LLM calls.

Each `judge_listener` cycle starts with a stuck-pending retry for
instant-mode listeners (re-fire delivery for any hit left undelivered by a
prior failed cycle). Per-item failures are isolated so one bad payload or
a transient engine/webhook error can't abort the whole cycle.

`JudgeListenerOperation` is a one-shot operation object; build with a
Listener and call `.run()` once.
"""

import logging
from collections.abc import Callable
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
from feeds.services import FeedService
from listeners import registry as listeners_registry
from listeners.models import Listener
from notifications.services import DeliveryService

from .listeners import ListenerService

logger = logging.getLogger("listeners")

# Operational failures we expect and recover from. Anything outside this set
# is a programming bug and should propagate. (No connector calls here - the
# Feed does the fetching - so the set is hydrate/judge/deliver failures.)
_RECOVERABLE_ERRORS = (
    httpx.HTTPError,
    ValidationError,
    ConnectionError,
)


@dataclass(frozen=True)
class JudgeResult:
    judged: int
    hits: int


@dataclass(frozen=True)
class JudgeProgress:
    """Per-item progress signal. The engine is the slow leg (multi-second
    LLM call per item), so callers wanting live feedback wire an
    `on_progress` callback. `score`/`hit` are None when `error` is set."""

    listener: Listener
    obs: Observation
    score: float | None = None
    hit: bool = False
    error: str | None = None


JudgeProgressCallback = Callable[[JudgeProgress], None]


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
        # permanently-failing item blocks progress past it — loud + rare;
        # bounded retry is a follow-up.
        latest = self.feed_svc.newest_item_id(feed)
        if latest is None or latest <= cursor:
            return JudgeResult(judged=0, hits=0)

        judged = 0
        hits = 0
        last_success = cursor
        failed = False
        for item in self.feed_svc.iter_items_in_window(feed, after_id=cursor, through_id=latest):
            try:
                if self._judge_item(item):
                    hits += 1
                judged += 1
                last_success = str(item.id)
            except UnhydrateableObservation as exc:
                # PERMANENT skip: a renamed/removed connector can't ever
                # hydrate this item. Advance past it so we don't loop on
                # the same poison row forever; log loud so it's visible.
                logger.warning(
                    "skipping un-hydrateable item listener=%s feed_item=%s: %s",
                    self.listener.id,
                    item.id,
                    exc,
                )
                last_success = str(item.id)
                continue
            except _RECOVERABLE_ERRORS as exc:
                logger.warning(
                    "item judgment failed listener=%s feed_item=%s err=%s: %s; "
                    "holding cursor, will retry from here next cycle",
                    self.listener.id,
                    item.id,
                    type(exc).__name__,
                    exc,
                )
                failed = True
                break

        cursor_target = last_success if failed else str(latest)
        if cursor_target != cursor:
            self.listener_svc.advance_judge_cursor(self.listener, item_id=cursor_target)
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

    def _judge_item(self, item: FeedItem) -> bool:
        """Judge one FeedItem; persist + (if instant) deliver on a hit.
        Returns True if a NEW hit Event was persisted.

        `on_progress` fires AFTER persist so its `hit` flag means "a new
        Event landed," not "the engine scored above threshold." When the
        unique constraint refuses a dedup re-emit, the per-item progress
        line shouldn't claim HIT — the cycle's hits counter, the on_progress
        HIT markers, and the new Event rows would otherwise disagree.
        """
        obs = hydrate_data(item.data)
        # `config.engine.model or None`: empty string in the listener config
        # means "use the engine's server-side default" (settings.OLLAMA_MODEL),
        # so collapse "" → None before handing to the engine.
        result = self.engine.judge(obs, self.listener, model=self.config.engine.model or None)
        is_hit = result.score >= self.config.hit_threshold

        new_event_persisted = False
        if is_hit:
            event = self.event_svc.persist(item, self.listener, kind=EventKind.HIT, score=result.score)
            if event is not None:
                new_event_persisted = True
                if self.is_instant and self.config.notifiers:
                    self.delivery_svc.deliver_instant(event, obs, self.listener, self.config)

        self.on_progress(JudgeProgress(listener=self.listener, obs=obs, score=result.score, hit=new_event_persisted))
        return new_event_persisted


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

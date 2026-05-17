"""Polling orchestrator: drives a Listener's attached streams through the engine.

The pipeline is hit-only, Events are persisted only when an engine judges relevant.
The connector yields typed Observations in memory; the engine reads them; if a hit,
we persist the Observation as an Event row. Misses are discarded. For instant-mode
listeners, the notifier fires immediately and Event.delivered_at is set on success.

Each `poll_listener` cycle starts with a stuck-pending retry sweep for instant-mode
listeners, any Event whose previous delivery attempt failed is re-tried before
new polling begins. (Digest-mode listeners get the same effect for free since
digest delivery already re-batches pending Events each cycle.)

Per-observation and per-stream failures are isolated so a single malformed payload,
a transient Ollama timeout, or a webhook 5xx can't abort the whole listener cycle.

Polling is live-only: cold start (a StreamWatch with `last_event_at=None`)
yields whatever a single poll cycle returns from the connector, then sets the
watermark to the newest item seen. Posts older than that moment are out of
scope, there is no historical backfill. If a Listener needs deep history, that
is a separate feature with its own state model, not something the watermark
field is asked to carry.

`PollListenerOperation` is a one-shot operation object, build with a Listener and
call `.run()` once. Not reusable across runs. The module-level `poll_listener`
function is a thin wrapper kept for callers that just want the function shape.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property

import httpx
from common.locks import poll_lock
from django.utils import timezone
from engine import registry as engine_registry
from engine.engines import Engine
from events.observations import Observation
from events.registry import hydrate as hydrate_event
from events.services import EventService
from listeners import registry as listeners_registry
from listeners.configs import SemanticListenerConfig, StreamWatch
from listeners.models import Listener
from notifications.services import DeliveryService
from pydantic import ValidationError
from sources import registry as source_registry
from sources.connectors.base import ConnectorParseError

from .listeners import ListenerService

logger = logging.getLogger("listeners")

# Operational failures we expect and recover from. Anything outside this set is
# a programming bug, it should propagate, not be silently logged.
#
# Today this can log N times per cycle if a shared dep (Ollama, connector) is
# broken. Acceptable for v0; revisit with a per-cycle circuit breaker or log
# rate-limiting if operators see flooding.
_RECOVERABLE_ERRORS = (
    httpx.HTTPError,
    ValidationError,
    ConnectionError,
    ConnectorParseError,
)


@dataclass(frozen=True)
class PollResult:
    observed: int
    hits: int


@dataclass(frozen=True)
class StreamStarted:
    """Fired once at the top of each stream's poll loop.

    `expected_max` is the count this poll cycle expects to judge: 0 on
    cold start (forward-looking snapshot), otherwise the connector's
    best-effort pre-count for warm starts (`Connector.count(...)`). It
    is best-effort, not exact: `count` and `poll` are separate upstream
    walks, so a high-churn source (Reddit /new) can grow between them
    and the actual judged count can exceed `expected_max`. The progress
    UI renders `N/expected_max`, percent, and ETA from this and clamps
    gracefully when the estimate is overrun.
    """

    listener: Listener
    stream_kind: str
    stream_display: str
    expected_max: int


@dataclass(frozen=True)
class JudgeProgress:
    """Per-observation progress signal emitted by the polling loop.

    The engine is the slow leg (multi-second LLM call per observation),
    and a cold-start run can churn through hundreds of items, so callers
    that want live feedback (management commands, CLI) wire an
    `on_progress` callback to render one of these per judgment.

    `score` and `hit` are None when `error` is set, the judge call
    raised before producing a verdict, so there's nothing to report
    beyond which observation failed.
    """

    listener: Listener
    obs: Observation
    score: float | None = None
    hit: bool = False
    error: str | None = None


PollEvent = StreamStarted | JudgeProgress
PollProgressCallback = Callable[[PollEvent], None]


class PollListenerOperation:
    """One-shot operation: poll a single Listener, judge, persist, deliver.

    Build with `PollListenerOperation(listener)` and call `.run()` once.
    Not reusable across runs, internal state (counters, watermarks via the
    mutated config) is tied to a single cycle.
    """

    def __init__(
        self,
        listener: Listener,
        *,
        on_progress: PollProgressCallback | None = None,
    ) -> None:
        config = listeners_registry.load_config(listener)
        if not isinstance(config, SemanticListenerConfig):
            raise NotImplementedError(f"Unsupported listener kind: {listener.kind}")

        self.listener = listener
        self.config = config
        self.account_id = str(listener.account_id)
        self.is_instant = listener.delivery_mode == Listener.DeliveryMode.INSTANT
        # No-op default so the per-obs callsite stays unconditional.
        self.on_progress: PollProgressCallback = on_progress or (lambda _: None)

    @cached_property
    def listener_svc(self) -> ListenerService:
        return ListenerService(account_id=self.account_id)

    @cached_property
    def event_svc(self) -> EventService:
        return EventService(account_id=self.account_id)

    @cached_property
    def delivery_svc(self) -> DeliveryService:
        return DeliveryService(account_id=self.account_id)

    @cached_property
    def engine(self) -> Engine:
        return engine_registry.get(self.config.engine.kind)

    def run(self) -> PollResult:
        """Execute one polling cycle for this Listener."""
        started_at = timezone.now()
        observed = 0
        hits = 0

        if self.is_instant and self.config.notifiers:
            self._retry_stuck_pending()

        for watch in self.config.streams:
            try:
                s_observed, s_hits = self._poll_stream(watch)
                observed += s_observed
                hits += s_hits
            except _RECOVERABLE_ERRORS as exc:
                logger.warning(
                    "stream polling failed listener=%s spec=%s err=%s: %s",
                    self.listener.id,
                    watch.spec.display(),
                    type(exc).__name__,
                    exc,
                )

        self.listener_svc.update_poll_state(
            self.listener,
            last_polled_at=started_at,
            data=self.config.model_dump(mode="json"),
        )
        return PollResult(observed=observed, hits=hits)

    def _retry_stuck_pending(self) -> None:
        """Re-fire instant delivery for any Event left at delivered_at=NULL by a
        previous failed cycle. Instant listeners don't run the digest sweep, so
        without this they'd stay stuck forever. Rides the existing poll cadence."""
        for stuck in self.event_svc.list_pending_for_listener(
            listener_id=str(self.listener.id)
        ):
            try:
                obs = hydrate_event(stuck)
                self.delivery_svc.deliver_instant(
                    stuck, obs, self.listener, self.config
                )
            except _RECOVERABLE_ERRORS as exc:
                logger.warning(
                    "stuck-pending retry failed listener=%s event=%s err=%s: %s",
                    self.listener.id,
                    stuck.id,
                    type(exc).__name__,
                    exc,
                )

    def _poll_stream(self, watch: StreamWatch) -> tuple[int, int]:
        """Poll one stream attached to this Listener. Returns (observed, hits)."""
        connector = source_registry.get(watch.spec.kind)

        if watch.last_event_at is None:
            # Forward-looking default: snapshot the watermark to "now"
            # and yield nothing. Operators who want backfill set
            # `last_event_at = now - timedelta(days=N)` at listener-
            # create time so this branch doesn't fire for them. Keeps
            # cold-starts free of surprise multi-hour LLM bills.
            watch.last_event_at = timezone.now()
            self.on_progress(
                StreamStarted(
                    listener=self.listener,
                    stream_kind=str(watch.spec.kind),
                    stream_display=watch.spec.display(),
                    expected_max=0,
                )
            )
            return 0, 0

        # Warm path. Cheap pre-count so progress UIs can render N/total
        # + ETA. Same network walk as `poll`, just skipping Observation
        # construction; the bandwidth doubles for the poll cycle but is
        # negligible relative to the per-judgment LLM time the count
        # makes visible.
        expected_max = connector.count(
            watch.spec, self.listener, since=watch.last_event_at
        )
        self.on_progress(
            StreamStarted(
                listener=self.listener,
                stream_kind=str(watch.spec.kind),
                stream_display=watch.spec.display(),
                expected_max=expected_max,
            )
        )
        observed = 0
        hits = 0
        for obs in connector.poll(watch.spec, self.listener, since=watch.last_event_at):
            observed += 1
            # Advance the watermark BEFORE per-obs processing so a failure here
            # doesn't trap us re-processing the same observation forever.
            if watch.last_event_at is None or obs.occurred_at > watch.last_event_at:
                watch.last_event_at = obs.occurred_at

            try:
                if self._process_observation(obs):
                    hits += 1
            except _RECOVERABLE_ERRORS as exc:
                logger.warning(
                    "observation processing failed listener=%s source=%s external_id=%s err=%s: %s",
                    self.listener.id,
                    obs.source,
                    obs.external_id,
                    type(exc).__name__,
                    exc,
                )
                self.on_progress(
                    JudgeProgress(
                        listener=self.listener,
                        obs=obs,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
        return observed, hits

    def _process_observation(self, obs: Observation) -> bool:
        """Judge, persist, and (if instant) deliver one observation.
        Returns True if a new Event was persisted (counts as a hit)."""
        result = self.engine.judge(obs, self.listener)
        is_hit = result.score >= self.config.hit_threshold
        # Emit progress before persistence so the caller's printer can
        # render the slow leg's outcome immediately, regardless of
        # whether persist_hit dedups or instant delivery succeeds.
        self.on_progress(
            JudgeProgress(
                listener=self.listener,
                obs=obs,
                score=result.score,
                hit=is_hit,
            )
        )
        if not is_hit:
            return False
        event = self.event_svc.persist_hit(obs, self.listener)
        if event is None:
            # Already persisted (dedup), nothing to notify.
            return False
        if self.is_instant and self.config.notifiers:
            self.delivery_svc.deliver_instant(event, obs, self.listener, self.config)
        return True


def poll_listener(
    listener: Listener,
    *,
    on_progress: PollProgressCallback | None = None,
) -> PollResult | None:
    """Locked entry point for a single Listener's poll cycle.

    Acquires `poll_lock(listener.id)`; if another process already holds it,
    returns None so callers can record a skip. On success returns the
    `PollResult` from `PollListenerOperation(listener).run()`.

    `on_progress`, if given, is invoked once per observation with a
    `JudgeProgress` carrying the listener, observation, score, and
    hit flag. Use it to render live progress in a command / CLI.

    Tests and ad-hoc debug paths that *want* to bypass the lock should call
    `PollListenerOperation(listener).run()` directly.
    """
    with poll_lock(str(listener.id)) as acquired:
        if not acquired:
            return None
        return PollListenerOperation(listener, on_progress=on_progress).run()

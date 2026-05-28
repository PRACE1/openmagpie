"""Dataclasses + callback type for judge-cycle progress events.

`JudgeCycleStarted` fires once at the top of a cycle that has work to
do; `JudgeItemDone` fires per item (success, recoverable error, or
un-hydrateable). Renderers (the CLI's `judge_listener` mgmt command,
test harnesses, etc.) wire one `on_progress` callback that handles both.
"""

from collections.abc import Callable
from dataclasses import dataclass

from events.observations import Observation
from listeners.models import Listener


@dataclass(frozen=True)
class JudgeResult:
    judged: int
    hits: int


@dataclass(frozen=True)
class JudgeCycleStarted:
    """Fired once at the top of a cycle that has work to do, AFTER the
    cursor/latest snapshot is taken. `pending` is the exact item count
    the loop will iterate. `est_seconds` is `pending * (per-listener
    EWMA of recent judge latency)`, so a cycle on a slow model or busy
    host shows a realistic ETA rather than the seed-default 2s/item.
    Not fired when the snapshot has no new items; that's the cheap
    empty-cycle path."""

    listener: Listener
    pending: int
    est_seconds: int


@dataclass(frozen=True)
class JudgeItemDone:
    """Per-item progress signal. The engine is the slow leg (multi-second
    LLM call per item), so callers wanting live feedback wire an
    `on_progress` callback. Fires on success AND on per-item failures
    (un-hydrateable observation, recoverable engine/connector error)
    so the operator sees errors in the live console, not just in logs.

    `error` is set on failures (with `obs` None for an un-hydrateable
    item, populated otherwise); `score`/`hit` are None on errors.
    `external_id` is the FeedItem's denormalized source id, populated
    on every event so the error path has a render-able identity even
    when `obs` couldn't be hydrated.

    `latency_ms` is the engine's measured judge time for THIS item
    (0 on error). `done` / `total` / `eta_seconds` are the running
    cycle stats AFTER this item, with `eta_seconds` computed from the
    in-cycle mean latency (more accurate than the cross-cycle EWMA
    once a couple items have actually landed)."""

    listener: Listener
    external_id: str = ""
    obs: Observation | None = None
    score: float | None = None
    hit: bool = False
    error: str | None = None
    latency_ms: int = 0
    done: int = 0
    total: int = 0
    eta_seconds: int = 0


JudgeEvent = JudgeCycleStarted | JudgeItemDone
JudgeProgressCallback = Callable[[JudgeEvent], None]

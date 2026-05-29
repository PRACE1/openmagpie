"""Judgment orchestrator: a Listener judges new FeedItems with its engine.

The Feed polls and persists every item; the Listener is an attention over
that Feed. This drives the listener leg: read FeedItems the listener
hasn't judged yet (id > its cursor) across every source in the Feed,
judge each with the engine, and on a hit persist an Event (kind="hit")
and (for instant-mode listeners) fire the notifier.

A per-listener cursor (`Listener.last_judged_item_id`, a ULID) is what
keeps misses from being re-judged: items are processed in id order and the
cursor advances to the snapshot max each cycle. Judgment has no cadence of
its own; it rides the Feed's poll cadence (new items appear when the Feed
polls); a cycle with no new items is a cheap cursor query, no LLM calls.

Each `judge_listener` cycle starts with a stuck-pending retry for
instant-mode listeners (re-fire delivery for any hit left undelivered by a
prior failed cycle). Per-item failures are isolated so one bad payload or
a transient engine/webhook error can't abort the whole cycle.

Package layout:
  _events.py     dataclasses for the on_progress event stream + JudgeResult
  _eta.py        per-listener latency EWMA + in-cycle ETA math
  _cursor.py     batched durable cursor saves (Ctrl-C / SIGINT-safe)
  _operation.py  JudgeListenerOperation + judge_listener entry point
"""

from ._events import (
    JudgeCycleStarted,
    JudgeEvent,
    JudgeItemDone,
    JudgeProgressCallback,
    JudgeResult,
)
from ._operation import JudgeListenerOperation, judge_listener

__all__ = [
    "JudgeCycleStarted",
    "JudgeEvent",
    "JudgeItemDone",
    "JudgeListenerOperation",
    "JudgeProgressCallback",
    "JudgeResult",
    "judge_listener",
]

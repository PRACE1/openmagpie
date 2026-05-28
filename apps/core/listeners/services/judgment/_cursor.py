"""_CursorSaver: batched, durable cursor advancement.

`JudgeListenerOperation.run()` calls `advance(item_id)` after each
successful judge; `_CursorSaver` writes back to the DB on every Nth
advance and flushes in `run()`'s `finally` block. So Ctrl-C, SIGINT,
recoverable errors, and natural completion all converge through one
save point and a mid-cycle interrupt keeps the work it has already
done (lose at most `every - 1` items of re-judge work next cycle).
"""

from listeners.models import Listener

from ..listeners import ListenerService

# How often (in items advanced) `_CursorSaver` writes back to the DB.
# Keeps progress durable through Ctrl-C / SIGINT / crash without
# write-amplifying on every item. At 10, a 50-item cycle persists 5
# times; a Ctrl-C loses at most 9 items of re-judge work.
_CURSOR_SAVE_EVERY = 10


class _CursorSaver:
    """Batches `Listener.last_judged_item_id` writes so an interrupted
    cycle keeps the work it has already done.

    `advance(item_id)` records the new high-water mark and persists it
    every `every` calls. `flush()` writes whatever is pending and is
    called from `run()`'s `finally` block so Ctrl-C, recoverable
    errors, and natural cycle completion all converge through one
    save point.

    Idempotent: re-calling `advance` with the same id or `flush` with
    no pending advance is a no-op. The wrapper compares against the
    listener's in-memory `last_judged_item_id` before issuing the
    UPDATE, so a no-op cycle (no items judged) doesn't touch the row.
    """

    def __init__(self, svc: ListenerService, listener: Listener, *, every: int = _CURSOR_SAVE_EVERY) -> None:
        self.svc = svc
        self.listener = listener
        self.every = max(1, every)
        self._pending_id: str | None = None
        self._since_flush = 0

    def advance(self, item_id: str) -> None:
        """Record that `item_id` is the new high-water mark. Persists
        if we've accumulated `every` advances since the last save."""
        if not item_id or item_id == self._pending_id:
            return
        self._pending_id = item_id
        self._since_flush += 1
        if self._since_flush >= self.every:
            self.flush()

    def flush(self) -> None:
        """Persist the pending high-water mark if anything's unsaved.
        Safe to call multiple times; safe to call with no pending work."""
        if self._pending_id is None or self._since_flush == 0:
            return
        if self._pending_id == (self.listener.last_judged_item_id or ""):
            self._since_flush = 0
            return
        self.svc.advance_judge_cursor(self.listener, item_id=self._pending_id)
        self._since_flush = 0

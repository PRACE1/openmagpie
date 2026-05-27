"""listeners.services, public surface.

from listeners.services import ListenerService, judge_listener

# Account-scoped operations:
svc = ListenerService(account_id=X)
listener = svc.get(id)
svc.advance_judge_cursor(listener, item_id=...)

# Cross-tenant operations (judgment scheduler):
for listener in ListenerService.Global.list_active():
    ...

# Judgment orchestrator (one-shot operation):
result = JudgeListenerOperation(listener).run()
# Or the equivalent function-shaped wrapper (locked):
result = judge_listener(listener)
"""

from .judgment import (
    JudgeListenerOperation,
    JudgeProgress,
    JudgeProgressCallback,
    JudgeResult,
    judge_listener,
)
from .listeners import ListenerService, SeedCursor

__all__ = [
    "JudgeListenerOperation",
    "JudgeProgress",
    "JudgeProgressCallback",
    "JudgeResult",
    "ListenerService",
    "SeedCursor",
    "judge_listener",
]

"""listeners.services, public surface.

from listeners.services import ListenerService, poll_listener

# Account-scoped operations:
svc = ListenerService(account_id=X)
listener = svc.get(id)
svc.update_poll_state(listener, last_polled_at=now, data=...)

# Cross-tenant operations (scheduler):
for listener in ListenerService.Global.list_due_for_poll(now=now):
    ...

# Orchestrator (one-shot operation):
result = PollListenerOperation(listener).run()
# Or the equivalent function-shaped wrapper:
result = poll_listener(listener)
"""

from .listeners import ListenerService
from .polling import PollListenerOperation, PollResult, poll_listener

__all__ = ["ListenerService", "PollListenerOperation", "PollResult", "poll_listener"]

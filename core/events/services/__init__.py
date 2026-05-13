"""events.services, public surface.

from events.services import EventService
svc = EventService(account_id=X)
svc.persist_hit(observation, listener)
"""

from .events import EventService

__all__ = ["EventService"]

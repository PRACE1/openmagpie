"""events.services, public surface.

from events.services import EventService, EventKind
svc = EventService(account_id=X)
svc.persist(feed_item, listener, kind=EventKind.HIT, score=...)
"""

from events.models import EventKind

from .events import EventService

__all__ = ["EventKind", "EventService"]

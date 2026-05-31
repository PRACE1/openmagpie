"""Read-only listing of hits (kind="hit" Events) for one Listener.

Separate from the judgment service (which writes hits) so the read path
doesn't drag in the engine ; mirrors `feeds.services.feeds._service.list`
in shape: account-scoped, cursor-paginated by ULID pk, newest first."""

from __future__ import annotations

from events.models.event import Event, EventKind
from listeners.models import Listener


def list_hits(listener: Listener, *, after: str | None = None, limit: int = 50) -> list[Event]:
    """Return hits for `listener`, newest first.

    Cursor: `after=<event_id>` returns rows with id strictly less than
    that (ULIDs sort by creation time, so "less than" = "older than").
    The caller is responsible for having already verified the listener
    belongs to the right account ; the query still scopes by both
    `account_id` and `listener_id` for defense in depth.
    """
    qs = Event.objects.filter(
        kind=EventKind.HIT,
        account_id=listener.account_id,
        listener_id=str(listener.id),
    )
    if after:
        qs = qs.filter(id__lt=after)
    return list(qs.order_by("-id")[:limit])

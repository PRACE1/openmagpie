"""Observation registry: (source, EVENT_KIND) → Observation subclass.

Connectors register their observation classes at import time so that we can
hydrate `Event.data` back into a typed Observation when needed.
"""

from events.models import Event
from events.observations import Observation

_REGISTRY: dict[tuple[str, str], type[Observation]] = {}


def register(source: str, observation_classes: list[type[Observation]]) -> None:
    for cls in observation_classes:
        _REGISTRY[(source, cls.EVENT_KIND)] = cls


def hydrate(event: Event) -> Observation:
    """Reconstruct a typed Observation from an Event's stored data."""
    cls = _REGISTRY[(str(event.source), str(event.kind))]
    return cls.model_validate(event.data)

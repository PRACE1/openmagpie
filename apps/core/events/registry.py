"""Observation registry: (source, EVENT_KIND) → Observation subclass.

Connectors register their observation classes at import time so we can
hydrate a stored Observation dump (FeedItem.data, or an Event's snapshot)
back into a typed Observation when needed.
"""

from pydantic import ValidationError as _PydanticValidationError

from events.models import Event
from events.observations import Observation

_REGISTRY: dict[tuple[str, str], type[Observation]] = {}


class UnhydrateableObservation(Exception):
    """Permanent failure to reconstruct an Observation from a stored dump.

    The judgment loop and digest path advance the cursor / mark the row
    so they don't loop on the same poison item every cycle. Subclasses
    distinguish the cause for forensics — operators read the log.
    """


class UnknownObservationKind(UnhydrateableObservation):
    """The dump's `(source, kind)` pair isn't in the registry. A connector
    was renamed or removed; the old pair can't be reconstructed."""


class InvalidObservationData(UnhydrateableObservation):
    """The dump's class IS registered but `model_validate` rejects it.
    Common cause: schema drift — a new required field was added, or a
    field type changed, between when the row was written and now. The
    row can't be retried into compliance; it's permanently bad."""


def register(source: str, observation_classes: list[type[Observation]]) -> None:
    for cls in observation_classes:
        _REGISTRY[(source, cls.EVENT_KIND)] = cls


def hydrate_data(data: dict) -> Observation:
    """Reconstruct a typed Observation from a stored Observation dump.

    Reads `source` + `kind` from the dump itself (the Observation carries
    both), so this works for any persisted snapshot — a FeedItem's data or
    an Event's data snapshot.

    Raises `UnhydrateableObservation` (or one of its subclasses) on any
    permanent failure: an unknown `(source, kind)` pair, or a known pair
    whose data fails the typed model's validation. Both signal "skip this
    row forever; retrying can't help."
    """
    source = str(data.get("source"))
    kind = str(data.get("kind"))
    try:
        cls = _REGISTRY[(source, kind)]
    except KeyError as exc:
        raise UnknownObservationKind(f"no Observation class registered for (source={source!r}, kind={kind!r})") from exc
    try:
        return cls.model_validate(data)
    except _PydanticValidationError as exc:
        raise InvalidObservationData(
            f"data fails {cls.__name__}.model_validate: {exc.error_count()} error(s); first: {exc.errors()[0]}"
        ) from exc


def hydrate(event: Event) -> Observation:
    """Reconstruct a typed Observation from an Event's data snapshot."""
    return hydrate_data(event.data)

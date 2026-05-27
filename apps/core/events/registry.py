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
    """Register concrete Observation classes for a source kind.

    Enforces the `sample()` override here (not at class-definition time
    via `__init_subclass__`) so connector authors can declare abstract
    intermediate bases (e.g. `RedditObservationBase` for shared fields
    across post/comment Observations) without tripping the guard at
    import. Only classes that actually get registered are required to
    have a real `sample()`; intermediates pass through untouched.
    """
    for cls in observation_classes:
        if "sample" not in cls.__dict__:
            raise TypeError(
                f"{cls.__name__} is registered but does not override Observation.sample() — "
                "payload-sample would 500 on listeners using this kind. Implement sample()."
            )
        _REGISTRY[(source, cls.EVENT_KIND)] = cls


def class_for_source(source: str) -> type[Observation] | None:
    """First registered Observation class for the given source-connector
    kind (e.g. `"reddit_subreddit"`). None if nothing is registered for
    that source. When a source has multiple event kinds (e.g. future:
    `new_post` + `new_comment`), returns the first registered.

    TODO: ambiguous when a source ships multiple event kinds — payload-
    sample would render whichever was registered first regardless of
    the listener's actual kind. No triggering case today (one kind per
    source); revisit when a second is added (needs a design decision:
    take an explicit kind hint, or expose the choice via feed spec)."""
    for (registered_source, _kind), cls in _REGISTRY.items():
        if registered_source == source:
            return cls
    return None


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

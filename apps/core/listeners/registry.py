"""Listener kind → typed config class.

`Listener.data` is a JSON blob whose schema depends on `Listener.kind`. This
registry maps the kind string to its Pydantic config class for validation.
"""

from listeners.configs import ListenerConfig, SemanticListenerConfig
from listeners.models import Listener
from listeners.policy import enforce_policy

_REGISTRY: dict[str, type[ListenerConfig]] = {
    SemanticListenerConfig.LISTENER_KIND: SemanticListenerConfig,
}


def get_config_class(kind: str) -> type[ListenerConfig]:
    return _REGISTRY[kind]


def parse_config(kind: str, data: dict) -> ListenerConfig:
    """kind + raw dict -> typed config, SHAPE ONLY (no policy).

    The ONE place `get_config_class(...).model_validate(...)` is called -
    every other raw->typed path composes from here, so config classes are
    never reached into directly outside this module. Use this (not
    `validate_config`) when policy must run on a later-derived object,
    e.g. `build_update` merges submitted+prior then enforces on the
    MERGE OUTPUT.

    Raises PydanticValidationError on a shape violation.
    """
    return get_config_class(kind).model_validate(data)


def validate_config(kind: str, data: dict) -> ListenerConfig:
    """Untrusted input -> policy-safe typed config: `parse_config` (shape)
    + enforce_policy (the server-only Django/settings guards) fused so a
    callsite can't do the first and forget the second - the failure mode
    the shape / policy split otherwise reintroduced.

    For WRITES where the returned object is exactly what persists
    (create). Symmetric with `load_config` (read-path twin).

    Raises PydanticValidationError (shape) or PolicyError (policy);
    callers map each to the appropriate 400.
    """
    return enforce_policy(parse_config(kind, data))


def load_config(listener: Listener) -> ListenerConfig:
    """At-rest listener -> typed config, shape only (see `parse_config`).

    No policy: stored data is already normalized, and re-enforcing under
    possibly-changed settings (e.g. WEBHOOK_REQUIRE_HTTPS flipped on
    after creation) could spuriously fail a pure read."""
    return parse_config(str(listener.kind), listener.data or {})

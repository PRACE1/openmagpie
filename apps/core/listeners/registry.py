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


def validate_config(kind: str, data: dict) -> ListenerConfig:
    """The single raw-dict -> policy-safe typed config path for WRITES.

    model_validate (shape, from the shared pure model) + enforce_policy
    (the server-only Django/settings guards) fused so a callsite can't
    do the first and forget the second - the failure mode the shape /
    policy split otherwise reintroduced. Symmetric with `load_config`
    (the read-path twin).

    Read path is deliberately NOT this: stored `data` is already
    normalized, so re-running policy would re-fill engine kind and
    re-check already-past watermarks for no gain.

    Raises PydanticValidationError (shape) or PolicyError (policy);
    callers map each to the appropriate 400.
    """
    return enforce_policy(get_config_class(kind).model_validate(data))


def load_config(listener: Listener) -> ListenerConfig:
    """Validate listener.data against the kind's Pydantic config class.

    Read path: shape only, no policy (see `validate_config`)."""
    return get_config_class(str(listener.kind)).model_validate(listener.data)

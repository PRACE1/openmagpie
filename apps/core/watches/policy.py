"""Server-side WatchAction-config POLICY (the Django/settings-coupled
guards that can't live in the pure shared `openmagpie-schema` package).

The watches analogue of `feeds.policy`. `enforce_action_policy` runs at
the write validation seam (serializer -> 400 ; service). Idempotent; pure
predicates over the already-shape-valid config.

Guards today:
  - a semantic_filter's `engine.kind`, when pinned, must be a registered
    engine (else the run 500s mid-poll instead of failing at create).
"""

from django.conf import settings

from engine import registry as engine_registry
from openmagpie_schema.watch_actions import SemanticFilterConfig, WatchActionConfigBase


class PolicyError(ValueError):
    """A watch-action config violates server policy. Mapped to a 400 at
    the HTTP boundary; the message is operator-facing."""


def enforce_action_policy(config: WatchActionConfigBase) -> WatchActionConfigBase:
    """Apply every server policy guard on the action config; return it or
    raise `PolicyError`. Idempotent. Dispatches per kind ; kinds with no
    settings-coupled policy pass straight through."""
    if isinstance(config, SemanticFilterConfig):
        _enforce_engine_registered(config)
    return config


def _enforce_engine_registered(config: SemanticFilterConfig) -> None:
    """A pinned `engine.kind` must be registered in this deployment.

    Empty kind means "use the server default" (resolved at judge time
    from settings.ENGINE_DEFAULT_KIND), so only a non-empty pin is
    checked here. Rejecting at the write boundary means the operator sees
    a clean 400 naming the bad kind + the available set, instead of every
    judge cycle 500ing on an unknown engine."""
    kind = config.engine.kind
    if not kind:
        # Defaulted at runtime ; the default itself is a deploy invariant
        # (settings + engine.registry), not operator input, so not gated here.
        return
    try:
        engine_registry.get(kind)
    except KeyError:
        raise PolicyError(
            f"unknown engine kind {kind!r}; registered: {engine_registry.kinds() or '(none)'} "
            f"(default is {settings.ENGINE_DEFAULT_KIND!r}; leave engine.kind empty to use it)"
        ) from None

"""Action-implementation registry: kind -> runnable `Action` instance.

The EXECUTION-layer registry, distinct from `watches.registry` (the
CONFIG-layer kind -> Pydantic config class). The drain looks up the impl
for a run's `action.kind` here and calls `.run(...)`. New action kinds
(webhook, log in commit 7) register their impl here ; same shape as
`engine.registry` / `sources.registry`.
"""

from .protocol import Action
from .semantic_filter import SemanticFilterAction

_REGISTRY: dict[str, Action] = {
    SemanticFilterAction.kind: SemanticFilterAction(),
}


def get(kind: str) -> Action:
    """The runnable Action for `kind`. Raises KeyError if no impl is
    registered (a kind that validates as config but has no executor yet ;
    the drain treats that as a permanent ERROR on the run)."""
    return _REGISTRY[kind]


def register(action: Action) -> None:
    _REGISTRY[action.kind] = action

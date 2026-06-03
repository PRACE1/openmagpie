"""Shared config loading for the delivery action impls.

`load_typed` is the ONE place the "invalid config -> the run ERRORS"
contract lives: load the action's typed config, and on a ValidationError
log the raw cause (keyed by action id, never surfaced) and return None so
the caller emits ERRORED. The kind registry guarantees the loaded type ;
it's asserted here. Hoisted out of the per-impl `_load` copies (webhook,
log) so the contract can't drift between kinds.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from openmagpie_schema.watch_actions import WatchActionConfigBase
from watches.models import WatchAction
from watches.registry import load_config

logger = logging.getLogger("watches")


def load_typed[T: WatchActionConfigBase](action: WatchAction, expected: type[T], *, log_label: str) -> T | None:
    """Load `action`'s config as `expected`, or None if it no longer
    validates (logged under `log_label` ; the caller then ERRORS the run)."""
    try:
        config = load_config(action)
    except ValidationError as exc:
        logger.exception("%s: invalid config for action=%s: %s", log_label, action.id, exc)
        return None
    assert isinstance(config, expected)  # registry guarantees by kind
    return config

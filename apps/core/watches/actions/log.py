"""LogAction: write one feed item to the server log (kind=`log`).

The simplest delivery: render a one-line summary under the config's
`prefix` and log it. Naturally idempotent (re-logging on a retry is
harmless), so it always SUCCEEDS unless its own config is invalid. Ports
the v1 log notifier into the per-item v2 action interface.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from openmagpie_schema.watch_actions import LogConfig, LogResult
from openmagpie_schema.watch_enums import WatchActionKind, WatchActionRunState
from watches import run_messages
from watches.models import WatchAction
from watches.registry import load_config

from .protocol import ActionOutcome

logger = logging.getLogger("watches")


class LogAction:
    """Logs one item under the configured prefix ; always SUCCEEDS."""

    kind = WatchActionKind.LOG.value

    def run(self, action: WatchAction, *, item_data: dict) -> ActionOutcome:
        try:
            config = load_config(action)
        except ValidationError as exc:
            logger.exception("log: invalid config for action=%s: %s", action.id, exc)
            return ActionOutcome(state=WatchActionRunState.ERRORED, error=run_messages.CONFIG_INVALID)
        assert isinstance(config, LogConfig)  # registry guarantees by kind

        line = _render(config, item_data)
        logger.info(line)
        return ActionOutcome(
            state=WatchActionRunState.SUCCEEDED,
            result=LogResult(rendered=line).model_dump(mode="json"),
        )


def _render(config: LogConfig, item_data: dict) -> str:
    """One-line item summary under the prefix. `include_fields` whitelists
    what to show; empty = all (the same meaning as webhook). Empty values
    are skipped so the line stays tight."""
    fields = config.include_fields or list(item_data)
    shown = " | ".join(f"{f}={item_data[f]}" for f in fields if item_data.get(f))
    return f"{config.prefix} {shown}".rstrip()

"""LogAction: write one feed item to the server log (kind=`log`).

The simplest delivery: render a one-line summary under the config's
`prefix` and log it. Naturally idempotent (re-logging on a retry is
harmless), so it always SUCCEEDS unless its own config is invalid. Ports
the v1 log notifier into the per-item v2 action interface.
"""

from __future__ import annotations

import logging

from openmagpie_schema.watch_actions import LogConfig, LogResult
from openmagpie_schema.watch_enums import WatchActionKind, WatchActionRunState
from watches import run_messages
from watches.models import WatchAction

from ._config import load_typed
from .protocol import ActionOutcome

logger = logging.getLogger("watches")


class LogAction:
    """Logs one item under the configured prefix ; always SUCCEEDS."""

    kind = WatchActionKind.LOG.value

    def run(self, action: WatchAction, *, item_data: dict) -> ActionOutcome:
        config = load_typed(action, LogConfig, log_label="log")
        if config is None:
            return ActionOutcome(state=WatchActionRunState.ERRORED, error=run_messages.CONFIG_INVALID)
        line = _render(config, item_data)
        logger.info(line)
        return ActionOutcome(
            state=WatchActionRunState.SUCCEEDED, result=LogResult(rendered=line).model_dump(mode="json")
        )

    def run_batch(self, action: WatchAction, *, items: list[dict]) -> ActionOutcome:
        config = load_typed(action, LogConfig, log_label="log")
        if config is None:
            return ActionOutcome(state=WatchActionRunState.ERRORED, error=run_messages.CONFIG_INVALID)
        lines = [f"{_render(config, item)} ({i + 1}/{len(items)})" for i, item in enumerate(items)]
        rendered = f"{config.prefix} digest of {len(items)}:\n" + "\n".join(lines)
        logger.info(rendered)
        return ActionOutcome(
            state=WatchActionRunState.SUCCEEDED, result=LogResult(rendered=rendered).model_dump(mode="json")
        )


def _render(config: LogConfig, item_data: dict) -> str:
    """One-line item summary under the prefix. `include_fields` whitelists
    what to show; empty = all (the same meaning as webhook). Empty values
    are skipped so the line stays tight."""
    fields = config.include_fields or list(item_data)
    shown = " | ".join(f"{f}={item_data[f]}" for f in fields if item_data.get(f))
    return f"{config.prefix} {shown}".rstrip()

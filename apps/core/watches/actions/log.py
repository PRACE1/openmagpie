"""LogAction: write feed items to the server log (kind=`log`).

The simplest delivery: render a one-line summary per item under the config's
`prefix` and log it (instant = one line, digest = a numbered block). Naturally
idempotent (re-logging on a retry is harmless), so it always SUCCEEDS unless
its own config is invalid. Makes no HTTP call, so it returns a plain
`ActionResult` (not an `OutboundActionResult`) and records no delivery row.
"""

from __future__ import annotations

import logging

from openmagpie_schema.watch_actions import LogConfig, LogResult
from openmagpie_schema.watch_enums import WatchActionKind, WatchActionRunState
from watches import run_messages
from watches.models import WatchAction

from ._config import load_typed
from .protocol import Action, ActionContext, ActionItem, ActionResult

logger = logging.getLogger("watches")


class LogAction(Action):
    """Logs items under the configured prefix ; always SUCCEEDS. Instant logs
    one line, digest a numbered block."""

    kind = WatchActionKind.LOG.value

    def run(self, action: WatchAction, *, items: list[ActionItem], context: ActionContext) -> ActionResult:
        config = load_typed(action, LogConfig, log_label="log")
        if config is None:
            return ActionResult(state=WatchActionRunState.ERRORED, error=run_messages.CONFIG_INVALID)
        if len(items) == 1:
            rendered = _render(config, items[0].data)
        else:
            lines = [f"{_render(config, it.data)} ({i + 1}/{len(items)})" for i, it in enumerate(items)]
            rendered = f"{config.prefix} digest of {len(items)}:\n" + "\n".join(lines)
        logger.info(rendered)
        # No HTTP call -> a plain ActionResult, so no WatchActionDelivery row.
        return ActionResult(
            state=WatchActionRunState.SUCCEEDED, result=LogResult(rendered=rendered).model_dump(mode="json")
        )


def _render(config: LogConfig, item_data: dict) -> str:
    """One-line item summary under the prefix. `include_fields` whitelists
    what to show; empty = all (the same meaning as webhook). Empty values
    are skipped so the line stays tight."""
    fields = config.include_fields or list(item_data)
    shown = " | ".join(f"{f}={item_data[f]}" for f in fields if item_data.get(f))
    return f"{config.prefix} {shown}".rstrip()

"""`magpie listener get` — show one listener."""

import typer

from ... import console
from ...context import app_ctx
from .._shared import _handle_api_errors
from . import listener_app
from ._helpers import _print_listener


@listener_app.command("get")
@_handle_api_errors
def get(listener_id: str = typer.Argument(..., help="Listener id.")) -> None:
    """Show one listener in the caller's account."""
    ac = app_ctx()
    detail = ac.api.listener.get(listener_id)
    _print_listener(detail, f"Listener {detail.id}  [{console.active_or_paused(detail.is_active)}]")
    if detail.last_judged_item_id:
        console.kv("last judged item", detail.last_judged_item_id)

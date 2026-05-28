"""`magpie listener list` — one line per listener, pipe-delimited."""

from ... import console
from ...context import app_ctx
from .._shared import _handle_api_errors
from . import listener_app


@listener_app.command("list")
@_handle_api_errors
def list_() -> None:
    """List listeners in the caller's account."""
    ac = app_ctx()
    items = ac.api.listener.list()

    if not items:
        console.log("No listeners yet. Try `magpie listener template`.")
        return

    # Compact one-line-per-listener output. Pipe-delimited fields per
    # repo convention. id last because it's the longest, lets the
    # interesting fields (name, kind, mode, active) sit left-aligned.
    for it in items:
        rate = console.rate(it.recent_hits, it.recent_items)
        console.log(
            f"  {it.name} | {it.kind} | {it.delivery_mode} | {console.active_or_paused(it.is_active)} | hit {rate} | {it.id}"
        )

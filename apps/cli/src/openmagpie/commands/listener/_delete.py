"""`magpie listener delete` — destructive, single-listener removal."""

import sys

import typer

from ... import console
from ...context import app_ctx
from .._shared import _handle_api_errors
from . import listener_app


@listener_app.command("delete")
@_handle_api_errors
def delete(
    listener_id: str = typer.Argument(..., help="Listener id."),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the confirmation prompt. Required for non-interactive input.",
    ),
) -> None:
    """Delete one listener. Destructive and not reversible."""
    ac = app_ctx()
    detail = ac.api.listener.get(listener_id)

    if not yes:
        if not sys.stdin.isatty():
            console.warn(f"Piped input: can't prompt. Re-run with --yes to delete {detail.name} ({detail.id}).")
            raise typer.Exit(code=1)
        console.error(f"Delete listener {detail.name} ({detail.id})? This cannot be undone.")
        if not typer.confirm("Delete?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)

    ac.api.listener.delete(listener_id)
    console.success(f"Deleted listener {detail.name} ({detail.id})")

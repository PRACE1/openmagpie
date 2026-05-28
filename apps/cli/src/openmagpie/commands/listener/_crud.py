"""`magpie listener get | edit | delete` — single-listener CRUD."""

import sys

import typer
import yaml

from ... import console
from ...context import app_ctx
from .._shared import _handle_api_errors, _open_editor_or_abort, _read_file_or_abort
from . import listener_app
from ._helpers import _edit_seed, _parse_yaml_or_abort, _print_listener, _run_mutation


@listener_app.command("get")
@_handle_api_errors
def get(listener_id: str = typer.Argument(..., help="Listener id.")) -> None:
    """Show one listener in the caller's account."""
    ac = app_ctx()
    detail = ac.api.listener.get(listener_id)
    _print_listener(detail, f"Listener {detail.id}  [{console.active_or_paused(detail.is_active)}]")
    if detail.last_judged_item_id:
        console.kv("last judged item", detail.last_judged_item_id)


@listener_app.command("edit")
@_handle_api_errors
def edit(
    listener_id: str = typer.Argument(..., help="Listener id."),
    file: str | None = typer.Option(
        None,
        "--file",
        "-f",
        help=("YAML to apply (use '-' for stdin). Omit to edit the current config in $EDITOR."),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Validate the edit server-side and show what would change, then stop.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the confirmation prompt. Required for non-interactive input.",
    ),
) -> None:
    """Full-replace edit of one listener.

    Fetches the current (redacted) config, opens it in $EDITOR (or takes
    `-f`), then runs the same validate -> preview -> confirm -> apply
    flow as create. `kind` is server-immutable. Watermarks and any
    secret left as `***` are preserved server-side.
    """
    ac = app_ctx()
    detail = ac.api.listener.get(listener_id)
    seed = yaml.safe_dump(_edit_seed(detail).model_dump(mode="json"), sort_keys=False)

    if file is None:
        body_text = _open_editor_or_abort(seed)
    elif file == "-":
        body_text = sys.stdin.read()
    else:
        body_text = _read_file_or_abort(file)

    body = _parse_yaml_or_abort(body_text)
    _run_mutation(ac, body, listener_id=listener_id, dry_run=dry_run, yes=yes)


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

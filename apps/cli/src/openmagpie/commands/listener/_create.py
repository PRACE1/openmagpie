"""`magpie listener create` — create a listener from a YAML config."""

import sys

import typer

from ...context import app_ctx
from .._shared import _handle_api_errors, _read_file_or_abort
from . import listener_app
from ._helpers import (
    _edit_template_or_abort,
    _parse_yaml_or_abort,
    _reject_if_unmodified_template,
    _run_mutation,
)


@listener_app.command("create")
@_handle_api_errors
def create(
    file: str | None = typer.Option(
        None,
        "--file",
        "-f",
        help=("Path to a YAML config (use '-' for stdin). Omit to edit a fresh template in $EDITOR."),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Validate server-side and show what would be created, then stop.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the confirmation prompt. Required for non-interactive input.",
    ),
    skip_existing: bool = typer.Option(
        False,
        "--skip-existing",
        help=(
            "Don't judge items already in the feed's retention window. Seeds "
            "the listener's judge cursor from the feed's newest item at "
            "create time, so only items arriving from now on get judged."
        ),
    ),
) -> None:
    """Create a listener from a YAML config.

    Validates server-side first and prints a preview of the would-be
    listener, then asks for confirmation before creating.

    Input modes:
      - `-f path/to/listener.yaml` reads the file.
      - `-f -` reads from stdin.
      - no `-f` opens $EDITOR on a fresh template.

    `--dry-run` stops after the preview (never creates). `--yes` skips
    the prompt and is required when input is piped (no TTY to prompt on).
    By default a new listener judges every item already in the feed's
    retention window; pass `--skip-existing` to start forward-only.
    """
    if file is None:
        body_text = _edit_template_or_abort()
    elif file == "-":
        body_text = sys.stdin.read()
    else:
        body_text = _read_file_or_abort(file)

    # Guard ALL input modes, not just the editor: piping the raw
    # template (`template | create -f -`) must fail here with useful
    # guidance, not sail through to a server validate + preview + an
    # unrelated "--yes" wall.
    _reject_if_unmodified_template(body_text)

    body = _parse_yaml_or_abort(body_text)
    seed_cursor = "latest" if skip_existing else None
    _run_mutation(app_ctx(), body, listener_id=None, dry_run=dry_run, yes=yes, seed_cursor=seed_cursor)

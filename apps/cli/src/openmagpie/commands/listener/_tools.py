"""`magpie listener rewind | payload-sample` — operator tools, not CRUD.

`rewind` resets the judge cursor (re-judge items in the retention
window after refining instructions / lowering the threshold).
`payload-sample` previews what each configured notifier would emit
for the next batch; same code path delivery takes, without the ship
step.
"""

import sys

import typer

from ... import console
from ...context import app_ctx
from .._shared import _handle_api_errors, _print_payload_sample
from . import listener_app


@listener_app.command("rewind")
@_handle_api_errors
def rewind(
    listener_id: str = typer.Argument(..., help="Listener id."),
    to: str | None = typer.Option(
        None,
        "--to",
        help="Rewind cursor to this ULID. Omit to rewind to the start of the retention window.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the confirmation prompt. Required for non-interactive input.",
    ),
) -> None:
    """Rewind the listener's judge cursor.

    By default (no `--to`) the cursor resets to empty; the next judge
    cycle re-judges every item still in the feed's retention window.
    Pass `--to <ULID>` to rewind to a specific point (re-judge items
    after that). Costs LLM tokens per re-judged item; confirm before
    running on a large backlog.
    """
    ac = app_ctx()
    detail = ac.api.listener.get(listener_id)
    target_desc = f"to {to}" if to else "to the start of the retention window"

    if not yes:
        if not sys.stdin.isatty():
            console.warn(f"Piped input: can't prompt. Re-run with --yes to rewind {detail.name} ({detail.id}).")
            raise typer.Exit(code=1)
        console.warn(
            f"Rewind listener {detail.name} ({detail.id}) {target_desc}? "
            "Items past the new cursor will be re-judged on the next cycle (LLM cost)."
        )
        if not typer.confirm("Rewind?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)

    updated = ac.api.listener.rewind(listener_id, to=to)
    console.success(f"Rewound listener {detail.name} ({detail.id}); cursor now {updated.last_judged_item_id!r}")


@listener_app.command("payload-sample")
@_handle_api_errors
def payload_sample(
    listener_id: str = typer.Argument(..., help="Listener id."),
    json_out: bool = typer.Option(False, "--json", help="Dump the raw API envelope as JSON."),
) -> None:
    """Show what each of this listener's notifiers WOULD emit for the
    next batch; same code path delivery takes, just without the ship
    step. One block per configured notifier; an operator wiring `webhook
    + log` sees the JSON their webhook receives alongside the text
    written to server logs for the same hits.

    Default output is human-formatted (header per notifier, payload as
    a JSON block or text block depending on notifier kind). Pass
    `--json` for the structured envelope when scripting. Pure preview;
    fires nothing.
    """
    ac = app_ctx()
    result = ac.api.listener.payload_sample(listener_id)

    if json_out:
        typer.echo(result.model_dump_json(indent=2))
        return

    _print_payload_sample(result)

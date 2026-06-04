"""`magpie watch action ...` verbs: surgical single-action chain edits.

The complement to the whole-watch YAML on `watch create`/`edit`: add or
drop one action without round-tripping the full config. Both wrap the
same server `/v1/watches/<id>/actions` endpoints.
"""

from __future__ import annotations

import sys
from typing import Any

import typer
import yaml

from openmagpie_schema.watch import WatchActionRunWire, WatchActionWire
from openmagpie_schema.watch_enums import WatchActionRunState, WatchActivityWindow, choices

from ... import console
from ...context import app_ctx
from .._shared import _handle_api_errors, _read_file_or_abort
from ._apps import action_app

# Human labels for the (server-resolved) activity windows, keyed by the
# shared enum so there are no magic strings. Server owns preset -> bounds ;
# the CLI just picks a value and renders the label off `summary.window`.
_WINDOW_LABELS = {
    WatchActivityWindow.DAY: "last 24 hours",
    WatchActivityWindow.YESTERDAY: "yesterday",
    WatchActivityWindow.WEEK: "last 7 days",
    WatchActivityWindow.MONTH: "last 30 days",
}

# Terminal run states, in the order the summary prints them. pending /
# running are the live backlog (no evaluation time), shown separately.
_EVALUATED_ORDER = ["succeeded", "gated", "failed", "errored", "skipped"]


def _score(run: WatchActionRunWire) -> str:
    """The semantic-filter score from the result blob, 2dp ; '-' for kinds
    that don't score (log / webhook) or runs that never produced one."""
    score = run.result.get("score")
    return f"{score:.2f}" if isinstance(score, (int, float)) else "-"


def _when(run: WatchActionRunWire) -> str:
    """Most-progressed timestamp, seconds precision (drop microseconds/tz)."""
    dt = run.completed_at or run.started_at or run.scheduled_at
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "-"


@action_app.command("list")
@_handle_api_errors
def action_list(watch_id: str = typer.Argument(..., help="Watch id.")) -> None:
    """List a watch's action chain, in rank order."""
    actions = app_ctx().api.watch.list_actions(watch_id)
    columns: list[console.Column[WatchActionWire]] = [
        console.Column("ID", lambda a: a.id),
        console.Column("RANK", lambda a: str(a.rank)),
        console.Column("KIND", lambda a: a.kind),
        console.Column("SUMMARY", lambda a: a.summary.detail or "(no summary)"),
    ]
    if not console.table(actions, columns):
        console.log("No actions yet. Add one with `magpie watch action add`.")


@action_app.command("add")
@_handle_api_errors
def action_add(
    watch_id: str = typer.Argument(..., help="Watch id."),
    file: str = typer.Option(..., "--file", "-f", help="YAML/JSON action config ('-' for stdin)."),
    rank: int | None = typer.Option(None, "--rank", "-r", help="Insert position (0-based). Appends when omitted."),
) -> None:
    """Add one action to a watch's chain from a config file.

    The file is one action: `{kind: <kind>, config: {...}}` ; the same
    shape as an entry in a watch template's `actions:` list."""
    text = sys.stdin.read() if file == "-" else _read_file_or_abort(file)
    kind, config = _parse_action_or_abort(text)
    created = app_ctx().api.watch.add_action(watch_id, kind, config, rank=rank)
    console.success(f"Added {created.kind} at rank {created.rank} ({created.id})")


@action_app.command("set")
@_handle_api_errors
def action_set(
    action_id: str = typer.Argument(..., help="Action id (from `watch action list`)."),
    file: str = typer.Option(..., "--file", "-f", help="YAML/JSON action config ('-' for stdin)."),
) -> None:
    """Replace one action's config in place (same position in the chain).

    The file is one action: `{kind: <kind>, config: {...}}` ; `kind` may
    differ from the current one to swap the node's kind."""
    text = sys.stdin.read() if file == "-" else _read_file_or_abort(file)
    kind, config = _parse_action_or_abort(text)
    updated = app_ctx().api.watch.set_action(action_id, kind, config)
    console.success(f"Updated action {updated.id} ({updated.kind}, rank {updated.rank})")


@action_app.command("remove")
@_handle_api_errors
def action_remove(
    action_id: str = typer.Argument(..., help="Action id (from `watch action list`)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Remove one action from a watch's chain (the chain renumbers to stay dense)."""
    ac = app_ctx()
    if not yes:
        if not sys.stdin.isatty():
            console.warn(f"Piped input: can't prompt. Re-run with --yes to remove action {action_id}.")
            raise typer.Exit(code=1)
        if not typer.confirm(f"Remove action {action_id}?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)
    ac.api.watch.remove_action(action_id)
    console.success(f"Removed action {action_id}")


@action_app.command("activity")
@_handle_api_errors
def action_activity(
    action_id: str = typer.Argument(..., help="Action id (from `watch action list`)."),
    window: str | None = typer.Option(
        None, "--window", "-w", help=f"Summary window by evaluation time ({choices(WatchActivityWindow)})."
    ),
    list_: bool = typer.Option(False, "--list", "-l", help="List individual runs instead of the summary."),
    state: str | None = typer.Option(
        None, "--state", "-s", help=f"Filter rows by state ({choices(WatchActionRunState)}). Implies --list."
    ),
    after: str | None = typer.Option(None, "--after", "-a", help="Cursor (run id) to page rows after. Implies --list."),
    limit: int | None = typer.Option(None, "--limit", "-n", help="Max rows to show (--list mode)."),
) -> None:
    """An action's activity: a state breakdown over a window by default, or
    the individual runs with --list.

    Default shows what the action EVALUATED in the window (succeeded / gated
    / failed / ...) plus the live pending backlog. Any row-level filter
    (--list / --state / --after) switches to the paginated run log."""
    api = app_ctx().api.watch
    if list_ or state or after:
        _print_runs(api.action_runs(action_id, state=state, after=after, limit=limit))
        return
    # Default window resolved from the shared enum (no magic string) ;
    # validated client-side for a clean local error before the round-trip.
    win = window or WatchActivityWindow.WEEK.value
    try:
        WatchActivityWindow(win)
    except ValueError as exc:
        raise typer.BadParameter(f"unknown window {win!r}; choose from {choices(WatchActivityWindow)}") from exc
    # limit=1: summary mode doesn't show rows, but the endpoint always returns
    # a page ; ask for the smallest. The server resolves the window to bounds
    # and attaches the summary.
    _print_summary(action_id, api.action_runs(action_id, window=win, limit=1))


def _print_summary(action_id: str, resp) -> None:
    s = resp.summary
    if s is None:  # defensive ; the first-page call always carries one
        console.log("No summary available.")
        return
    label = _WINDOW_LABELS[s.window]
    # Two pivoted 2-column tables (same renderer as every list view): what
    # the action EVALUATED in the window, then the live backlog. Kept apart
    # because the backlog isn't time-bound (pending/running have no
    # evaluation time). Window label rides in the first column header.
    pair_cols: list[console.Column[tuple[str, str]]] = [
        console.Column(f"EVALUATED ({label})", lambda kv: kv[0], width=24),
        console.Column("RUNS", lambda kv: kv[1]),
    ]
    console.header(f"action {action_id}")
    console.table([(st, str(s.evaluated.get(st, 0))) for st in _EVALUATED_ORDER], pair_cols)
    console.log("")  # blank line between the two tables
    backlog_cols: list[console.Column[tuple[str, str]]] = [
        console.Column("BACKLOG (now)", lambda kv: kv[0], width=24),
        console.Column("RUNS", lambda kv: kv[1]),
    ]
    console.table([("pending", str(s.pending)), ("running", str(s.running))], backlog_cols)
    console.log("\nDrill in: --list (runs) | -s <state> (filter) | -w <window>")


def _print_runs(resp) -> None:
    if not resp.items:
        console.log("No runs match.")
        return
    columns: list[console.Column[WatchActionRunWire]] = [
        console.Column("RUN ID", lambda r: r.id),
        console.Column("STATE", lambda r: str(r.state)),
        console.Column("SCORE", _score),
        console.Column("ITEM", lambda r: r.feed_item_id),
        console.Column("WHEN", _when),
        console.Column("ERROR", lambda r: r.error or "-"),
    ]
    console.table(resp.items, columns)
    if resp.next_cursor:
        console.log(f"\nNext page: --after {resp.next_cursor}")


def _parse_action_or_abort(text: str) -> tuple[str, dict[str, Any]]:
    """Parse a single-action file into `(kind, config)`. The expected
    shape is `{kind: <kind>, config: {...}}` ; the same shape as an entry
    in a watch template's `actions:` list, so an operator can copy one
    out and feed it here."""
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as e:
        console.error(f"YAML parse error: {e}")
        raise typer.Exit(code=1) from None
    if not isinstance(parsed, dict):
        console.error("Action must be a YAML mapping with `kind` and `config`.")
        raise typer.Exit(code=1)
    kind = parsed.get("kind")
    config = parsed.get("config")
    if not isinstance(kind, str) or not kind:
        console.error("Action `kind` is required (a string).")
        raise typer.Exit(code=1)
    if not isinstance(config, dict):
        console.error("Action `config` must be a mapping.")
        raise typer.Exit(code=1)
    return kind, config

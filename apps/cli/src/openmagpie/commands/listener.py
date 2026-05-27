"""`magpie listener ...` commands: create, list, template, get, edit, delete.

YAML is the on-disk format because the `instructions` field is often a
paragraph or two of prompt and YAML's `|` block scalar makes that
readable in a way JSON doesn't. The CLI parses YAML to JSON before
hitting the server; the server only speaks JSON.

Entry points:

- `magpie listener create -f listener.yaml` (or `-f -`, or no `-f` for $EDITOR)
- `magpie listener template` emits the skeleton to stdout
- `magpie listener list` shows the account's listeners
- `magpie listener get <id>` shows one listener
- `magpie listener edit <id>` full-replace edit (current config in $EDITOR)
- `magpie listener delete <id>` deletes one listener

`create` and `edit` share one mutation flow: server-validate (dry-run)
-> preview -> confirm -> apply. `--dry-run` stops after the preview;
`--yes` skips the prompt and is required for piped (non-TTY) input so
an accidental pipe can't silently mutate. Validation lives server-side;
the CLI surfaces field-level errors from the 400 response. The CLI
never parses the config blob - the server emits a typed `summary`.
"""

from __future__ import annotations

import sys
from importlib import resources

import typer
import yaml
from pydantic import ValidationError

from .. import console
from ..api.listener import (
    ListenerEnvelope,
    ListenerMutationResponse,
    ListenerView,
)
from ..context import AppContext, app_ctx
from ._shared import (
    _handle_api_errors,
    _open_editor_or_abort,
    _print_payload_sample,
    _read_file_or_abort,
)

listener_app = typer.Typer(no_args_is_help=True)


# ── Template ───────────────────────────────────────────────────────────


# The starter config lives as a real `.yaml` resource (syntax-highlighted,
# editable as YAML) rather than a Python heredoc. Shipped as package data;
# read once at import.
TEMPLATE_YAML = resources.files("openmagpie").joinpath("listener_template.yaml").read_text(encoding="utf-8")


@listener_app.command("template")
def template() -> None:
    """Emit a starter listener config to stdout."""
    sys.stdout.write(TEMPLATE_YAML)


# ── Create ─────────────────────────────────────────────────────────────


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


# ── Get / Edit / Delete (single listener) ──────────────────────────────


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

    By default (no `--to`) the cursor resets to empty — the next judge
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
    console.success(f"Rewound listener {detail.name} ({detail.id}) — cursor now {updated.last_judged_item_id!r}")


@listener_app.command("payload-sample")
@_handle_api_errors
def payload_sample(
    listener_id: str = typer.Argument(..., help="Listener id."),
    json_out: bool = typer.Option(False, "--json", help="Dump the raw API envelope as JSON."),
) -> None:
    """Show what each of this listener's notifiers WOULD emit for the
    next batch — same code path delivery takes, just without the ship
    step. One block per configured notifier; an operator wiring `webhook
    + log` sees the JSON their webhook receives alongside the text
    written to server logs for the same hits.

    Default output is human-formatted (header per notifier, payload as
    a JSON block or text block depending on notifier kind). Pass
    `--json` for the structured envelope when scripting.
    Pure preview — fires nothing.
    """
    ac = app_ctx()
    result = ac.api.listener.payload_sample(listener_id)

    if json_out:
        typer.echo(result.model_dump_json(indent=2))
        return

    _print_payload_sample(result)


# ── List ───────────────────────────────────────────────────────────────


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


# ── Helpers ────────────────────────────────────────────────────────────


def _abort_unexpected(what: str, maybe_id: str | None) -> typer.Exit:
    """Build the exit for an inconsistent server response. If an id came
    back, a listener may actually exist despite the inconsistency, so
    name it: the user needs to know to go check / clean up. Returned (not
    raised) so the call site reads `raise _abort_unexpected(...)`."""
    msg = f"Unexpected server response: {what}."
    if maybe_id:
        msg += f" A listener may have been created - check id {maybe_id}"
    console.error(msg)
    return typer.Exit(code=1)


def _edit_template_or_abort() -> str:
    """Open $EDITOR on the template; return the saved text.

    Aborts (exit 1) if the editor returns nothing (user quit without
    saving). The "unchanged from template" check is centralized in
    `_reject_if_unmodified_template` so it covers every input mode.
    """
    edited = typer.edit(TEMPLATE_YAML, extension=".yaml")
    if edited is None:
        console.warn("Edit cancelled.")
        raise typer.Exit(code=1) from None
    return edited


def _reject_if_unmodified_template(body_text: str) -> None:
    """Abort if the config is the shipped template with nothing filled
    in - whatever the input mode (editor, -f file, or piped -f -).

    Without this, `magpie listener template | magpie listener create
    -f -` would server-validate the placeholder, print a preview, and
    only then hit the --yes wall: the actual mistake (you didn't fill
    anything in) never surfaces. Fail here, with the next step."""
    if body_text.strip() == TEMPLATE_YAML.strip():
        console.warn(
            "This is the unmodified template (nothing filled in). Edit "
            "it and pass it with -f, or run `magpie listener create` "
            "(no -f) to fill it in interactively."
        )
        raise typer.Exit(code=1)


def _parse_yaml_or_abort(text: str) -> ListenerEnvelope:
    """Parse operator YAML into the typed `ListenerEnvelope`.

    Only the kind-INDEPENDENT envelope is validated here (missing
    `name`, wrong-typed `delivery_mode`, ...) - cheap, stable, and a far
    better error than a server round-trip for an obvious shape slip.
    `data`'s interior stays opaque (`ConfigBlob`); the server remains its
    sole validator."""
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as e:
        console.error(f"YAML parse error: {e}")
        raise typer.Exit(code=1) from None
    if not isinstance(parsed, dict):
        console.error("Config root must be a YAML mapping (key: value pairs).")
        raise typer.Exit(code=1)
    try:
        return ListenerEnvelope.model_validate(parsed)
    except ValidationError as e:
        console.error("Config envelope error:")
        for err in e.errors():
            path = ".".join(str(p) for p in err["loc"]) or "_"
            console.error(f"  {path}: {err['msg']}")
        raise typer.Exit(code=1) from None


def _mutate(
    ac: AppContext,
    envelope: ListenerEnvelope,
    *,
    dry_run: bool,
    listener_id: str | None,
    seed_cursor: str | None = None,
) -> ListenerMutationResponse:
    """create (POST) when `listener_id` is None, else edit (PUT). Same
    response shape either way; `_run_mutation` drives both. The envelope
    is dumped to the wire dict here (the api client is the transport
    boundary). Transport errors propagate to `@_handle_api_errors`."""
    body = envelope.model_dump(mode="json")
    if listener_id is None:
        return ac.api.listener.create(body, dry_run=dry_run, seed_cursor=seed_cursor)
    return ac.api.listener.update(listener_id, body, dry_run=dry_run)


def _run_mutation(
    ac: AppContext,
    body: ListenerEnvelope,
    *,
    listener_id: str | None,
    dry_run: bool,
    yes: bool,
    seed_cursor: str | None = None,
) -> None:
    """Shared create/edit tail: server validate-only -> preview ->
    confirm -> apply. `listener_id` None = create, else edit.

    The dry-run sanity check differs by verb: a create preview must have
    NO id (server strips the pre-save placeholder); an edit preview
    keeps the real id. Either way `dry_run` must be True, or the server
    persisted when we asked it not to."""
    is_edit = listener_id is not None
    noun = "update" if is_edit else "create"

    preview = _mutate(ac, body, dry_run=True, listener_id=listener_id, seed_cursor=seed_cursor)
    if not preview.dry_run or (preview.id and not is_edit):
        raise _abort_unexpected(
            "asked for a dry run but the server reported a persisted listener",
            preview.id,
        )
    _print_listener(preview, f"Would {noun} this listener:")

    if dry_run:
        console.warn("Dry run only. Nothing was changed.")
        return

    if not yes:
        if not sys.stdin.isatty():
            console.warn(
                f"Piped input: can't prompt for confirmation. Re-run with "
                f"--yes to {noun}, --dry-run to validate only, or run the "
                f"command without -f to use $EDITOR."
            )
            raise typer.Exit(code=1)
        if not typer.confirm(f"{noun.capitalize()} this listener?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)

    result = _mutate(ac, body, dry_run=False, listener_id=listener_id, seed_cursor=seed_cursor)
    if result.dry_run or not result.id:
        raise _abort_unexpected(f"{noun} did not confirm persistence", result.id)
    done = "Updated" if is_edit else "Created"
    console.success(f"{done} listener {result.name} ({result.id})")


def _edit_seed(detail: ListenerView) -> ListenerEnvelope:
    """The editable envelope for `edit`, projected from the current
    (redacted) listener. `ListenerEnvelope`'s `extra=ignore` drops the
    read-only fields (id/is_active/summary/...); only the editable
    envelope survives. `data` is the server's redacted config, opaque
    here - the operator edits it as text; the server re-validates and
    restores `***` secrets + watermarks on PUT."""
    return ListenerEnvelope.model_validate(detail.model_dump())


def _print_listener(obj: ListenerMutationResponse | ListenerView, title: str) -> None:
    """Render a listener for the operator. Pure presentation off typed
    fields; `obj.summary` is the server-built display projection (the
    CLI never parses the config blob). Shared by create/edit previews
    and `get`. Top-level fields pipe-delimited per repo convention;
    multi-value fields comma-separated."""
    s = obj.summary

    console.header(title)
    console.kv("name", obj.name)
    console.kv("kind", obj.kind)
    console.kv("delivery", obj.delivery_mode)
    console.kv("feed", s.feed or "(none)")
    console.kv("engine", s.engine or "?")
    console.kv("notifiers", ", ".join(s.notifiers) or "(none)")
    if obj.recent_items:
        rate = console.rate(obj.recent_hits, obj.recent_items)
        console.kv(
            f"hit rate ({obj.recent_window_days}d)",
            f"{rate} ({obj.recent_hits}/{obj.recent_items})",
        )

    instructions = obj.instructions.strip().replace("\n", " ")
    if len(instructions) > 100:
        instructions = instructions[:99] + "…"
    console.kv("instructions", instructions or "(empty)")

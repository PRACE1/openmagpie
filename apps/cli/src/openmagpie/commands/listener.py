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

import functools
import sys
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

import httpx
import typer
import yaml
from pydantic import ValidationError

from ..api.listener import (
    ListenerDetail,
    ListenerEnvelope,
    ListenerMutationResponse,
)
from ..context import AppContext, app_ctx
from ..http import ApiError, AuthError


def _handle_api_errors[T](fn: Callable[..., T]) -> Callable[..., T]:
    """Translate the transport failure modes into one clean CLI exit, at
    the command boundary. Command bodies just call `ac.api.listener.*`
    directly - no thunks. `typer.Exit` (confirm-aborts, the persistence
    sanity guards) is NOT caught, so it propagates normally. `ApiError`
    goes through `_print_api_error` so 400 field errors and structured
    4xx/5xx details both read legibly."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            return fn(*args, **kwargs)
        except AuthError:
            typer.secho(
                "Not authenticated. Run `magpie auth login` first.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1) from None
        except ApiError as e:
            _print_api_error(e)
            raise typer.Exit(code=1) from None
        except httpx.HTTPError as e:
            typer.secho(
                f"Couldn't reach the server ({type(e).__name__}).",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1) from None

    return wrapper


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
    _run_mutation(app_ctx(), body, listener_id=None, dry_run=dry_run, yes=yes)


# ── Get / Edit / Delete (single listener) ──────────────────────────────


@listener_app.command("get")
@_handle_api_errors
def get(listener_id: str = typer.Argument(..., help="Listener id.")) -> None:
    """Show one listener in the caller's account."""
    ac = app_ctx()
    detail = ac.api.listener.get(listener_id)
    state = "active" if detail.is_active else "paused"
    _print_listener(detail, f"Listener {detail.id}  [{state}]")
    if detail.last_polled_at:
        typer.echo(f"  last polled      | {detail.last_polled_at}")


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
            typer.secho(
                f"Piped input: can't prompt. Re-run with --yes to delete {detail.name} ({detail.id}).",
                fg=typer.colors.YELLOW,
                err=True,
            )
            raise typer.Exit(code=1)
        typer.secho(
            f"Delete listener {detail.name} ({detail.id})? This cannot be undone.",
            fg=typer.colors.RED,
        )
        if not typer.confirm("Delete?"):
            typer.secho("Aborted.", fg=typer.colors.YELLOW, err=True)
            raise typer.Exit(code=1)

    ac.api.listener.delete(listener_id)
    typer.secho(
        f"✓ Deleted listener {detail.name} ({detail.id})",
        fg=typer.colors.GREEN,
    )


# ── List ───────────────────────────────────────────────────────────────


@listener_app.command("list")
@_handle_api_errors
def list_() -> None:
    """List listeners in the caller's account."""
    ac = app_ctx()
    items = ac.api.listener.list()

    if not items:
        typer.echo("No listeners yet. Try `magpie listener template`.")
        return

    # Compact one-line-per-listener output. Pipe-delimited fields per
    # repo convention. id last because it's the longest, lets the
    # interesting fields (name, kind, mode, active) sit left-aligned.
    for it in items:
        active = "active" if it.is_active else "paused"
        typer.echo(f"  {it.name} | {it.kind} | {it.delivery_mode} | {active} | {it.id}")


# ── Helpers ────────────────────────────────────────────────────────────


def _abort_unexpected(what: str, maybe_id: str | None) -> typer.Exit:
    """Build the exit for an inconsistent server response. If an id came
    back, a listener may actually exist despite the inconsistency, so
    name it: the user needs to know to go check / clean up. Returned (not
    raised) so the call site reads `raise _abort_unexpected(...)`."""
    msg = f"Unexpected server response: {what}."
    if maybe_id:
        msg += f" A listener may have been created - check id {maybe_id}"
    typer.secho(msg, fg=typer.colors.RED, err=True)
    return typer.Exit(code=1)


def _read_file_or_abort(path: str) -> str:
    p = Path(path)
    if not p.exists():
        typer.secho(f"File not found: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    return p.read_text()


def _edit_template_or_abort() -> str:
    """Open $EDITOR on the template; return the saved text.

    Aborts (exit 1) if the editor returns nothing (user quit without
    saving). The "unchanged from template" check is centralized in
    `_reject_if_unmodified_template` so it covers every input mode.
    """
    edited = typer.edit(TEMPLATE_YAML, extension=".yaml")
    if edited is None:
        typer.secho("Edit cancelled.", fg=typer.colors.YELLOW, err=True)
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
        typer.secho(
            "This is the unmodified template (nothing filled in). Edit "
            "it and pass it with -f, or run `magpie listener create` "
            "(no -f) to fill it in interactively.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=1)


def _parse_yaml_or_abort(text: str) -> ListenerEnvelope:
    """Parse operator YAML into the typed `ListenerEnvelope`.

    Only the kind-INDEPENDENT envelope is validated here (missing
    `name`, wrong-typed `poll_interval_seconds`, ...) - cheap, stable,
    and a far better error than a server round-trip for an obvious
    shape slip. `data`'s interior stays opaque (`ConfigBlob`); the
    server remains its sole validator."""
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as e:
        typer.secho(f"YAML parse error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None
    if not isinstance(parsed, dict):
        typer.secho(
            "Config root must be a YAML mapping (key: value pairs).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        return ListenerEnvelope.model_validate(parsed)
    except ValidationError as e:
        typer.secho("Config envelope error:", fg=typer.colors.RED, err=True)
        for err in e.errors():
            path = ".".join(str(p) for p in err["loc"]) or "_"
            typer.secho(f"  {path}: {err['msg']}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None


def _mutate(
    ac: AppContext,
    envelope: ListenerEnvelope,
    *,
    dry_run: bool,
    listener_id: str | None,
) -> ListenerMutationResponse:
    """create (POST) when `listener_id` is None, else edit (PUT). Same
    response shape either way; `_run_mutation` drives both. The envelope
    is dumped to the wire dict here (the api client is the transport
    boundary). Transport errors propagate to `@_handle_api_errors`."""
    body = envelope.model_dump(mode="json")
    if listener_id is None:
        return ac.api.listener.create(body, dry_run=dry_run)
    return ac.api.listener.update(listener_id, body, dry_run=dry_run)


def _run_mutation(
    ac: AppContext,
    body: ListenerEnvelope,
    *,
    listener_id: str | None,
    dry_run: bool,
    yes: bool,
) -> None:
    """Shared create/edit tail: server validate-only -> preview ->
    confirm -> apply. `listener_id` None = create, else edit.

    The dry-run sanity check differs by verb: a create preview must have
    NO id (server strips the pre-save placeholder); an edit preview
    keeps the real id. Either way `dry_run` must be True, or the server
    persisted when we asked it not to."""
    is_edit = listener_id is not None
    noun = "update" if is_edit else "create"

    preview = _mutate(ac, body, dry_run=True, listener_id=listener_id)
    if not preview.dry_run or (preview.id and not is_edit):
        raise _abort_unexpected(
            "asked for a dry run but the server reported a persisted listener",
            preview.id,
        )
    _print_listener(preview, f"Would {noun} this listener:")

    if dry_run:
        typer.secho("Dry run only. Nothing was changed.", fg=typer.colors.YELLOW, err=True)
        return

    if not yes:
        if not sys.stdin.isatty():
            typer.secho(
                f"Piped input: can't prompt for confirmation. Re-run with "
                f"--yes to {noun}, --dry-run to validate only, or run the "
                f"command without -f to use $EDITOR.",
                fg=typer.colors.YELLOW,
                err=True,
            )
            raise typer.Exit(code=1)
        if not typer.confirm(f"{noun.capitalize()} this listener?"):
            typer.secho("Aborted.", fg=typer.colors.YELLOW, err=True)
            raise typer.Exit(code=1)

    result = _mutate(ac, body, dry_run=False, listener_id=listener_id)
    if result.dry_run or not result.id:
        raise _abort_unexpected(f"{noun} did not confirm persistence", result.id)
    done = "Updated" if is_edit else "Created"
    typer.secho(
        f"✓ {done} listener {result.name} ({result.id})",
        fg=typer.colors.GREEN,
    )


def _edit_seed(detail: ListenerDetail) -> ListenerEnvelope:
    """The editable envelope for `edit`, projected from the current
    (redacted) listener. `ListenerEnvelope`'s `extra=ignore` drops the
    read-only fields (id/is_active/summary/...); only the editable
    envelope survives. `data` is the server's redacted config, opaque
    here - the operator edits it as text; the server re-validates and
    restores `***` secrets + watermarks on PUT."""
    return ListenerEnvelope.model_validate(detail.model_dump())


def _open_editor_or_abort(seed: str) -> str:
    """Open $EDITOR on `seed` (the current config for an edit). Aborts
    if the editor returns nothing (quit without saving). Unchanged text
    is allowed - re-applying the same config is a valid no-op edit."""
    edited = typer.edit(seed, extension=".yaml")
    if edited is None:
        typer.secho("Edit cancelled.", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1) from None
    return edited


def _print_listener(obj: ListenerMutationResponse | ListenerDetail, header: str) -> None:
    """Render a listener for the operator. Pure presentation off typed
    fields; `obj.summary` is the server-built display projection (the
    CLI never parses the config blob). Shared by create/edit previews
    and `get`. Top-level fields pipe-delimited per repo convention;
    multi-value fields comma-separated."""
    s = obj.summary

    typer.secho(header, fg=typer.colors.CYAN)
    typer.echo(f"  name             | {obj.name}")
    typer.echo(f"  kind             | {obj.kind}")
    typer.echo(f"  delivery         | {obj.delivery_mode} | poll {obj.poll_interval_seconds}s")
    typer.echo(f"  engine           | {s.engine or '?'}")
    typer.echo(f"  streams          | {', '.join(s.streams) or '(none)'}")
    typer.echo(f"  notifiers        | {', '.join(s.notifiers) or '(none)'}")

    instructions = obj.instructions.strip().replace("\n", " ")
    if len(instructions) > 100:
        instructions = instructions[:99] + "…"
    typer.echo(f"  instructions     | {instructions or '(empty)'}")


def _print_api_error(e: ApiError) -> None:
    """Pretty-print a server-side error.

    On 400 the body is the serializer's flat `{path: [messages]}` shape
    (`{"data": {"streams[0].spec.kind": ["..."]}}`); walk it and print
    one line per leaf. Non-400 (404/409/5xx) carry a structured
    `{"error","detail"}`; surface the `detail`/`error` string only -
    never the whole body, which can carry tokens on other endpoints.
    """
    if e.status == 400 and isinstance(e.body, dict):
        typer.secho("Validation error:", fg=typer.colors.RED, err=True)
        for line in _flatten_errors(e.body):
            typer.secho(f"  {line}", fg=typer.colors.RED, err=True)
        return
    detail = ""
    if isinstance(e.body, dict):
        for key in ("detail", "error"):
            val = e.body.get(key)
            if isinstance(val, str) and val:
                detail = f" {val}"
                break
    typer.secho(
        f"Server returned an error (HTTP {e.status}).{detail}",
        fg=typer.colors.RED,
        err=True,
    )


def _flatten_errors(body: Any, prefix: str = "") -> list[str]:
    """DFS over the error dict; yield `path: message` strings.

    The value at each leaf is typically a list of strings, but the
    server also emits a top-level "detail" key for non-field errors, so
    handle scalars too.
    """
    out: list[str] = []
    if isinstance(body, dict):
        for key, val in body.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            out.extend(_flatten_errors(val, child_prefix))
    elif isinstance(body, list):
        for i, item in enumerate(body):
            if isinstance(item, (dict, list)):
                out.extend(_flatten_errors(item, f"{prefix}[{i}]"))
            else:
                out.append(f"{prefix or '_'}: {item}")
    else:
        out.append(f"{prefix or '_'}: {body}")
    return out

"""Shared mutation + display helpers used by the listener commands.

Mutation flow (`create` / `edit`): `_run_mutation` is the validate-only
preview -> confirm -> apply driver; `_mutate` is the per-verb POST/PUT
call it dispatches to. YAML parsing + envelope validation is sync,
cheap, and gives operator-readable errors before any server round-trip
(`_parse_yaml_or_abort`).

Display (`_print_listener`) renders off the typed `summary` projection
the server emits; the CLI never parses the listener `data` blob.

`TEMPLATE_YAML` is the starter config shipped as a real `.yaml`
resource so `magpie listener template` emits it byte-for-byte and the
editor opens a syntax-highlighted file.
"""

from __future__ import annotations

import sys
from importlib import resources

import typer
import yaml
from pydantic import ValidationError

from ... import console
from ...api.listener import (
    ListenerEnvelope,
    ListenerMutationResponse,
    ListenerView,
)
from ...context import AppContext

# The starter config lives as a real `.yaml` resource (syntax-highlighted,
# editable as YAML) rather than a Python heredoc. Shipped as package data;
# read once at import.
TEMPLATE_YAML = resources.files("openmagpie").joinpath("listener_template.yaml").read_text(encoding="utf-8")


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

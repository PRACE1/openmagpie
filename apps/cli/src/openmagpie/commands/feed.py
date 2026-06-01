"""`magpie feed ...` commands: template, create, list, get, view, edit, delete.

A Feed is the curated set of sources the server polls; its items are the
"sort by new and go" surface (`feed view`) and what Watches subscribe
to. YAML is the on-disk format. `create`
and `edit` share the validate -> preview -> confirm -> apply flow.
"""

from __future__ import annotations

import json
import sys
from importlib import resources

import typer
import yaml
from pydantic import ValidationError

from .. import console
from ..api.feed import FeedEnvelope, FeedMutationResponse, FeedView
from ..context import AppContext, app_ctx
from ._shared import (
    _check_format,
    _handle_api_errors,
    _open_editor_or_abort,
    _read_file_or_abort,
)

feed_app = typer.Typer(no_args_is_help=True)

FEED_TEMPLATE_YAML = resources.files("openmagpie").joinpath("feed_template.yaml").read_text(encoding="utf-8")

_DEFAULT_VIEW_LIMIT = 25
_DEFAULT_LIST_LIMIT = 50


# ── Template ───────────────────────────────────────────────────────────


def _emit_doc(yaml_text: str, *, format: str, output: str | None) -> None:
    """Write a documented YAML template either verbatim (preserving
    comments) or projected through json. JSON output loses the inline
    `# ...` annotations: explicit trade-off for the scripted-consumer
    case where comments aren't load-bearing."""
    text = yaml_text if format == "yaml" else json.dumps(yaml.safe_load(yaml_text), indent=2)
    text = text if text.endswith("\n") else text + "\n"
    if output is None:
        sys.stdout.write(text)
        return
    try:
        with open(output, "w") as fh:
            fh.write(text)
    except OSError as exc:
        console.error(f"failed to write {output}: {exc}")
        raise typer.Exit(code=1) from None
    console.success(f"Wrote template to {output}")


@feed_app.command("template")
def template(
    format: str = typer.Option(
        "yaml",
        "--format",
        "-F",
        case_sensitive=False,
        help="Output format: `yaml` (commented; default) or `json` (structural; no comments).",
    ),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout."),
) -> None:
    """Emit a starter feed config to stdout."""
    fmt = _check_format(format)
    _emit_doc(FEED_TEMPLATE_YAML, format=fmt, output=output)


# ── Create ─────────────────────────────────────────────────────────────


@feed_app.command("create")
@_handle_api_errors
def create(
    file: str | None = typer.Option(
        None, "--file", "-f", help="YAML config ('-' for stdin). Omit to edit a fresh template in $EDITOR."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Validate server-side and show the result, then stop."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Create a feed from a YAML config."""
    if file is None:
        body_text = _edit_template_or_abort()
    elif file == "-":
        body_text = sys.stdin.read()
    else:
        body_text = _read_file_or_abort(file)
    _reject_if_unmodified_template(body_text)
    body = _parse_yaml_or_abort(body_text)
    _run_mutation(app_ctx(), body, feed_id=None, dry_run=dry_run, yes=yes)


# ── Get / View / Edit / Delete (single feed) ───────────────────────────


@feed_app.command("get")
@_handle_api_errors
def get(feed_id: str = typer.Argument(..., help="Feed id.")) -> None:
    """Show one feed's config in the caller's account."""
    detail = app_ctx().api.feed.get(feed_id)
    _print_feed(detail, f"Feed {detail.id}  [{console.active_or_paused(detail.is_active)}]")


@feed_app.command("view")
@_handle_api_errors
def view(
    feed_id: str = typer.Argument(..., help="Feed id."),
    limit: int = typer.Option(_DEFAULT_VIEW_LIMIT, "--limit", "-l", help="Max items to show."),
) -> None:
    """Sort by new and go: the feed's recent items, newest first."""
    detail = app_ctx().api.feed.get(feed_id, limit=limit)
    if not detail.recent_items:
        console.log("No items yet.")
        return
    console.header(f"{detail.name} ; {len(detail.recent_items)} recent item(s)")
    for item in detail.recent_items:
        title = str((item.data or {}).get("title") or item.external_id)
        console.log(f"  {item.source_label} | {title[:80]} | {item.external_id}")


@feed_app.command("edit")
@_handle_api_errors
def edit(
    feed_id: str = typer.Argument(..., help="Feed id."),
    file: str | None = typer.Option(
        None, "--file", "-f", help="YAML to apply ('-' for stdin). Omit to edit the current config in $EDITOR."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Validate the edit and show the result, then stop."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Full-replace edit of one feed's config (retention + default_field_map).
    `kind` is server-immutable. Source list mutations go through the
    dedicated verbs (`magpie feed set-sources` / `remove-source`); the
    edit YAML deliberately covers only feed-level knobs."""
    ac = app_ctx()
    detail = ac.api.feed.get(feed_id)
    # `sources` is excluded from the dump even though it lives on
    # FeedEnvelope (the create path uses it); the PUT server route
    # silently discards it on edits, so the editor must not show
    # an editable block for it.
    seed = yaml.safe_dump(
        _edit_seed(detail).model_dump(mode="json", exclude={"sources"}),
        sort_keys=False,
    )
    if file is None:
        body_text = _open_editor_or_abort(seed)
    elif file == "-":
        body_text = sys.stdin.read()
    else:
        body_text = _read_file_or_abort(file)
    body = _parse_yaml_or_abort(body_text)
    _run_mutation(ac, body, feed_id=feed_id, dry_run=dry_run, yes=yes)


@feed_app.command("delete")
@_handle_api_errors
def delete(
    feed_id: str = typer.Argument(..., help="Feed id."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Delete one feed (and its stored items). Destructive and not reversible."""
    ac = app_ctx()
    detail = ac.api.feed.get(feed_id)
    if not yes:
        if not sys.stdin.isatty():
            console.warn(f"Piped input: can't prompt. Re-run with --yes to delete {detail.name} ({detail.id}).")
            raise typer.Exit(code=1)
        console.error(f"Delete feed {detail.name} ({detail.id})? This cannot be undone.")
        if not typer.confirm("Delete?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)
    ac.api.feed.delete(feed_id)
    console.success(f"Deleted feed {detail.name} ({detail.id})")


# ── List ───────────────────────────────────────────────────────────────


@feed_app.command("list")
@_handle_api_errors
def list_(
    limit: int = typer.Option(_DEFAULT_LIST_LIMIT, "--limit", "-l", help="Max feeds per page."),
    after: str | None = typer.Option(None, "--after", "-a", help="Cursor (feed id) to fetch the page after."),
    all_: bool = typer.Option(False, "--all", help="Page through every feed in the account."),
) -> None:
    """List feeds in the caller's account, newest first.

    Cursor-paginated: a single call shows up to `--limit` feeds. Pass the
    `--after <id>` cursor printed at the bottom to get the next page, or
    `--all` to follow the cursor across pages automatically.
    """
    api = app_ctx().api.feed
    seen_any = False
    while True:
        page = api.list(after=after, limit=limit)
        for it in page.items:
            console.log(
                f"  {it.name} | {it.kind} | poll {it.poll_interval_seconds}s "
                f"| {console.active_or_paused(it.is_active)} | {it.id}"
            )
        seen_any = seen_any or bool(page.items)
        if not all_ or not page.next_cursor:
            if page.next_cursor:
                console.log(f"  (more available; rerun with --after {page.next_cursor}, or --all)")
            break
        after = page.next_cursor
    if not seen_any:
        console.log("No feeds yet. Try `magpie feed template`.")


# ── Helpers ────────────────────────────────────────────────────────────


def _abort_unexpected(what: str, maybe_id: str | None) -> typer.Exit:
    msg = f"Unexpected server response: {what}."
    if maybe_id:
        msg += f" A feed may have been created - check id {maybe_id}"
    console.error(msg)
    return typer.Exit(code=1)


def _edit_template_or_abort() -> str:
    edited = typer.edit(FEED_TEMPLATE_YAML, extension=".yaml")
    if edited is None:
        console.warn("Edit cancelled.")
        raise typer.Exit(code=1) from None
    return edited


def _reject_if_unmodified_template(body_text: str) -> None:
    if body_text.strip() == FEED_TEMPLATE_YAML.strip():
        console.warn(
            "This is the unmodified template (nothing filled in). Edit it and pass it with "
            "-f, or run `magpie feed create` (no -f) to fill it in interactively."
        )
        raise typer.Exit(code=1)


def _parse_yaml_or_abort(text: str) -> FeedEnvelope:
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as e:
        console.error(f"YAML parse error: {e}")
        raise typer.Exit(code=1) from None
    if not isinstance(parsed, dict):
        console.error("Config root must be a YAML mapping (key: value pairs).")
        raise typer.Exit(code=1)
    try:
        return FeedEnvelope.model_validate(parsed)
    except ValidationError as e:
        console.error("Config envelope error:")
        for err in e.errors():
            path = ".".join(str(p) for p in err["loc"]) or "_"
            console.error(f"  {path}: {err['msg']}")
        raise typer.Exit(code=1) from None


def _mutate(ac: AppContext, envelope: FeedEnvelope, *, dry_run: bool, feed_id: str | None) -> FeedMutationResponse:
    body = envelope.model_dump(mode="json")
    if feed_id is None:
        return ac.api.feed.create(body, dry_run=dry_run)
    return ac.api.feed.update(feed_id, body, dry_run=dry_run)


def _run_mutation(ac: AppContext, body: FeedEnvelope, *, feed_id: str | None, dry_run: bool, yes: bool) -> None:
    is_edit = feed_id is not None
    noun = "update" if is_edit else "create"

    preview = _mutate(ac, body, dry_run=True, feed_id=feed_id)
    if not preview.dry_run or (preview.id and not is_edit):
        raise _abort_unexpected("asked for a dry run but the server reported a persisted feed", preview.id)
    _print_feed(preview, f"Would {noun} this feed:")

    if dry_run:
        console.warn("Dry run only. Nothing was changed.")
        return

    if not yes:
        if not sys.stdin.isatty():
            console.warn(
                f"Piped input: can't prompt for confirmation. Re-run with --yes to {noun}, "
                f"--dry-run to validate only, or run the command without -f to use $EDITOR."
            )
            raise typer.Exit(code=1)
        if not typer.confirm(f"{noun.capitalize()} this feed?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)

    result = _mutate(ac, body, dry_run=False, feed_id=feed_id)
    if result.dry_run or not result.id:
        raise _abort_unexpected(f"{noun} did not confirm persistence", result.id)
    done = "Updated" if is_edit else "Created"
    console.success(f"{done} feed {result.name} ({result.id})")


def _edit_seed(detail: FeedView) -> FeedEnvelope:
    """The editable envelope for `edit`, projected from the current feed.

    `sources` is a declared field on `FeedEnvelope` (the create-time
    write path uses it), so `extra=ignore` does NOT drop it on a
    naive `model_validate(detail.model_dump())`. The seed YAML
    rendered to $EDITOR would then carry a `sources:` block that the
    server's PUT path silently discards (FeedService.update reads
    only name / poll_interval_seconds / data). Explicit pop is the
    right shape: source list changes go through `set-sources` /
    `remove-source`, and the operator should never see an editable
    sources block here."""
    body = detail.model_dump()
    # Pop server-managed / read-only / sub-resource fields.
    for key in ("sources", "source_count", "recent_items", "summary"):
        body.pop(key, None)
    return FeedEnvelope.model_validate(body)


def _print_feed(obj: FeedMutationResponse | FeedView, title: str) -> None:
    """Render a feed's config + source list for the operator."""
    console.header(title)
    console.kv("name", obj.name)
    console.kv("kind", obj.kind)
    console.kv("poll interval", f"{obj.poll_interval_seconds}s")
    # SourceWire.spec is the typed SourceSpec union; use `.display()`
    # (every variant implements it) ; `.get(...)` would AttributeError.
    display = ", ".join(s.spec.display() for s in obj.sources)
    console.kv("sources", f"({obj.source_count}) {display or '(none)'}")

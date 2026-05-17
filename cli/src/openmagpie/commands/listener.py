"""`magpie listener ...` commands: create, list, template.

YAML is the on-disk format because the `instructions` field is often a
paragraph or two of prompt and YAML's `|` block scalar makes that
readable in a way JSON doesn't. The CLI parses YAML to JSON before
hitting the server; the server only speaks JSON.

Entry points:

- `magpie listener create -f listener.yaml` (or `-f -` for stdin)
- `magpie listener create` (no `-f`) opens `$EDITOR` on a template
- `magpie listener template` emits the skeleton to stdout
- `magpie listener list` shows the account's listeners

`create` always server-validates first and prints a preview, then
prompts before creating. `--dry-run` stops after the preview; `--yes`
skips the prompt and is required for piped (non-TTY) input so an
accidental pipe can't silently create. Validation lives server-side;
the CLI surfaces field-level errors from DRF's 400 response so the
user can correct their YAML.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import httpx
import typer
import yaml

from ..context import app_ctx
from ..http import ApiError, AuthError

listener_app = typer.Typer(no_args_is_help=True)


# ── Template ───────────────────────────────────────────────────────────


# Commented-out example showing every common field so first-time users
# can fill in rather than invent. `instructions` uses the YAML `|`
# block scalar so the prompt reads as plain prose.
TEMPLATE_YAML = """\
# Magpie listener config.
#
# Save and run:
#   magpie listener create -f listener.yaml
#
# Or pipe through stdin (--yes required, no TTY to confirm on):
#   magpie listener template | magpie listener create -f - --yes

name: my-listener
instructions: |
  What should the engine consider a hit? Plain-English criteria
  the LLM reads verbatim. Be specific about what counts AND what
  doesn't, e.g. "Posts about X, but skip jokes and ads."

kind: semantic
delivery_mode: digest      # 'instant' = fire per hit; 'digest' = batch
poll_interval_seconds: 300

data:
  engine:
    kind: ollama
    model: qwen2.5:7b

  streams:
    - spec:
        kind: reddit_subreddit
        subreddit: MachineLearning
      # last_event_at: 2026-04-14T00:00:00Z   # optional; default = "from now forward"

  notifiers:
    - kind: log
      prefix: "[my-listener]"
      include_fields: []
    # - kind: webhook
    #   url: https://example.com/hook
    #   headers: {}
    #   include_fields: []

  hit_threshold: 0.7            # engine score >= this counts as a hit
  digest_interval_seconds: 3600 # only consulted when delivery_mode = digest
"""


@listener_app.command("template")
def template() -> None:
    """Emit a starter listener config to stdout."""
    sys.stdout.write(TEMPLATE_YAML)


# ── Create ─────────────────────────────────────────────────────────────


@listener_app.command("create")
def create(
    file: Optional[str] = typer.Option(
        None,
        "--file",
        "-f",
        help=("Path to a YAML config (use '-' for stdin). Omit to edit a fresh template in $EDITOR."),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Validate server-side and show what would be created, then stop. Never creates.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the confirmation prompt. Required for non-interactive (piped) input.",
    ),
) -> None:
    """Create a listener from a YAML config.

    Always validates server-side first and prints a preview of the
    would-be listener, then asks for confirmation before creating.

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

    body = _parse_yaml_or_abort(body_text)

    ac = app_ctx()

    # Server-side validate-only first. Catches a bad engine kind, an
    # unknown stream kind, a malformed `data` blob, etc. BEFORE anything
    # is persisted, and returns the normalized would-be record so the
    # preview is exactly what create would store.
    preview = _create_or_abort(ac, body, dry_run=True)
    _print_preview(preview)

    if dry_run:
        typer.secho("Dry run only. Nothing was created.", fg=typer.colors.YELLOW, err=True)
        return

    if not yes:
        if not sys.stdin.isatty():
            # Piped / non-interactive: no TTY to prompt on. Refuse rather
            # than silently create, that's the exact accident this guards.
            typer.secho(
                "Non-interactive input: re-run with --yes to create, or --dry-run to validate only.",
                fg=typer.colors.YELLOW,
                err=True,
            )
            raise typer.Exit(code=1)
        if not typer.confirm("Create this listener?"):
            typer.secho("Aborted.", fg=typer.colors.YELLOW, err=True)
            raise typer.Exit(code=1)

    result = _create_or_abort(ac, body, dry_run=False)
    listener_id = result.get("id", "?")
    name = result.get("name", "?")
    typer.secho(f"✓ Created listener {name} ({listener_id})", fg=typer.colors.GREEN)


# ── List ───────────────────────────────────────────────────────────────


@listener_app.command("list")
def list_() -> None:
    """List listeners in the caller's account."""
    ac = app_ctx()
    try:
        items = ac.api.listener.list()
    except AuthError:
        typer.secho(
            "Not authenticated. Run `magpie auth login` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    except ApiError as e:
        typer.secho(
            f"Server returned an error (HTTP {e.status}).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    except httpx.HTTPError as e:
        typer.secho(
            f"Couldn't reach the server ({type(e).__name__}).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

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


def _read_file_or_abort(path: str) -> str:
    p = Path(path)
    if not p.exists():
        typer.secho(f"File not found: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    return p.read_text()


def _edit_template_or_abort() -> str:
    """Open $EDITOR on the template; return the saved text.

    Aborts (exit 1) if the editor returns nothing (user quit without
    saving) or if the text is unchanged from the template (no fields
    filled in).
    """
    edited = typer.edit(TEMPLATE_YAML, extension=".yaml")
    if edited is None:
        typer.secho("Edit cancelled.", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1)
    if edited.strip() == TEMPLATE_YAML.strip():
        typer.secho(
            "Template unchanged. Fill in the fields and re-run.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=1)
    return edited


def _parse_yaml_or_abort(text: str) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as e:
        typer.secho(f"YAML parse error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if not isinstance(parsed, dict):
        typer.secho(
            "Config root must be a YAML mapping (key: value pairs).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    return parsed


def _create_or_abort(ac: Any, body: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    """Call the create endpoint (dry-run or real), mapping the transport
    failure modes to a clean exit. Shared by the preview pass and the
    real create so both surface identical error messages."""
    try:
        return ac.api.listener.create(body, dry_run=dry_run)
    except AuthError:
        typer.secho(
            "Not authenticated. Run `magpie auth login` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    except ApiError as e:
        _print_api_error(e)
        raise typer.Exit(code=1)
    except httpx.HTTPError as e:
        typer.secho(
            f"Couldn't reach the server ({type(e).__name__}).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)


def _print_preview(p: dict[str, Any]) -> None:
    """Render the would-be listener so the user can eyeball it before
    confirming. Fields are pipe-delimited per repo convention; secrets
    in the config blob are already redacted server-side."""
    data = p.get("data") or {}
    streams = data.get("streams") or []
    notifiers = data.get("notifiers") or []
    engine = data.get("engine") or {}

    stream_bits = [
        (s.get("spec") or {}).get("kind", "?")
        + (f":{(s.get('spec') or {}).get('subreddit')}" if (s.get("spec") or {}).get("subreddit") else "")
        for s in streams
    ]
    notifier_bits = [n.get("kind", "?") for n in notifiers]

    typer.secho("Would create this listener:", fg=typer.colors.CYAN)
    typer.echo(f"  name             | {p.get('name', '?')}")
    typer.echo(f"  kind             | {p.get('kind', '?')}")
    typer.echo(f"  delivery         | {p.get('delivery_mode', '?')} | poll {p.get('poll_interval_seconds', '?')}s")
    typer.echo(
        f"  engine           | {engine.get('kind', '?')}" + (f" | {engine.get('model')}" if engine.get("model") else "")
    )
    typer.echo(f"  streams          | {' , '.join(stream_bits) or '(none)'}")
    typer.echo(f"  notifiers        | {' , '.join(notifier_bits) or '(none)'}")

    instructions = (p.get("instructions") or "").strip().replace("\n", " ")
    if len(instructions) > 100:
        instructions = instructions[:99] + "…"
    typer.echo(f"  instructions     | {instructions or '(empty)'}")


def _print_api_error(e: ApiError) -> None:
    """Pretty-print a server-side validation error.

    DRF returns a nested dict on 400 (`{"data": {"streams[0].spec.kind":
    ["..."]}}`), the serializer's `_pydantic_errors_to_drf` shape. We
    walk it depth-first and print one line per leaf so the user sees
    the field path + message inline.
    """
    if e.status == 400 and isinstance(e.body, dict):
        typer.secho("Validation error:", fg=typer.colors.RED, err=True)
        for line in _flatten_errors(e.body):
            typer.secho(f"  {line}", fg=typer.colors.RED, err=True)
        return
    typer.secho(
        f"Server returned an error (HTTP {e.status}).",
        fg=typer.colors.RED,
        err=True,
    )


def _flatten_errors(body: Any, prefix: str = "") -> list[str]:
    """DFS over DRF's nested error dict; yield `path: message` strings.

    DRF's value at each leaf is typically a list of strings, but DRF
    also emits a top-level "detail" key for non-field errors, so we
    handle scalars too.
    """
    out: list[str] = []
    if isinstance(body, dict):
        for key, val in body.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            out.extend(_flatten_errors(val, child_prefix))
    elif isinstance(body, list):
        # Could be a list of error strings (DRF leaf) or a list of
        # dicts (nested per-item errors, e.g. streams[i]).
        for i, item in enumerate(body):
            if isinstance(item, (dict, list)):
                out.extend(_flatten_errors(item, f"{prefix}[{i}]"))
            else:
                out.append(f"{prefix or '_'}: {item}")
    else:
        out.append(f"{prefix or '_'}: {body}")
    return out

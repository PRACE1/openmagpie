"""`magpie listener ...` commands: create, list, template.

YAML is the on-disk format because the `instructions` field is often a
paragraph or two of prompt and YAML's `|` block scalar makes that
readable in a way JSON doesn't. The CLI parses YAML to JSON before
hitting the server; the server only speaks JSON.

Three entry points:

- `magpie listener create -f listener.yaml` (or `-f -` for stdin)
- `magpie listener create` (no `-f`) opens `$EDITOR` on a template
- `magpie listener template` emits the skeleton to stdout
- `magpie listener list` shows the account's listeners

Validation lives server-side; the CLI surfaces field-level errors from
DRF's 400 response so the user can correct their YAML.
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
# Or pipe through stdin:
#   magpie listener template | magpie listener create -f -

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
) -> None:
    """Create a listener from a YAML config.

    Three modes:
      - `-f path/to/listener.yaml` reads the file.
      - `-f -` reads from stdin.
      - no `-f` opens $EDITOR on a fresh template.
    """
    if file is None:
        body_text = _edit_template_or_abort()
    elif file == "-":
        body_text = sys.stdin.read()
    else:
        body_text = _read_file_or_abort(file)

    body = _parse_yaml_or_abort(body_text)

    ac = app_ctx()
    try:
        result = ac.api.listener.create(body)
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

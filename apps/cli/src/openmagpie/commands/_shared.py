"""Cross-command CLI plumbing.

Decorator + I/O + transport-error rendering used by more than one command
module. Lives here so each command module imports from a package-private
shared module instead of reaching across to a sibling's underscore-prefixed
symbols. Leading underscore on each helper keeps them internal to the
commands package — they are not part of any public surface.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import typer

from openmagpie_schema.wire import PayloadSampleResponse

from .. import console
from ..http import ApiError, AuthError


def _handle_api_errors[T](fn: Callable[..., T]) -> Callable[..., T]:
    """Translate the transport failure modes into one clean CLI exit, at
    the command boundary. Command bodies just call `ac.api.*` directly —
    no thunks. `typer.Exit` (confirm-aborts, the persistence sanity
    guards) is NOT caught, so it propagates normally. `ApiError` goes
    through `_print_api_error` so 400 field errors and structured
    4xx/5xx details both read legibly."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            return fn(*args, **kwargs)
        except AuthError:
            console.error("Not authenticated. Run `magpie auth login` first.")
            raise typer.Exit(code=1) from None
        except ApiError as e:
            _print_api_error(e)
            raise typer.Exit(code=1) from None
        except httpx.HTTPError as e:
            console.error(f"Couldn't reach the server ({type(e).__name__}).")
            raise typer.Exit(code=1) from None

    return wrapper


def _read_file_or_abort(path: str) -> str:
    p = Path(path)
    if not p.exists():
        console.error(f"File not found: {path}")
        raise typer.Exit(code=1)
    return p.read_text()


def _open_editor_or_abort(seed: str) -> str:
    """Open $EDITOR on `seed` (the current config for an edit). Aborts
    if the editor returns nothing (quit without saving). Unchanged text
    is allowed — re-applying the same config is a valid no-op edit."""
    edited = typer.edit(seed, extension=".yaml")
    if edited is None:
        console.warn("Edit cancelled.")
        raise typer.Exit(code=1) from None
    return edited


def _print_payload_sample(result: PayloadSampleResponse) -> None:
    """Render the payload-sample envelope for a human.

    One block per notifier (header: `kind` or `kind | target`), body
    is JSON for dict-rendered notifiers (webhook) and plain text for
    string-rendered ones (log). Shared by `listener payload-sample`
    and the `quickstart` wizard so both flows format hits identically.
    """
    if result.synthetic:
        console.warn("(synthetic sample — listener has no hit events yet)")

    if not result.notifiers:
        console.warn("(no notifiers configured — this listener fires nothing)")
        return

    for i, entry in enumerate(result.notifiers):
        if i > 0:
            typer.echo("")
        header = f"{entry.kind} | {entry.target}" if entry.target else entry.kind
        console.header(header)
        if isinstance(entry.rendered, dict):
            typer.echo(json.dumps(entry.rendered, indent=2, default=str))
        else:
            typer.echo(entry.rendered)


def _print_api_error(e: ApiError) -> None:
    """Pretty-print a server-side error.

    On 400 the body is the serializer's flat `{path: [messages]}` shape
    (`{"data": {"streams[0].spec.kind": ["..."]}}`); walk it and print
    one line per leaf. Non-400 (404/409/5xx) carry a structured
    `{"error","detail"}`; surface the `detail`/`error` string only —
    never the whole body, which can carry tokens on other endpoints.
    """
    if e.status == 400 and isinstance(e.body, dict):
        console.error("Validation error:")
        for line in _flatten_errors(e.body):
            console.error(f"  {line}")
        return
    detail = ""
    if isinstance(e.body, dict):
        for key in ("detail", "error"):
            val = e.body.get(key)
            if isinstance(val, str) and val:
                detail = f" {val}"
                break
    console.error(f"Server returned an error (HTTP {e.status}).{detail}")


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

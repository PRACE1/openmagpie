"""Semantic CLI output helpers.

Use these instead of `typer.secho` / `typer.echo` so the color + stream routing
is implicit in the intent, not duplicated at every call site:

  error(msg)   red,    stderr   — failure, refusal, validation error
  warn(msg)    yellow, stderr   — caution, cancellation, dry-run notice, "are you sure?"
  success(msg) green,  stdout   — operation completed (auto-prepends "✓ ")
  header(msg)  cyan,   stdout   — section title above a block of detail
  log(msg)     plain,  stdout   — neutral output (field rows, list items, body text)

Plus small value formatters:

  active_or_paused(is_active) -> "active" | "paused"   — canonical label for
  the `is_active` flag carried on feeds + listeners.
  rate(numerator, denominator) -> "X%" or "—"   — percentage label,
  rendered "—" when the denominator is zero.

Plus one row-renderer for detail views:

  kv(label, value, *, width=16)   — one "  label  | value" row, with
  `label` left-padded to `width` so the ` | ` separator lines up across
  a block of rows.
"""

import typer


def error(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.RED, err=True)


def warn(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.YELLOW, err=True)


def success(msg: str) -> None:
    typer.secho(f"✓ {msg}", fg=typer.colors.GREEN)


def header(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.CYAN)


def log(msg: str) -> None:
    typer.echo(msg)


def kv(label: str, value: str, *, width: int = 16) -> None:
    """One '  label  | value' row, label left-padded to `width` so the
    ` | ` separator lines up across a block of rows in a detail view."""
    typer.echo(f"  {label:<{width}} | {value}")


def active_or_paused(is_active: bool) -> str:
    return "active" if is_active else "paused"


def rate(numerator: int, denominator: int) -> str:
    if not denominator:
        return "—"
    return f"{100 * numerator / denominator:.0f}%"

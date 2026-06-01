"""The typer app + shared template constant for the `feed` command group.

Split out so `_crud.py` and `_sources.py` both register verbs onto the
same `feed_app` without importing each other (no circular import).
"""

from __future__ import annotations

from importlib import resources

import typer

feed_app = typer.Typer(no_args_is_help=True)

FEED_TEMPLATE_YAML = resources.files("openmagpie").joinpath("feed_template.yaml").read_text(encoding="utf-8")

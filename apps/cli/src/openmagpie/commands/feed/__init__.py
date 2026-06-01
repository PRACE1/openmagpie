"""`magpie feed` command group.

`_apps` owns the typer app; importing `_crud` and `_sources` registers
their verbs onto it as a side effect. `feed_app` is what cli.py mounts.
"""

from . import _crud, _sources  # noqa: F401  side-effect: register verbs
from ._apps import feed_app

__all__ = ["feed_app"]

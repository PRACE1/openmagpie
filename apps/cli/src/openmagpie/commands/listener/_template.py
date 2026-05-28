"""`magpie listener template` — emit a starter listener config to stdout."""

import sys

from . import listener_app
from ._helpers import TEMPLATE_YAML


@listener_app.command("template")
def template() -> None:
    """Emit a starter listener config to stdout."""
    sys.stdout.write(TEMPLATE_YAML)

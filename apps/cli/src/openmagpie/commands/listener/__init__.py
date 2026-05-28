"""`magpie listener ...` commands.

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
- `magpie listener rewind <id>` resets the judge cursor
- `magpie listener payload-sample <id>` previews per-notifier delivery

`create` and `edit` share one mutation flow (in `_helpers`):
server-validate (dry-run) -> preview -> confirm -> apply. `--dry-run`
stops after the preview; `--yes` skips the prompt and is required for
piped (non-TTY) input so an accidental pipe can't silently mutate.
Validation lives server-side; the CLI surfaces field-level errors from
the 400 response. The CLI never parses the config blob; the server
emits a typed `summary`.
"""

import typer

listener_app = typer.Typer(no_args_is_help=True)

# Sub-modules register their commands on `listener_app` at import time.
# Imported here for side effects; ordering matches the help screen the
# operator sees.
from . import _template, _create, _crud, _tools, _list  # noqa: F401, I001

__all__ = ["listener_app"]

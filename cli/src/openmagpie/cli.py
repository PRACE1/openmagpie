"""Root Typer app + subcommand registration."""

from __future__ import annotations

import typer

from .commands.auth import auth_app
from .commands.listener import listener_app
from .context import AppContext, bind_app_ctx, unbind_app_ctx

app = typer.Typer(
    name="magpie",
    help="The magpie CLI. Talk to an OpenMagpie server.",
    no_args_is_help=True,
)


@app.callback()
def main(
    ctx: typer.Context,
    server: str | None = typer.Option(
        None,
        "--server",
        "-s",
        help="OpenMagpie server URL override for this invocation.",
    ),
) -> None:
    """Build the shared AppContext (config + resource API) and bind it
    into the ContextVar so every subcommand can pull it via `app_ctx()`.
    """
    obj = AppContext(server_url=server)
    token = bind_app_ctx(obj)
    ctx.call_on_close(obj.close)
    ctx.call_on_close(lambda: unbind_app_ctx(token))


app.add_typer(auth_app, name="auth", help="Sign in / out and inspect identity.")
app.add_typer(
    listener_app,
    name="listener",
    help="Create + list listeners.",
)

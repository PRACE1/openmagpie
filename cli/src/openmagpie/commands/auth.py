"""`magpie auth ...` commands: login (device flow), status, logout."""

from __future__ import annotations

import time
import webbrowser
from urllib.parse import urlparse

import httpx
import typer

from ..api.auth import DeviceSessionCompleted, DeviceSessionExpired
from ..context import app_config, app_ctx
from ..http import ApiError, AuthError

auth_app = typer.Typer(no_args_is_help=True)

POLL_INTERVAL_SECONDS = 2.0
# Small grace window past the server-reported `expires_in` to give the
# server's eviction-driven 404 time to land (so the user sees "Session
# expired" instead of our own generic "timed out").
POLL_DEADLINE_GRACE_SECONDS = 5.0
# Hard ceiling on how long we're willing to sit in the polling loop,
# regardless of what the server reports. A buggy / hostile server
# returning `expires_in: 10_000_000` would otherwise leave a CLI
# process spinning for months. The legit server's pending-session TTL
# is 15 min; 30 min gives us 2x headroom for edge cases without ever
# letting the loop run indefinitely.
MAX_DEVICE_LOGIN_SECONDS = 30 * 60


def _print_signed_in(email: str) -> None:
    typer.secho(f"✓ Signed in as {email}", fg=typer.colors.GREEN)


def _safe_authorize_url(authorize_url: str, server_url: str) -> bool:
    """Refuse server-supplied URLs that don't match the configured server.

    A compromised or rogue server could return an `authorize_url`
    pointing anywhere (phishing surface). We require:
      - scheme is http or https (no `javascript:`, `data:`, etc.)
      - hostname matches the configured server's hostname exactly
        (port-agnostic, so localhost:8000 ↔ localhost:3001 works for
        the standard dev split).
    """
    try:
        a = urlparse(authorize_url)
        s = urlparse(server_url)
    except Exception:
        return False
    if a.scheme not in ("http", "https"):
        return False
    if not a.hostname or not s.hostname:
        return False
    return a.hostname.lower() == s.hostname.lower()


@auth_app.command("login")
def login(
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Don't try to launch a browser; just print the URL.",
    ),
) -> None:
    """Sign in via the browser device-flow handshake."""
    ac = app_ctx()

    try:
        created = ac.api.auth.create_device_session()
    except ApiError as e:
        typer.secho(
            f"Couldn't reach server at {ac.config.server_url}: {e}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    if not _safe_authorize_url(created.authorize_url, ac.config.server_url):
        typer.secho(
            "The server returned an authorize URL that doesn't match "
            f"the configured server ({ac.config.server_url}). Refusing "
            "to open it. If you trust this server, reconfigure with "
            "--server, then try again.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(f"Open this URL to authorize: {created.authorize_url}")
    typer.secho(
        f"Verification code: {created.user_code}",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.echo("Enter this code on the authorize page to confirm it's your CLI.")
    if not no_browser:
        opened = False
        try:
            opened = webbrowser.open(created.authorize_url)
        except Exception as e:
            typer.secho(
                f"Couldn't launch a browser ({e}). Open the URL above to continue.",
                fg=typer.colors.YELLOW,
                err=True,
            )
        else:
            if not opened:
                typer.secho(
                    "No browser available. Open the URL above to continue.",
                    fg=typer.colors.YELLOW,
                    err=True,
                )

    typer.echo("Waiting for authorization...")

    # Server tells us how long the session is valid; we follow that
    # (plus a small grace), but clamp to MAX_DEVICE_LOGIN_SECONDS so a
    # buggy or hostile server returning `expires_in: 10_000_000` can't
    # park this CLI process in a polling loop for months.
    session_seconds = min(created.expires_in, MAX_DEVICE_LOGIN_SECONDS)
    deadline = time.monotonic() + session_seconds + POLL_DEADLINE_GRACE_SECONDS
    transport_warned = False
    try:
        while time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL_SECONDS)
            try:
                poll = ac.api.auth.poll_device_session(created.session_id, device_secret=created.device_secret)
            except ApiError as e:
                if e.status == 404:
                    typer.secho(
                        "Session expired before authorization completed.",
                        fg=typer.colors.RED,
                        err=True,
                    )
                    raise typer.Exit(code=1)
                # Non-404 ApiError: server is reachable but unhappy. No
                # value in retrying; surface the status and exit.
                # `e.body` deliberately not printed, bodies can carry
                # tokens; if the user needs detail, server logs have it.
                typer.secho(
                    f"Server returned an error while polling (HTTP {e.status}).",
                    fg=typer.colors.RED,
                    err=True,
                )
                raise typer.Exit(code=1)
            except httpx.HTTPError as e:
                # Transport-level error (connect refused, timeout, DNS,
                # protocol). Treat as transient and keep polling. Warn
                # once so the user knows the CLI isn't hung; subsequent
                # retries stay quiet to avoid flooding the terminal.
                if not transport_warned:
                    typer.secho(
                        f"  (transport error: {type(e).__name__}; will keep retrying. Ctrl-C to abort.)",
                        fg=typer.colors.YELLOW,
                        err=True,
                    )
                    transport_warned = True
                continue

            if isinstance(poll, DeviceSessionCompleted):
                ac.sign_in(poll)
                _print_signed_in(poll.user.email)
                return
            if isinstance(poll, DeviceSessionExpired):
                typer.secho("Session expired.", fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1)
    except KeyboardInterrupt:
        # 130 = 128 + SIGINT, the conventional shell exit code for Ctrl-C.
        # Newline first so the message doesn't ride on the same line as
        # the terminal's "^C" echo.
        typer.secho("\nCancelled.", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=130)

    typer.secho(
        f"Timed out waiting for authorization ({session_seconds // 60} min).",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=1)


@auth_app.command("status")
def status() -> None:
    """Show the currently signed-in identity."""
    ac = app_ctx()
    if not ac.config.is_authenticated:
        typer.secho("Not authenticated.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    try:
        me = ac.api.auth.me()
    except AuthError:
        typer.secho(
            "Stored credentials are no longer valid. Run `magpie auth login` again.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    except ApiError as e:
        typer.secho(
            f"Couldn't reach the server cleanly (HTTP {e.status}). Try again or check the server status.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    except httpx.HTTPError as e:
        typer.secho(
            f"Couldn't reach the server ({type(e).__name__}). Check your network or server URL.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(f"Signed in as {me.email}")
    if me.account_id:
        typer.echo(f"Account:     {me.account_id}")
    typer.echo(f"Server:      {ac.config.server_url}")


@auth_app.command("logout")
def logout() -> None:
    """Clear locally stored credentials and revoke server-side."""
    if not app_config().is_authenticated:
        typer.echo("Not signed in; nothing to clear.")
        return
    if not app_ctx().sign_out():
        typer.secho(
            "Couldn't reach server to revoke the token. Local credentials "
            "cleared, but the token may stay valid until it expires naturally.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    typer.secho("✓ Logged out.", fg=typer.colors.GREEN)

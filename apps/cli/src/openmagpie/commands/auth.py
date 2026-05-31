"""`magpie auth ...` commands: login (device flow), status, logout."""

from __future__ import annotations

import time
import webbrowser
from urllib.parse import urlparse

import httpx
import typer

from .. import console
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
    console.success(f"Signed in as {email}")


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
    except ValueError:
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
        console.error(f"Couldn't reach server at {ac.config.server_url}: {e}")
        raise typer.Exit(code=1) from None

    if not _safe_authorize_url(created.authorize_url, ac.config.server_url):
        console.error(
            "The server returned an authorize URL that doesn't match "
            f"the configured server ({ac.config.server_url}). Refusing "
            "to open it. If you trust this server, reconfigure with "
            "--server, then try again."
        )
        raise typer.Exit(code=1)

    console.log(f"Open this URL to authorize: {created.authorize_url}")
    # Cyan+bold for the verification code: it's the one piece of data the
    # operator must read off the terminal and type into the browser, so it
    # gets a bolder treatment than `console.header` (which is cyan, not bold).
    typer.secho(f"Verification code: {created.user_code}", fg=typer.colors.CYAN, bold=True)
    console.log("Enter this code on the authorize page to confirm it's your CLI.")
    if not no_browser:
        opened = False
        try:
            opened = webbrowser.open(created.authorize_url)
        except webbrowser.Error as e:
            console.warn(f"Couldn't launch a browser ({e}). Open the URL above to continue.")
        else:
            if not opened:
                console.warn("No browser available. Open the URL above to continue.")

    console.log("Waiting for authorization...")

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
                    console.error("Session expired before authorization completed.")
                    raise typer.Exit(code=1) from None
                # Non-404 ApiError: server is reachable but unhappy. No
                # value in retrying; surface the status and exit.
                # `e.body` deliberately not printed, bodies can carry
                # tokens; if the user needs detail, server logs have it.
                console.error(f"Server returned an error while polling (HTTP {e.status}).")
                raise typer.Exit(code=1) from None
            except httpx.HTTPError as e:
                # Transport-level error (connect refused, timeout, DNS,
                # protocol). Treat as transient and keep polling. Warn
                # once so the user knows the CLI isn't hung; subsequent
                # retries stay quiet to avoid flooding the terminal.
                if not transport_warned:
                    console.warn(f"  (transport error: {type(e).__name__}; will keep retrying. Ctrl-C to abort.)")
                    transport_warned = True
                continue

            if isinstance(poll, DeviceSessionCompleted):
                ac.sign_in(poll)
                _print_signed_in(poll.user.email)
                return
            if isinstance(poll, DeviceSessionExpired):
                console.error("Session expired.")
                raise typer.Exit(code=1)
    except KeyboardInterrupt:
        # 130 = 128 + SIGINT, the conventional shell exit code for Ctrl-C.
        # Newline first so the message doesn't ride on the same line as
        # the terminal's "^C" echo.
        console.warn("\nCancelled.")
        raise typer.Exit(code=130) from None

    console.error(f"Timed out waiting for authorization ({session_seconds // 60} min).")
    raise typer.Exit(code=1)


@auth_app.command("status")
def status() -> None:
    """Show the currently signed-in identity."""
    ac = app_ctx()
    if not ac.config.is_authenticated:
        console.error("Not authenticated.")
        raise typer.Exit(code=1)

    try:
        me = ac.api.auth.me()
    except AuthError:
        console.error("Stored credentials are no longer valid. Run `magpie auth login` again.")
        raise typer.Exit(code=1) from None
    except ApiError as e:
        console.error(f"Couldn't reach the server cleanly (HTTP {e.status}). Try again or check the server status.")
        raise typer.Exit(code=1) from None
    except httpx.HTTPError as e:
        console.error(f"Couldn't reach the server ({type(e).__name__}). Check your network or server URL.")
        raise typer.Exit(code=1) from None

    console.log(f"Signed in as {me.email}")
    if me.account_id:
        console.log(f"Account:     {me.account_id}")
    console.log(f"Server:      {ac.config.server_url}")


@auth_app.command("logout")
def logout() -> None:
    """Clear locally stored credentials and revoke server-side."""
    if not app_config().is_authenticated:
        console.log("Not signed in; nothing to clear.")
        return
    if not app_ctx().sign_out():
        console.warn(
            "Couldn't reach server to revoke the token. Local credentials "
            "cleared, but the token may stay valid until it expires naturally."
        )
    console.success("Logged out.")

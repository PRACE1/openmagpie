# cli/AGENTS.md

Conventions for the `magpie` CLI. Cross-cutting rules live in [../AGENTS.md](../AGENTS.md).

## Stack

Typer + httpx + Pydantic + PyYAML. Binary name: `magpie`. Config: `~/.magpie/config.json` (mode `0600`, Pydantic-validated). YAML is the on-disk format for config blobs the CLI feeds the server (e.g. listener configs); the server only speaks JSON. Convert with `yaml.safe_load` at the CLI boundary.

## Config shape

The on-disk file is a multi-env wrapper, not a flat Config:

```json
{
  "active_env": "local",
  "envs": {
    "local": { "server_url": "http://localhost:8000", "access_token": "...", ... }
  }
}
```

`load()` returns the active env's `Config`, so call sites stay on `config.access_token` etc. and don't see the wrapper. New envs (e.g. a future `cloud`) drop in as additional keys under `envs` without a file-format break.

Default `local` env: `server_url = "http://localhost:8000"`. Any other env name set as `active_env` without a corresponding `envs` record raises rather than silently fabricating defaults.

## Layout

```
cli/src/openmagpie/
  routes.py          # path constants (routes.auth.tokens.refresh, ...)
  constants.py       # wire-level enums (DeviceSessionStatus, BEARER_TOKEN_TYPE)
  http.py            # MagpieClient transport (auto-refresh inside _refresh)
  api/               # SDK-style resource clients with Pydantic models
  context.py         # AppContext (config + http + api)
  config.py          # Pydantic-validated config; concurrent-safe save()
  commands/          # Typer subcommands. Thin orchestration only.
```

## AppContext

Built once by the root Typer callback into a `contextvars.ContextVar`. Subcommands pull it via `app_ctx()` / `app_api()` / `app_config()`. No `ctx: typer.Context` threading in command signatures.

```python
ac = app_ctx()
me = ac.api.auth.me()            # resource-style access
ac.sign_in(token_bundle)         # credential mutation through AppContext
ac.sign_out()                    # returns bool: server-side revoke success
```

- `AppContext.sign_in(bundle)` and `sign_out()` own credential mutation. `Config.apply_credentials` / `clear_credentials` are the primitives both `sign_in` and `MagpieClient._refresh` call.
- `sign_out()` returns `bool`: local cleanup is unconditional; the bool surfaces whether server-side revoke also succeeded so commands can warn the user when the token may still be live.

## HTTP transport

- `MagpieClient` in `http.py` is pure transport. Adds the server base URL + Bearer header.
- **TLS verification is explicit** on the underlying `httpx.Client` (`verify=_VERIFY_TLS`). Don't rely on httpx's default. `MAGPIE_INSECURE_SKIP_TLS_VERIFY=1` opts out for corporate-MITM scenarios.
- **Two refresh triggers, both transparent to callers:**
  1. **Proactive (local clock).** `_ensure_fresh_token` rotates when within `REFRESH_LEEWAY_SECONDS` of expiry.
  2. **Reactive (server 401).** `_authed_call` does a single-shot refresh-and-replay when an authenticated request returns 401, covering server-side early revocation (admin force-logout, key rotation, user revoking from another device). Single attempt only; the absence of a loop is the guard.
- **Unauthenticated POSTs (`with_auth=False`) skip the retry path** because there's no token to refresh. Used by `/tokens/refresh` itself (would loop) and pre-login endpoints.
- **Refresh failure: only 401 clears local creds.** Other non-2xx raise `ApiError` without clearing; a network blip or 5xx shouldn't sign the user out.
- **`ApiError.__str__` deliberately OMITS the body.** Response bodies can carry tokens (the refresh-rotation path echoes them on success, a misbehaving server might echo them on failure too). Callers that want body info access `e.body` and own the print/redact decision.

## Typed boundaries (where `Any` / raw `dict` is allowed)

Resource clients in `api/` **return parsed Pydantic models, never raw
`dict`**. A method that does `return self._http.get(...)` straight to the
caller is a bug: parse the `raw` through a model first (see
`api/auth.py`, and `ListenerListResponse` / `ListenerMutationResponse`
in `api/listener.py`). The point is that a command author reads response
shapes from the CLI's own models, never by diving into the server.

`Any` / `dict[str, Any]` is allowed in exactly three places, and only
these:

1. **The `http.py` transport seam.** `get` / `post` / `_handle` return
   `Any` because the raw layer genuinely can't know the shape. Typing
   happens one layer up, in the `api/` client. Don't fake a type here.
2. **The opaque request body.** User-authored config (YAML → `dict`) is
   posted to the server-as-sole-validator. Typing it CLI-side would mean
   mirroring the server's Pydantic registry and re-versioning on every
   new listener kind, the drift the dry-run design exists to avoid. The
   honest type for "arbitrary config we deliberately don't validate
   here" is `dict[str, Any]`.
3. **Polymorphic error bodies.** `ApiError.body` / `_flatten_errors` walk
   a genuinely variable structure (DRF nested dict, structured error, or
   plain text).

Everything else, command args, helper params, AppContext, gets a real
type. A new `Any` outside the three cases above needs a one-line comment
justifying why the shape is genuinely unknowable, or it's wrong.

## Structured CLI identity (not User-Agent parsing)

The CLI sends a structured `client_info()` payload on the device-flow `/create` body. The authorize page renders those fields directly. **Do not parse User-Agent strings server-side for product behavior.** UA exists for log visibility only.

```python
{"name": "magpie-cli", "version": __version__, "hostname": socket.gethostname()}
```

OS / Python runtime details are intentionally OUT: they're noise on a security UI and we don't audit them.

## Per-request headers

`MagpieClient.get` / `post` accept an optional `headers` parameter for one-off concerns like the device-flow `X-Device-Secret` polling proof. Resource clients in `api/` build those at the call site rather than threading them through the client.

## Concurrent-safe config writes

`config.save()` uses `tempfile.NamedTemporaryFile` so two concurrent CLI processes can't half-overwrite each other's tokens. Permissions are `0600` from the moment the file is created (not chmod'd after).

## Error handling in commands

- **Transport-level errors** (`httpx.HTTPError` subclasses) are transient. In polling loops, warn once and continue; the next iteration may succeed.
- **`AuthError`** (401) means the stored credential is invalid. Tell the user to re-run `magpie auth login`.
- **`ApiError`** (other non-2xx) means the server is reachable but unhappy. Surface the status; don't print the body.
- **`KeyboardInterrupt`** in interactive flows exits 130 (128 + SIGINT), the conventional shell exit code for Ctrl-C. Print a newline first so the message doesn't ride on the terminal's `^C` echo.

All RED/YELLOW `typer.secho` writes go to stderr (`err=True`) so command output stays clean for piping.

## Server-supplied URL safety

The CLI never opens a server-supplied URL blindly. `_safe_authorize_url` requires `scheme in ("http", "https")` and `hostname == configured server hostname` before `webbrowser.open(...)` is allowed to touch it.

## File-driven config commands

Commands that create server-side resources from operator-authored config (currently just `magpie listener create`) accept YAML on disk or stdin, plus a no-argument variant that opens `$EDITOR` on a template:

- `magpie listener create -f listener.yaml`
- `magpie listener create -f -` (stdin)
- `magpie listener create` (opens `$EDITOR` on the template via `typer.edit`)
- `magpie listener template` emits the skeleton to stdout for piping or redirecting

A creating command validates server-side before it mutates: it POSTs once with `?dry_run=true` (server runs the identical serializer/service validation and returns the would-be record without persisting), prints a preview, then prompts to confirm. `--dry-run` stops after the preview; `--yes` skips the prompt and is required when stdin is not a TTY so a pipe can't silently create. Dry-run is a parameter on the real endpoint, not a separate validate route, so the preview's *validation* cannot drift from the create path. It is a validation preview, not a create-success guarantee (persistence can still fail).

YAML round-trips via `yaml.safe_load` into a `dict` and posts straight at the server's JSON endpoint. The server is the single source of validation truth; the CLI's job is to surface DRF's nested 400 error dict (`{"data": {"streams[0].spec.kind": ["..."]}}`) as one line per leaf path. New file-driven commands should follow the same modes + template emitter + dry-run/confirm convention.

# core/AGENTS.md

Conventions for the Django backend. Cross-cutting rules live in [../AGENTS.md](../AGENTS.md).

## Layout

```
core/
  common/          BaseModel (ULID PK + timestamps), ULIDField, /healthz view
  accounts/        User (email login), Account, UserProfile + services/
  auth_api/        DRF auth surface: signup / login / logout / me + tokens/* + device-sessions/*
  listeners/       Listener model + SemanticListenerConfig (Pydantic) + polling orchestrator
  events/          Event model + Observation hierarchy + per-(source,kind) registry
  sources/         Connectors (RedditSubRedditConnector, ...) + per-kind observation classes
  engine/          Engine Protocol + OllamaEngine + registry
  notifications/   Notifier Protocol (Webhook, Log) + instant/digest delivery
  conf/            settings (base + local override), urls, wsgi
```

## File shape per app

```
<app>/
  services/<resource>.py    # CRUD + read services
  models/<model>.py         # Django models (no admin)
  apps.py                   # may have ready() to load registries
  migrations/
  tests.py
  views.py                  # only if the app exposes HTTP
```

Apps are created with `python manage.py startapp <name>` inside the container (`make dev-manage CMD="startapp <name>"`), then customized per these conventions.

## Models & data access

- **Every model inherits `common.models.BaseModel`** → ULID primary key + `created_at` + `updated_at`.
- **No `ForeignKey`. Char pointers only.** Cross-model references are `CharField(max_length=26)` named `<thing>_id`. Stale data is OK; no cascades; no auto-indexes. Add `db_index=True` only when a specific lookup needs it.
- **No direct `Model.objects.*` outside the model's owning service module.** All access goes through `<app>/services/<resource>.py`.
- **Services are classes, not loose module functions.** One class per primary entity (e.g. `ListenerService`, `EventService`, `DeliveryService`). Instance methods for account-scoped operations; a nested `class Global:` (with `@staticmethod` methods) for cross-tenant operations.
- **Account-scoped services bind their scope in `__init__`** and raise `ValueError` if `account_id` is missing or empty. Methods then drop the `account_id=` kwarg; `self.account_id` is the single source of truth. Scoped services also assert that incoming domain objects match `self.account_id` (defense-in-depth at the seam; `ValueError` if a foreign-account object is handed in).
- **System-level operations live under `<Service>.Global`** as static methods. These are the only place cross-tenant queries happen; reach for them sparingly (schedulers, admin / debug entry points).
- **One-shot orchestrators are `Operation` classes**, not `Service` classes. Pattern: build with the domain object, call `.run()` once, discard. Use this when an action has internal state across helpers (counters, watermarks) and would otherwise force every helper to thread the same args. Example: `PollListenerOperation(listener).run()`. The `Service` suffix stays reserved for reusable, account-scoped services; `Operation` signals "single-use, not reusable."
- **Operations instantiate scoped services internally** from the domain object's `account_id`; callers just hand in a domain object. Service constructions belong on `@cached_property` so `__init__` stays validation-only.
- **Function-shaped wrappers** (e.g. `poll_listener(listener)`) may exist alongside an Operation for callers that prefer the function form (mgmt commands, scripts). The wrapper is one line: `return PollListenerOperation(listener).run()`.
- **`get`/`get_by_<field>` raise `DoesNotExist`.** Never return `None`. Type stays `-> Model`; callers handle missing via `try/except`. If "might not exist" is the normal path, add a separate `find_by_<field>` returning `Model | None`.
- **Return iterators for collections.** Use `.iterator(chunk_size=N)`; callers `list(...)` if they need to materialize. Bulk writes use `.bulk_create()` / `.bulk_update()`.
- **Every query hits an index.** Every column in a service WHERE must be indexed via `db_index=True`, a `UniqueConstraint`, or `Meta.indexes`. Don't add an explicit index if a `UniqueConstraint` already left-prefix-covers the read path.

### Call-site shape

```python
# Account-scoped (the common case)
svc = ListenerService(account_id=account_id)
listener = svc.get(id)
svc.update_poll_state(listener, last_polled_at=now, data=...)

# Cross-tenant (rare; scheduler, admin)
for listener in ListenerService.Global.list_due_for_poll(now=now):
    ...

# One-shot Operation (recommended)
result = PollListenerOperation(listener).run()

# Or the function-shaped wrapper (identical behavior)
result = poll_listener(listener)
```

## Scoping

- **Every domain model carries `account_id` + `user_id`.** `User` and `Account` themselves are exempt; they *are* those entities.
- **Every account-scoped service query filters by `self.account_id`.** Cross-tenant data leakage is impossible by construction. The only escape hatch is `<Service>.Global.*` for explicit system-level ops.

## Types

- All service functions, manager methods, helpers: fully type-annotated.
- `django-stubs` is installed so ty resolves `.objects`, manager generics, and field descriptors. When something still trips ty, fix it properly: explicit `ClassVar[Manager[Self]]` annotation, `cast()` at the field boundary, or a small helper in `common/`. `# type: ignore` is a last resort with the specific rule name, used only when no principled fix exists.
- Run `make dev-types` before declaring done. Don't reach for `# type: ignore`, `# noqa`, or workarounds to make checks pass; find the root cause.

## Typed-blob pattern (Listener & Event)

Both models carry queryable common fields top-level + a `data: JSONField` whose schema is owned by a Pydantic class.

- **`Listener.data`** is validated by a Pydantic config class keyed off `Listener.kind` (see `listeners.registry`). v0 only kind is `"semantic"` → `SemanticListenerConfig`. New kind = new Pydantic class + registry entry, no schema migration.
- **`Event.data`** is the full `Observation.model_dump()` of the observation that triggered the hit. `events.registry.hydrate(event)` returns the typed Observation.
- **Queryable fields stay top-level**: scoping, source/kind, dedup (`external_id`), time, delivery state (`delivered_at`).
- **Stream-specific identifiers** (subreddit, repo) live inside `data`, accessed via `Observation.stream_slug()`, which subclasses override.

## Event-sourced pipeline (hit-only)

`Event` rows exist **only** when a Listener's engine judged the observation relevant. Misses live and die in memory.

```
poll_due_listeners
  ↓ for listener in due:
      config  = listeners.registry.load_config(listener)   # SemanticListenerConfig
      engine  = engine.registry.get(config.engine.kind)
      for watch in config.streams:
          connector = sources.registry.get(watch.spec.kind)
          for obs in connector.poll(watch.spec, listener, since=watch.last_event_at):
              advance watch.last_event_at                  # high-water mark, in memory
              if engine.judge(obs, listener).hit:
                  event = events.services.persist_hit(obs, listener)
                  if listener.delivery_mode == Listener.DeliveryMode.INSTANT:
                      notifications.deliver_instant(event, obs, listener, config)
  ↓ one save per cycle: listener.data (config dump) + poll state
```

Forward-looking by default. A StreamWatch created with `last_event_at=None` (the default for a fresh listener) is treated as "snapshot now"; the polling op sets `last_event_at = timezone.now()` on the first cycle and yields zero observations. The next poll cadence catches whatever's accumulated since that snapshot.

Operators who want backfill set `last_event_at = now - timedelta(days=N)` explicitly at listener-create time. There is no implicit "scan all history" mode; if you want history, you ask for it by date.

**No surprise multi-hour cold-starts.** This rule exists so creating a listener never accidentally enqueues an hours-long LLM run on day one. If a future Listener needs deep history, build it as a separate feature with its own state model (cursor + horizon + completion flag); don't smuggle it into the watermark.

## Notifications & delivery state

- **Notifier specs embedded in `Listener.data.notifiers`** (Pydantic discriminated union over `kind`). Promote to a shared `SideEffect` model only when real cross-listener URL duplication shows up.
- **`Event.delivered_at`** = pending if null, set on success. Failures leave it null; the next pass retries.
- **`Listener.delivery_mode`** is `Listener.DeliveryMode.INSTANT` or `.DIGEST` (queryable, indexed alongside `next_digest_at`):
  - **Instant**: notifier fires inline after `persist_hit`; `delivered_at` set on full success.
  - **Digest**: hits accumulate. `deliver_due_digests` scheduler batches pending Events for each listener into one payload per notifier, bulk-marks delivered on success. Implicit retry via `next_digest_at`, failure leaves the high-water mark untouched, so the next cycle re-batches.
- **Webhook payload groups hits by `{source}:{slug}`** (slug from `Observation.stream_slug()`). One payload per batch.
- **`NotifierSpec.include_fields`**: empty list = include full Observation dump (minus scoping); explicit list = whitelist.

## Plugins (connectors, engines, notifiers)

Same shape inside each owning app:
```
app/
  <thing>s/
    base.py        # Protocol + shared DTOs
    <impl>.py      # concrete plugins
  registry.py      # name → instance
```

- Adding a new plugin = one file + one registry entry.
- Connector classes declare both `kind: str` and `observations: list[type[Observation]]`. The `register(...)` call at the bottom of the connector module references the class attrs; no string duplication.
- App `ready()` hooks import the registry so plugins self-register at Django startup, not lazily.

## HTTP API

- `djangorestframework` is in. All endpoints are DRF `APIView` CBVs.
- New API surfaces use the same pattern (CBV + serializer for input + serializer for output).
- Keep `API_VERSION_PREFIX` in `core/conf/settings/base.py` in lockstep with `NEXT_PUBLIC_API_VERSION` in the web app.
- **Every `/v1/` route is trailing-slash-optional.** Two helpers in `common/urls.py` make this work at every level of nesting:
  - **`api_include(prefix, module)`** for every mount of an app's urlconf into a parent (root-level and nested). Makes the trailing slash on the prefix optional.
  - **`api_path(route, view, name=...)`** for every leaf route inside a urlconf. Makes the trailing slash on the leaf optional.
  Use these instead of `path(..., include(...))` / `path(..., view)`. Django's `APPEND_SLASH` only fixes GETs (POST/PUT/etc. don't follow the 301), so matching both forms in URL resolution is the proper fix.

### Discriminated-config endpoints

For resources whose schema varies by `kind` (e.g. `Listener` with `kind=semantic`, future `kind=keyword`), the endpoint accepts an envelope containing a kind-specific `data` blob. Validate `data` via the Pydantic registry that already owns the typed-blob schema (`listeners.registry.get_config_class(kind).model_validate(...)`); don't duplicate the schema in a DRF serializer. Translate Pydantic `ValidationError` into DRF's nested 400 shape so a `data.streams[0].spec.kind` failure surfaces at the right path. Example: `listeners/serializers.py:ListenerCreateSerializer`.

## Cache-backed state pattern

When persisting structured state in `django.core.cache`:

- **Define a Pydantic model for the bag.** Never write raw `dict[str, Any]` directly into the cache.
- **Encapsulate I/O in a `Store` class** with `get` / `put` / `delete` statics. `Store` picks the TTL off the state's phase so callers don't pick TTLs by hand.
- **Views own the HTTP surface and auth checks. Store owns shape + I/O.** Views never reach into `cache` directly.
- Lifecycle transitions return a new state via a method on the model (e.g. `state.complete_with(...)`), preserving carried-over fields. The view writes the result back via `Store.put`.
- Example pairing: `core/auth_api/device_session_store.py` (state + Store) plus `core/auth_api/device_sessions.py` (views).

## Auth + identity

### Tokens

- **Issued by `django-oauth-toolkit`.** We don't drive its grant flows. `auth_api.services.tokens.mint_token_pair_for_user(user)` is the one seam that creates an `AccessToken` + `RefreshToken` pair against the singleton `magpie-cli` `Application` (public client). Revocation goes through `revoke_access_token(token)` in the same module; deletes the access row + revokes its paired refresh.
- **OAuth Application bootstrap**: `manage.py bootstrap_oauth_app` is idempotent and runs as part of `make dev-migrate`. Creates the `magpie-cli` Application; the client_id is irrelevant to our flow.

### One token model, two delivery mechanisms

- **Browser** holds the access-token value in an HttpOnly `auth_token` cookie set by `auth_api.cookies.set_auth_cookie`. We do NOT use Django's session middleware for auth; the cookie literally carries the OAuth `AccessToken.token` value.
- **CLI** holds the same kind of value in `~/.magpie/config.json` and sends it as `Authorization: Bearer <token>`.
- **Lookup** is unified: `auth_api.authentication.BearerOrCookieAuthentication` checks the Bearer header first, then the `auth_token` cookie. Registered globally via `REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES`.

### URL surface

```
/v1/auth/signup                          POST   browser  signup + cookie
/v1/auth/login                           POST   browser  login + cookie
/v1/auth/logout                          POST   browser  clear cookie + revoke its token
/v1/auth/me                              GET    either   {user} (IsAuthenticated)

/v1/auth/tokens/refresh                  POST   CLI      rotate bearer pair
/v1/auth/tokens/revoke                   POST   CLI      bearer "logout"

/v1/auth/device-sessions                 POST   CLI      start handshake
/v1/auth/device-sessions/{id}            GET    CLI      poll (header: X-Device-Secret)
/v1/auth/device-sessions/{id}/info       GET    browser  audit metadata (IsAuthenticated)
/v1/auth/device-sessions/{id}/deny       POST   browser  decline (IsAuthenticated)
/v1/auth/device-sessions/{id}/complete   POST   browser  authorize (IsAuthenticated)

/v1/listeners                            POST   either   create listener (IsAuthenticated)
/v1/listeners                            GET    either   list listeners in account (IsAuthenticated)

/healthz                                 GET    public   DB + cache pings
```

### Permission gating principle

- `permission_classes = [IsAuthenticated]` when the view needs an **identified user** (`/me`, `/device-sessions/{id}/info`, `/deny`, `/complete`).
- Open (`permission_classes = []`) when the endpoint consumes whatever credential is presented (signup, login, refresh, logout, revoke, device-session create/poll, healthz).
- Logout/revoke are intentionally open: gating breaks cleanup of stale credentials, which is exactly when callers need them most.

### CSRF defense for cookie auth

`BearerOrCookieAuthentication.authenticate` enforces an Origin-check on cookie-auth non-safe methods (anything other than GET/HEAD/OPTIONS/TRACE). Bearer requests are exempt. The allowed Origin list comes from `CORS_ALLOWED_ORIGINS` / `CSRF_TRUSTED_ORIGINS`. Per-view `permission_classes` do not need to re-check this.

### Device-flow handshake

`auth_api/device_sessions.py` is cache-backed via the cache-state pattern above. Pending TTL is 15 min (user has time to switch tabs); completed TTL is 5 min so leftover tokens don't linger after the CLI picks them up.

Three secrets with deliberately separate roles (RFC 8628):

- **`session_id`**, public, in URL. Identifies the session. Leaking it alone gives an attacker nothing.
- **`device_secret`**, CLI-only bearer for polling. Returned ONCE at create time, stored as SHA-256 on the server side. Required header on every `GET /device-sessions/{id}` poll.
- **`user_code`**, short human-typed verification code. Gates `POST /complete` so a phished browser session can't authorize an attacker's CLI without seeing the code the attacker's terminal shows.

### Cross-app access

`auth_api` consumes accounts data through services only:

- `accounts.services.UserService.Global.{create, email_exists, get}`
- `accounts.services.AccountService.Global.{create, get, primary_account_id_for}`
- `accounts.services.UserProfileService.Global.{bind_owner, primary_for_user, any_active_for_user}`

The signup multi-step (create User + Account + UserProfile inside one transaction) lives in `auth_api/operations/signup.py` as `SignupOperation` per the one-shot orchestrator pattern.

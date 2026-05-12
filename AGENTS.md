# AGENTS.md

Conventions for AI coding agents (Claude Code, Codex, Cursor, etc.) and human contributors.

## What is OpenMagpie

An open-source semantic listener. Tell it what to listen for; it picks out what matters from any stream and learns over time.

Three things stay pluggable across the codebase:
- **Connectors** (Reddit, GitHub, GDocs, Slack, ...) — yield typed `Observation` subclasses from each source
- **Engines** (Ollama, future Anthropic/OpenAI/keyword) — BYO LLM that judges an Observation against a Listener
- **Notifiers** (webhook, log, future Slack/email) — deliver hits as side effects

The product is **only** a listener: watches, judges, learns, notifies. It does NOT auto-reply, post back to sources, run workflows, or generate reports. Scope test: if a feature isn't listening / learning / notifying, it's out.

## Repo layout

```
core/
  common/          BaseModel (ULID PK + timestamps), ULIDField
  accounts/        User (email login), Account, UserProfile
  listeners/       Listener model + SemanticListenerConfig (Pydantic) + polling orchestrator
  events/          Event model + Observation hierarchy + per-(source,kind) registry
  sources/         Connectors (RedditSubRedditConnector, ...) + per-kind observation classes
  engine/          Engine Protocol + OllamaEngine + registry
  notifications/   Notifier Protocol (Webhook, Log) + instant/digest delivery
  conf/            settings (base, dev), urls, wsgi
make/              Per-concern Makefile targets
scripts/           Helper scripts (lint, whitespace, make-help)
```

## Models & data access

- **Every model inherits `common.models.BaseModel`** → ULID primary key + `created_at` + `updated_at`.
- **No `ForeignKey`. Char pointers only** — cross-model references are `CharField(max_length=26)` named `<thing>_id`. Stale data is OK; no cascades; no auto-indexes. Add `db_index=True` only when a specific lookup needs it.
- **No direct `Model.objects.*` outside the model's owning service module.** All access goes through `<app>/services/<resource>.py`.
- **Services are classes, not loose module functions.** One class per primary entity (e.g. `ListenerService`, `EventService`, `DeliveryService`). Instance methods for account-scoped operations; a nested `class Global:` (with `@staticmethod` methods) for cross-tenant operations.
- **Account-scoped services bind their scope in `__init__`** and raise `ValueError` if `account_id` is missing or empty. Methods then drop the `account_id=` kwarg — `self.account_id` is the single source of truth. Scoped services also assert that incoming domain objects match `self.account_id` (defense-in-depth at the seam — `ValueError` if a foreign-account object is handed in).
- **System-level operations live under `<Service>.Global`** as static methods. These are the only place cross-tenant queries happen; reach for them sparingly (schedulers, admin / debug entry points).
- **One-shot orchestrators are `Operation` classes**, not `Service` classes. Pattern: build with the domain object, call `.run()` once, discard. Use this when an action has internal state across helpers (counters, watermarks) and would otherwise force every helper to thread the same args. Example: `PollListenerOperation(listener).run()`. The `Service` suffix stays reserved for reusable, account-scoped services; `Operation` signals "single-use, not reusable."
- **Operations instantiate scoped services internally** from the domain object's `account_id` — callers just hand in a domain object. Service constructions belong on `@cached_property` so `__init__` stays validation-only.
- **Function-shaped wrappers** (e.g. `poll_listener(listener)`) may exist alongside an Operation for callers that prefer the function form (mgmt commands, scripts). The wrapper is one line: `return PollListenerOperation(listener).run()`.
- **`get`/`get_by_<field>` raise `DoesNotExist`** — never return `None`. Type stays `-> Model`; callers handle missing via `try/except`. If "might not exist" is the normal path, add a separate `find_by_<field>` returning `Model | None`.
- **Return iterators for collections.** Use `.iterator(chunk_size=N)`; callers `list(...)` if they need to materialize. Bulk writes use `.bulk_create()` / `.bulk_update()`.
- **Every query hits an index.** Every column in a service WHERE must be indexed via `db_index=True`, a `UniqueConstraint`, or `Meta.indexes`. Don't add an explicit index if a `UniqueConstraint` already left-prefix-covers the read path.

### Call-site shape

```python
# Account-scoped (the common case)
svc = ListenerService(account_id=account_id)
listener = svc.get(id)
svc.update_poll_state(listener, last_polled_at=now, data=...)

# Cross-tenant (rare — scheduler, admin)
for listener in ListenerService.Global.list_due_for_poll(now=now):
    ...

# One-shot Operation (recommended)
result = PollListenerOperation(listener).run()

# Or the function-shaped wrapper (identical behavior)
result = poll_listener(listener)
```

## Scoping

- **Every domain model carries `account_id` + `user_id`.** `User` and `Account` themselves are exempt — they *are* those entities.
- **Every account-scoped service query filters by `self.account_id`**. Cross-tenant data leakage is impossible by construction. The only escape hatch is `<Service>.Global.*` for explicit system-level ops.

## Naming

- The unit of attention is a **`Listener`** — not Context, not Brief, not Beat.
- An ingested hit is an **`Event`** (Django model). The in-memory typed version is an **`Observation`** (Pydantic).
- Source connectors are named for the kind of variant: **`RedditSubRedditConnector`** (kind=`"reddit_subreddit"`). Future Reddit variants (user feed, search, comments) get their own connector + kind.
- Events from sources are named for *what happened*: **`NewRedditPostObservation`** (`EVENT_KIND="new_post"`).
- The relevance verdict is a **`JudgmentResult`** (in-memory dataclass — no Judgment model yet).

## Types

- All service functions, manager methods, helpers — fully type-annotated.
- `django-stubs` is installed so ty resolves `.objects`, manager generics, and field descriptors. When something still trips ty, fix it *properly*: explicit `ClassVar[Manager[Self]]` annotation, `cast()` at the field boundary, or a small helper in `common/`. `# type: ignore` is a last resort with the specific rule name, used only when no principled fix exists.
- Run `make dev-types` before declaring done. Don't reach for `# type: ignore`, `# noqa`, or workarounds to make checks pass — find the root cause.

## Typed-blob pattern (Listener & Event)

Both models carry queryable common fields top-level + a `data: JSONField` whose schema is owned by a Pydantic class.

- **`Listener.data`** is validated by a Pydantic config class keyed off `Listener.kind` (see `listeners.registry`). v0 only kind is `"semantic"` → `SemanticListenerConfig`. New kind = new Pydantic class + registry entry, no schema migration.
- **`Event.data`** is the full `Observation.model_dump()` of the observation that triggered the hit. `events.registry.hydrate(event)` returns the typed Observation.
- **Queryable fields stay top-level**: scoping, source/kind, dedup (`external_id`), time, delivery state (`delivered_at`).
- **Stream-specific identifiers** (subreddit, repo) live inside `data` — accessed via `Observation.stream_slug()`, which subclasses override.

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

Forward-looking by default. Bootstrap recent history by setting `StreamWatch.last_event_at = now - timedelta(days=N)`. No "scan all history" mode.

**Live-only polling contract.** Cold start (StreamWatch with `last_event_at=None`) yields whatever a single poll cycle returns from the connector — for Reddit /new that's bounded by `MAX_PAGES × PAGE_SIZE` *and* by Reddit's own ~1000-item ceiling. Once the watermark is set to the newest item seen, every subsequent cycle is pure live mode: only items newer than `last_event_at` are yielded. **There is no historical backfill.** Posts older than the moment we cold-started are out of scope. If a future Listener needs deep history, build it as a separate feature with its own state model (cursor + horizon + completion flag) — don't smuggle it into the watermark.

## Notifications & delivery state

- **Notifier specs embedded in `Listener.data.notifiers`** (Pydantic discriminated union over `kind`). Promote to a shared `SideEffect` model only when real cross-listener URL duplication shows up.
- **`Event.delivered_at`** = pending if null, set on success. Failures leave it null; the next pass retries.
- **`Listener.delivery_mode`** is `Listener.DeliveryMode.INSTANT` or `.DIGEST` (queryable, indexed alongside `next_digest_at`):
  - **Instant**: notifier fires inline after `persist_hit`; `delivered_at` set on full success.
  - **Digest**: hits accumulate. `deliver_due_digests` scheduler batches pending Events for each listener into one payload per notifier, bulk-marks delivered on success. Implicit retry via `next_digest_at` — failure leaves the high-water mark untouched, so the next cycle re-batches.
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
- Connector classes declare both `kind: str` and `observations: list[type[Observation]]`. The `register(...)` call at the bottom of the connector module references the class attrs — no string duplication.
- App `ready()` hooks import the registry so plugins self-register at Django startup, not lazily.

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

## Dev loop

```
make build           # build images and start
make up / down       # start / stop stack
make logs            # tail everything
make dev-migrate     # run migrations
make dev-makemigrations ARGS="<app> --name <descriptive_name>"
make dev-manage CMD=createsuperuser
make dev-dbshell     # SQLite shell via Django dbshell
make dev-test
make dev-lint        # ruff + whitespace/trailing-newline
make dev-lint-fix    # auto-fix
make dev-types       # ty
make dev-check       # lint + types + test
make help            # full list
```

Apps are created with `python manage.py startapp <name>` inside the container (`make dev-manage CMD="startapp <name>"`), then customized per these conventions.

## Stack

Current v0: **Django + SQLite. That's it.** Postgres-swap is one settings change away if scale demands it.

Deliberately deferred until concrete need:
- **Redis / Celery / Celery-beat** — when async or scheduled work shows up
- **Garage** (S3-compatible blob storage; NOT MinIO) — when we need blobs
- **DRF** — when there's a real API surface; stdlib views fine for now
- **Web UI / Django admin** — `manage.py shell` or custom commands for v0

Do NOT proactively re-add deferred infra. Wait for a concrete need.

## Tooling preferences

Prefer OSS-aligned / community-governed tools over commercial-OSS hybrids with a history of license rugs.
- **Blob storage** (when needed): Garage, not MinIO
- **Type checker**: ty (not mypy unless ty proves insufficient)

# AGENTS.md

Conventions for AI coding agents (Claude Code, Codex, Cursor, etc.) and human contributors.

This file is cross-cutting only. Each top-level app owns its own conventions:

- [core/AGENTS.md](core/AGENTS.md) — Django backend (models, services, auth, plugins)
- [cli/AGENTS.md](cli/AGENTS.md) — `magpie` CLI (Typer + httpx + Pydantic)
- [web/AGENTS.md](web/AGENTS.md) — pnpm workspace (Next.js + shared packages)

When working in `core/`, `cli/`, or `web/`, load the matching `AGENTS.md` alongside this one.

## What is OpenMagpie

An open-source semantic listener. Tell it what to listen for; it picks out what matters from any stream and learns over time.

Three things stay pluggable across the codebase:
- **Connectors** (Reddit, GitHub, GDocs, Slack, ...) — yield typed `Observation` subclasses from each source
- **Engines** (Ollama, future Anthropic/OpenAI/keyword) — BYO LLM that judges an Observation against a Listener
- **Notifiers** (webhook, log, future Slack/email) — deliver hits as side effects

The product is **only** a listener: watches, judges, learns, notifies. It does NOT auto-reply, post back to sources, run workflows, or generate reports. Scope test: if a feature isn't listening / learning / notifying, it's out.

## Repo layout

```
core/      Django backend (see core/AGENTS.md)
web/       pnpm workspace, Next.js + shared packages (see web/AGENTS.md)
cli/       magpie CLI (see cli/AGENTS.md)
make/      Per-concern Makefile targets
scripts/   Helper scripts (lint, whitespace, make-help)
```

## Naming (cross-cutting domain vocabulary)

- The unit of attention is a **`Listener`**. Not Context, not Brief, not Beat.
- An ingested hit is an **`Event`** (Django model). The in-memory typed version is an **`Observation`** (Pydantic).
- Source connectors are named for the variant: **`RedditSubRedditConnector`** (kind=`"reddit_subreddit"`). Future Reddit variants get their own connector + kind.
- Events from sources are named for *what happened*: **`NewRedditPostObservation`** (`EVENT_KIND="new_post"`).
- The relevance verdict is a **`JudgmentResult`** (in-memory dataclass; no `Judgment` model yet).

## Cross-cutting code rules

- **State-machine values get a const object + derived type from the start.** No bare string literals in match arms or status checks. Python: `class Status(Enum): ...`. TypeScript: `const PHASE = {...} as const; type Phase = typeof PHASE[keyof typeof PHASE]`.
- **No em dashes.** Use commas or periods. Applies to UI text, comments, docs.
- **Convention docs describe what to do.** No justifications, no historical context, no "we chose X because of Y." Forward-looking constraints are fine; past-decision narratives are not.

## Stack

Current v0: **Django + SQLite. That's it.** Postgres-swap is one settings change away if scale demands it.

- Web: pnpm + Next.js 16 + React 19 + Tailwind v4 + zod.
- CLI: Typer + httpx + Pydantic.

Deliberately deferred until concrete need:
- **Redis / Celery / Celery-beat** when async or scheduled work shows up
- **Garage** (S3-compatible blob storage; NOT MinIO) when we need blobs
- **Django admin** — `manage.py shell` or custom commands for v0

Do NOT proactively re-add deferred infra. Wait for a concrete need.

## Tooling preferences

Prefer OSS-aligned / community-governed tools over commercial-OSS hybrids with a history of license rugs.
- **Blob storage** (when needed): Garage, not MinIO
- **Type checker**: ty (not mypy unless ty proves insufficient)

## Dev loop

```
make build              build images and start
make up / down          start / stop stack
make logs               tail everything
make dev-migrate        run migrations
make dev-makemigrations ARGS="<app> --name <descriptive_name>"
make dev-test
make dev-lint           ruff + whitespace/trailing-newline
make dev-lint-fix       auto-fix
make dev-types          ty
make dev-check          lint + types + test
make help               full list
```

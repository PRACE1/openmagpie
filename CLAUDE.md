See [AGENTS.md](AGENTS.md) for cross-cutting conventions.

Monorepo: uv workspace. `apps/*` (deployables) + `packages/*` (shared
libs), one root `uv.lock`. `web/` is a separate pnpm workspace.

Per-subtree:
- [apps/core/AGENTS.md](apps/core/AGENTS.md) — Django backend
- [apps/cli/AGENTS.md](apps/cli/AGENTS.md) — `magpie` CLI
- [packages/openmagpie-schema](packages/openmagpie-schema) — pure Pydantic models shared by core + cli
- [web/AGENTS.md](web/AGENTS.md) — pnpm workspace (Next.js + shared packages)

# uv workspace image for the `openmagpie-core` member.
#
# Build context is the REPO ROOT (see docker-compose.yml): the single
# uv.lock + every member's pyproject.toml live there and uv resolves
# the whole workspace. Canonical uv pattern:
#   1. deps-only layer from the lock (cached) -> --no-install-workspace
#   2. copy the repo, then `uv sync --frozen --package openmagpie-core`
# `--frozen` (not `--locked`): uv can't assert the lock is current
# without every member pyproject, which only exist after the full copy.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# PYTHONUNBUFFERED: real-time management-command output when piped.
# UV_LINK_MODE=copy: avoid hardlink warnings across the cache mount.
ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Deps-only layer: just the manifests needed to resolve the workspace,
# so this cache layer only busts when dependencies change.
COPY pyproject.toml uv.lock ./
COPY apps/core/pyproject.toml apps/core/pyproject.toml
COPY apps/cli/pyproject.toml apps/cli/pyproject.toml
COPY packages/openmagpie-schema/pyproject.toml packages/openmagpie-schema/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace

# Full workspace, then install just this member (+ its workspace deps,
# e.g. openmagpie-schema).
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package openmagpie-core

EXPOSE 8000
# manage.py lives at apps/core/manage.py; `python apps/core/manage.py`
# puts apps/core on sys.path[0] so `conf.settings`, app packages, etc.
# import exactly as before the reshuffle.
CMD ["uv", "run", "--package", "openmagpie-core", \
     "python", "apps/core/manage.py", "runserver", "0.0.0.0:8000"]

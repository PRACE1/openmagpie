#!/usr/bin/env bash
#
# Enforce the branch-name convention: <type>/<kebab-slug>, where <type> is a
# Conventional-Commits prefix. Used by BOTH the pre-commit hook (validates the
# current branch) and CI (validates a PR's source branch) so the rule lives in
# one place.
#
#   feat/leaf-only-action-cli   fix/poll-lock-lease   ci/branch-naming
#
# `main` is exempt (the default branch carries no type prefix). A detached
# HEAD (mid-rebase, or CI's detached checkout) is skipped, so this never
# blocks a rebase or a non-PR build.
#
# Usage:
#   ./scripts/check-branch-name.sh [branch-name]
# With no argument it reads the current branch from git.

set -euo pipefail

branch="${1:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)}"

# Detached HEAD (rebase in progress, CI checkout): nothing to validate.
[[ "$branch" == "HEAD" || -z "$branch" ]] && exit 0
# The default branch is exempt.
[[ "$branch" == "main" ]] && exit 0

pattern='^(feat|fix|docs|refactor|test|chore|ci|perf|build|style|revert)/[a-z0-9][a-z0-9._-]*$'
if [[ "$branch" =~ $pattern ]]; then
    exit 0
fi

cat >&2 <<'EOF'
Branch name doesn't match the convention: <type>/<kebab-slug>
  e.g.  feat/leaf-only-action-cli   fix/poll-lock-lease   ci/branch-naming

  type      when to use it
  --------  ------------------------------------------------------------
  feat      a new user-facing capability
  fix       a bug fix
  perf      a performance improvement (behavior unchanged)
  refactor  restructure code; no behavior or API change
  docs      documentation only
  test      add or correct tests only
  ci        CI / workflows / pipeline config
  build     build system, dependencies, packaging
  chore     maintenance / tooling that doesn't touch src behavior
  style     formatting / whitespace only; no logic change
  revert    revert a previous change

  slug : lowercase letters / digits / - . _  (starts alphanumeric)

Rename the current branch with:  git branch -m <new-name>
EOF
exit 1

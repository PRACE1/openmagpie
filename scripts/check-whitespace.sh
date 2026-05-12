#!/usr/bin/env bash
#
# Checks tracked text files for trailing whitespace and missing final newlines.
# Uses git to find tracked text files — no hardcoded extensions, binaries skipped.
#
# Usage:
#   ./scripts/check-whitespace.sh          # check only
#   ./scripts/check-whitespace.sh --fix    # fix all issues in-place

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

fix=false
[ "${1:-}" = "--fix" ] && fix=true

files=$(git grep -Il '' || true)
[ -z "$files" ] && exit 0

errors=0

# --- Trailing whitespace ---

trailing=$(echo "$files" | xargs grep -ln '[[:blank:]]$' /dev/null 2>/dev/null || true)

if [ -n "$trailing" ]; then
    echo "Trailing whitespace:"
    for f in $trailing; do
        echo "  $f"
        if $fix; then
            sed -i '' 's/[[:blank:]]*$//' "$f"
        fi
    done
    if $fix; then
        echo "  Fixed."
    else
        errors=1
    fi
else
    echo "Trailing whitespace: OK"
fi

# --- Missing final newline ---

missing=""
for f in $files; do
    if [ -s "$f" ] && [ "$(tail -c1 "$f")" != "" ]; then
        missing="$missing $f"
    fi
done

if [ -n "$missing" ]; then
    echo "Missing final newline:"
    for f in $missing; do
        echo "  $f"
        if $fix; then
            echo "" >> "$f"
        fi
    done
    if $fix; then
        echo "  Fixed."
    else
        errors=1
    fi
else
    echo "Missing final newline: OK"
fi

exit $errors

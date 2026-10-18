#!/usr/bin/env bash
# Block private identifiers from reaching this public repo.
#
# Greps the staged diff (default) or every tracked file (--all) against a
# private denylist: one extended regex per line, '#' comments allowed. The
# denylist is never committed - it would leak the very names it guards - so it
# lives outside the repo at $CMDB_DENYLIST. If that file is absent the check
# passes silently, which keeps it harmless for other clones and in CI.
#
# Install as a pre-commit hook:
#   printf '#!/bin/sh\nexec scripts/check-public-leaks.sh\n' > .git/hooks/pre-commit
#   chmod +x .git/hooks/pre-commit
set -euo pipefail

denylist="${CMDB_DENYLIST:-$HOME/.config/homelabcmdb/public-denylist}"
[ -f "$denylist" ] || exit 0

patterns="$(mktemp)"
trap 'rm -f "$patterns"' EXIT
grep -vE '^[[:space:]]*(#|$)' "$denylist" > "$patterns" || exit 0

if [ "${1:-}" = "--all" ]; then
  if hits="$(git grep -nIiE -f "$patterns" -- . ':!*.lock')"; then
    echo "check-public-leaks: denylisted terms in tracked files:" >&2
    echo "$hits" >&2
    exit 1
  fi
else
  # Only added lines matter; removing a leak must never be blocked.
  if hits="$(git diff --cached -U0 --no-color -- . ':!*.lock' \
      | grep -E '^\+' | grep -vE '^\+\+\+ ' | grep -iE -f "$patterns")"; then
    echo "check-public-leaks: denylisted terms in staged changes:" >&2
    echo "$hits" >&2
    echo "Remove them, or bypass with 'git commit --no-verify' if this is a false positive." >&2
    exit 1
  fi
fi

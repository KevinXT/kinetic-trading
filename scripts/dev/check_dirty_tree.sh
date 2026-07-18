#!/usr/bin/env bash
# Exit 0 when the git work tree is clean; exit 1 when dirty.
#
# Ordinary development targets must not call this script.
# Release-oriented targets (source-archive, release-check) must call it.
#
# Override for intentional dirty release archives:
#   ALLOW_DIRTY_TREE=1 make source-archive
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

if [[ "${ALLOW_DIRTY_TREE:-}" == "1" ]]; then
  echo "dirty-tree check: overridden (ALLOW_DIRTY_TREE=1)"
  git status --porcelain || true
  exit 0
fi

porcelain="$(git status --porcelain)"
if [[ -n "$porcelain" ]]; then
  echo "error: git work tree is dirty; refuse release-oriented action." >&2
  echo "Commit or stash changes, or set ALLOW_DIRTY_TREE=1 to override." >&2
  echo >&2
  echo "$porcelain" >&2
  exit 1
fi

echo "dirty-tree check: clean"
exit 0

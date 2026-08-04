#!/usr/bin/env bash
# Fail when package/app build/ trees are present in the checkout.
#
# setuptools leaves packages/*/build/lib after `python -m build`. Those trees
# can shadow editable installs when the repo root is on sys.path.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

found=0
shopt -s nullglob
for path in packages/*/build apps/*/build; do
  if [[ -d "$path" ]]; then
    if [[ "$found" -eq 0 ]]; then
      echo "error: stale build directories present (import shadow risk):" >&2
    fi
    echo "  $path" >&2
    if [[ -d "$path/lib" ]]; then
      echo "  $path/lib" >&2
    fi
    found=1
  fi
done
shopt -u nullglob

if [[ "$found" -ne 0 ]]; then
  echo >&2
  echo "Run: make clean-build" >&2
  exit 1
fi

echo "build-pollution check: clean"
exit 0

#!/usr/bin/env bash
# Remove setuptools build/ trees under packages/* and apps/*.
# Does not remove *.egg-info (editable installs need those).
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

removed=0
shopt -s nullglob
for path in packages/*/build apps/*/build; do
  if [[ -e "$path" ]]; then
    rm -rf "$path"
    echo "removed $path"
    removed=$((removed + 1))
  fi
done
shopt -u nullglob

if [[ "$removed" -eq 0 ]]; then
  echo "clean-build: nothing to remove"
else
  echo "clean-build: removed $removed path(s)"
fi

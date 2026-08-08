#!/usr/bin/env bash
# Remove the setuptools build/ tree. Leaves *.egg-info (editable installs need it).
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
if [[ -e build ]]; then
  rm -rf build
  echo "clean-build: removed build/"
else
  echo "clean-build: nothing to remove"
fi

#!/usr/bin/env bash
# Fail when a stale build/ tree is present in the checkout.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
if [[ -d build ]]; then
  echo "error: stale build directory present (import shadow risk): build/" >&2
  echo >&2
  echo "Run: make clean-build" >&2
  exit 1
fi
echo "build-pollution check: clean"
exit 0

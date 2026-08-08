#!/usr/bin/env bash
# Black --check over the whole repository.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
exec black --check src tests tools projects scripts

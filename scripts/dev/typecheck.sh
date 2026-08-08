#!/usr/bin/env bash
# mypy over the single source tree.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
exec mypy src

#!/usr/bin/env bash
# Build installable wheels outside the checkout import path and smoke-import them.
#
# Matches CI semantics: temp wheelhouse, imports from a non-repo cwd, clean build/ after.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WHEELHOUSE="$(mktemp -d "${TMPDIR:-/tmp}/kinetic-wheels.XXXXXX")"
SMOKE_VENV="$(mktemp -d "${TMPDIR:-/tmp}/kinetic-smoke.XXXXXX")"
SMOKE_CWD="$(mktemp -d "${TMPDIR:-/tmp}/kinetic-smoke-cwd.XXXXXX")"

cleanup() {
  "$SCRIPT_DIR/clean_build.sh" >/dev/null || true
  rm -rf "$WHEELHOUSE" "$SMOKE_VENV" "$SMOKE_CWD"
}
trap cleanup EXIT

mkdir -p "$WHEELHOUSE"

python -m pip install --quiet build
python -m build --wheel --outdir "$WHEELHOUSE" ./packages/common
python -m build --wheel --outdir "$WHEELHOUSE" ./packages/pipeline_core
python -m build --wheel --outdir "$WHEELHOUSE" ./packages/news_data
python -m build --wheel --outdir "$WHEELHOUSE" ./packages/market_data
python -m build --wheel --outdir "$WHEELHOUSE" ./packages/research_data
python -m build --wheel --outdir "$WHEELHOUSE" ./apps/trading_platform

python -m venv "$SMOKE_VENV"
"${SMOKE_VENV}/bin/python" -m pip install --upgrade pip
"${SMOKE_VENV}/bin/python" -m pip install "$WHEELHOUSE"/*.whl

(
  cd "$SMOKE_CWD"
  "${SMOKE_VENV}/bin/python" -c "import common, pipeline_core, news_data, market_data, research_data, trading_platform"
  "${SMOKE_VENV}/bin/python" -c "from market_data.domain import PriceBar; from market_data.providers.alpaca import AlpacaPriceProvider"
)

echo "wheels-smoke: ok"

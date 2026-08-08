#!/usr/bin/env bash
# Build the wheel, install it into a throwaway environment, and prove it works
# from a working directory that is NOT the repository — so nothing can pass by
# accidentally importing from the source tree.
#
# Uses uv when it is available (as CI does) and falls back to pip otherwise.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WHEELHOUSE="$(mktemp -d "${TMPDIR:-/tmp}/kinetic-wheel.XXXXXX")"
SMOKE_VENV="$(mktemp -d "${TMPDIR:-/tmp}/kinetic-smoke.XXXXXX")"
SMOKE_CWD="$(mktemp -d "${TMPDIR:-/tmp}/kinetic-smoke-cwd.XXXXXX")"

cleanup() {
  "$SCRIPT_DIR/clean_build.sh" >/dev/null || true
  rm -rf "$WHEELHOUSE" "$SMOKE_VENV" "$SMOKE_CWD"
}
trap cleanup EXIT

if command -v uv >/dev/null 2>&1; then
  uv build --wheel --out-dir "$WHEELHOUSE" .
  uv venv --quiet "$SMOKE_VENV"
  VIRTUAL_ENV="$SMOKE_VENV" uv pip install --quiet "$WHEELHOUSE"/*.whl
else
  python -m pip install --quiet build
  python -m build --wheel --outdir "$WHEELHOUSE" .
  python -m venv "$SMOKE_VENV"
  "${SMOKE_VENV}/bin/python" -m pip install --quiet --upgrade pip
  "${SMOKE_VENV}/bin/python" -m pip install --quiet "$WHEELHOUSE"/*.whl
fi

(
  cd "$SMOKE_CWD"
  "${SMOKE_VENV}/bin/python" -c "
import kinetic
print('import ok:', kinetic.__version__, kinetic.__file__)
"
  "${SMOKE_VENV}/bin/python" -c "
from kinetic.bootstrap import build_default_registry
from kinetic.data.schemas.market import PriceBar
from kinetic.ingestion.market.alpaca import AlpacaPriceProvider

registry = build_default_registry()
assert 'research.build_news_market_dataset' in registry.task_ids(), registry.task_ids()
assert PriceBar is not None and AlpacaPriceProvider is not None
print('registry ok:', len(registry), 'tasks')
"
  "${SMOKE_VENV}/bin/python" -c "
from kinetic.ingestion.warehouse.bigquery.reporting.views import discover_sql_files
files = discover_sql_files()
assert files, 'reporting SQL package data missing from the wheel'
print('package data ok:', len(files), 'sql files')
"
  "${SMOKE_VENV}/bin/kinetic" --help >/dev/null
  "${SMOKE_VENV}/bin/kinetic" --version
  "${SMOKE_VENV}/bin/kinetic" task list >/dev/null
)

echo "wheel-smoke: ok"

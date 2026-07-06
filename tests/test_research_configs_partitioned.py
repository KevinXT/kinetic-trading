"""
Guard tests: every *active* BigQuery GDELT research config must use
``_PARTITIONTIME`` partition pruning. Unpartitioned configs are allowed only
under ``configs/research/legacy/`` (and must be named ``*_unpartitioned*``).

On ``gdelt-bq.gdeltv2.gkg_partitioned`` the ``DATE`` column does not prune
partitions, so a ``DATE BETWEEN``-only filter scans the whole table. These tests
make sure an unsafe config can't quietly re-enter the normal workflow.
"""

from pathlib import Path

import pytest
from common.config_builder import load_yaml
from news_data.bigquery.gdelt_queries import build_gdelt_daily_counts_query

RESEARCH_DIR = Path("configs/research")
LEGACY_DIR = RESEARCH_DIR / "legacy"

BIGQUERY_SOURCE = "bigquery_gdelt_counts"
THEME_DISCOVERY_SOURCE = "bigquery_gdelt_theme_discovery"

# Any BigQuery GDELT source must scan the partitioned table, so all of these
# must enable _PARTITIONTIME pruning.
BIGQUERY_SOURCES = {BIGQUERY_SOURCE, THEME_DISCOVERY_SOURCE}


def _ingest(cfg: dict) -> dict:
    return ((cfg.get("pipeline", {}) or {}).get("ingest", {}) or {})


def _is_bigquery_gdelt(cfg: dict) -> bool:
    return _ingest(cfg).get("source") in BIGQUERY_SOURCES


def _active_configs() -> list[Path]:
    # Top-level research configs only (exclude the legacy/ subdirectory).
    return sorted(p for p in RESEARCH_DIR.glob("*.yaml") if p.is_file())


def _legacy_configs() -> list[Path]:
    return sorted(LEGACY_DIR.glob("*.yaml")) if LEGACY_DIR.is_dir() else []


def test_active_research_dir_has_bigquery_configs() -> None:
    # Sanity: the active set is non-empty so the guard below is meaningful.
    bq = [p for p in _active_configs() if _is_bigquery_gdelt(load_yaml(p))]
    assert bq, "expected at least one active BigQuery GDELT config under configs/research/"


@pytest.mark.parametrize("path", _active_configs(), ids=lambda p: p.name)
def test_active_bigquery_configs_enable_partition_pruning(path: Path) -> None:
    cfg = load_yaml(path)
    if not _is_bigquery_gdelt(cfg):
        pytest.skip("not a BigQuery GDELT config")
    pf = _ingest(cfg).get("partition_filter", {}) or {}
    assert pf.get("enabled") is True, f"{path.name} must set partition_filter.enabled: true"
    assert pf.get("column") == "_PARTITIONTIME", f"{path.name} must prune on _PARTITIONTIME"
    assert pf.get("type") == "timestamp", f"{path.name} must use partition_filter.type: timestamp"


def test_no_unpartitioned_bigquery_config_is_active() -> None:
    offenders = []
    for path in _active_configs():
        cfg = load_yaml(path)
        if not _is_bigquery_gdelt(cfg):
            continue
        pf = _ingest(cfg).get("partition_filter", {}) or {}
        if not pf.get("enabled"):
            offenders.append(path.name)
    assert not offenders, (
        "unpartitioned BigQuery configs must live under configs/research/legacy/: "
        f"{offenders}"
    )


@pytest.mark.parametrize("path", _legacy_configs(), ids=lambda p: p.name)
def test_legacy_configs_are_marked_unpartitioned(path: Path) -> None:
    assert "_unpartitioned" in path.name, (
        f"legacy config {path.name} should be renamed with '_unpartitioned'"
    )
    text = path.read_text(encoding="utf-8")
    assert "LEGACY / UNSAFE FOR NORMAL USE" in text, (
        f"legacy config {path.name} must carry the LEGACY/UNSAFE warning comment"
    )


def test_recommended_partition_configs_exist() -> None:
    for name in (
        "inflation_rates_bigquery_7d_partition_dryrun.yaml",
        "inflation_rates_bigquery_30d_partition_dryrun.yaml",
        "inflation_rates_bigquery_30d_partition_execute.yaml",
        "inflation_rates_bigquery_1y_partition_dryrun.yaml",
    ):
        assert (RESEARCH_DIR / name).is_file(), f"missing recommended config {name}"


def test_partition_configs_set_a_positive_cap() -> None:
    # maximum_bytes_billed must always be set (>0) on every active config so a
    # query can never run uncapped. Exact values are operator-tunable.
    for path in _active_configs():
        cfg = load_yaml(path)
        if not _is_bigquery_gdelt(cfg):
            continue
        cap = (_ingest(cfg).get("cost_controls", {}) or {}).get("maximum_bytes_billed")
        assert isinstance(cap, int) and cap > 0, (
            f"{path.name} must set a positive maximum_bytes_billed"
        )


def test_no_one_year_execute_config_yet() -> None:
    assert not (RESEARCH_DIR / "inflation_rates_bigquery_1y_partition_execute.yaml").is_file()


def test_builder_emits_partition_and_date_filters() -> None:
    sql = build_gdelt_daily_counts_query(
        table="gdelt-bq.gdeltv2.gkg_partitioned",
        start_date="2026-05-24",
        end_date="2026-05-30",
        query_terms=["ECON_INFLATION"],
        search_columns=["V2Themes"],
        partition_filter={"enabled": True, "column": "_PARTITIONTIME", "type": "timestamp"},
    )
    assert '_PARTITIONTIME >= TIMESTAMP("2026-05-24")' in sql
    assert '_PARTITIONTIME < TIMESTAMP("2026-05-31")' in sql
    assert "DATE BETWEEN 20260524000000 AND 20260530235959" in sql
    assert "SELECT *" not in sql


def test_readme_references_partition_configs() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    for name in (
        "inflation_rates_bigquery_7d_partition_dryrun.yaml",
        "inflation_rates_bigquery_30d_partition_dryrun.yaml",
        "inflation_rates_bigquery_30d_partition_execute.yaml",
        "inflation_rates_bigquery_1y_partition_dryrun.yaml",
    ):
        assert name in readme, f"README must reference {name}"


# ── theme discovery configs ──────────────────────────────────────────────────

_DISCOVERY_DRYRUN = "debug_gdelt_themes_inflation_30d_partition_dryrun.yaml"
_DISCOVERY_EXECUTE = "debug_gdelt_themes_inflation_30d_partition_execute.yaml"


def test_theme_discovery_configs_exist() -> None:
    assert (RESEARCH_DIR / _DISCOVERY_DRYRUN).is_file()
    assert (RESEARCH_DIR / _DISCOVERY_EXECUTE).is_file()


@pytest.mark.parametrize("name", [_DISCOVERY_DRYRUN, _DISCOVERY_EXECUTE])
def test_theme_discovery_configs_use_partition_pruning(name: str) -> None:
    cfg = load_yaml(RESEARCH_DIR / name)
    ingest = _ingest(cfg)
    assert ingest.get("source") == THEME_DISCOVERY_SOURCE
    pf = ingest.get("partition_filter", {}) or {}
    assert pf.get("enabled") is True
    assert pf.get("column") == "_PARTITIONTIME"
    assert pf.get("type") == "timestamp"


def test_theme_discovery_dryrun_is_dry_run() -> None:
    cfg = load_yaml(RESEARCH_DIR / _DISCOVERY_DRYRUN)
    cc = _ingest(cfg).get("cost_controls", {}) or {}
    assert cc.get("dry_run") is True
    assert cc.get("execute_query", "") != "ENABLE"


def test_theme_discovery_execute_enables_execution() -> None:
    cfg = load_yaml(RESEARCH_DIR / _DISCOVERY_EXECUTE)
    cc = _ingest(cfg).get("cost_controls", {}) or {}
    assert cc.get("dry_run") is False
    assert cc.get("execute_query") == "ENABLE"


@pytest.mark.parametrize("name", [_DISCOVERY_DRYRUN, _DISCOVERY_EXECUTE])
def test_theme_discovery_configs_disable_cache(name: str) -> None:
    # Discovery is a debug path: stale cache (e.g. an earlier 0-row result) is
    # actively harmful, so these configs must default to use_cache: false.
    cfg = load_yaml(RESEARCH_DIR / name)
    cc = _ingest(cfg).get("cost_controls", {}) or {}
    assert cc.get("use_cache") is False


def test_theme_bundles_config_exists() -> None:
    assert Path("configs/gdelt_theme_bundles.yaml").is_file()


def test_readme_references_theme_discovery_and_bundles() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert _DISCOVERY_DRYRUN in readme
    assert _DISCOVERY_EXECUTE in readme
    assert "theme bundle" in readme.lower()
    assert "gdelt_theme_bundles.yaml" in readme

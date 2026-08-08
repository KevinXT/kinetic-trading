"""Tests for the seeded (record-based) GDELT theme-discovery builder + task."""

import copy
import json
from pathlib import Path

import pytest

from kinetic.core.errors import CostGuardrailError
from kinetic.core.pipeline.context import RunContext
from kinetic.ingestion.news.gdelt.bigquery.queries import build_gdelt_seeded_theme_discovery_query
from kinetic.ingestion.news.gdelt.bigquery.tasks import seeded_theme_discovery as task_mod
from kinetic.ingestion.news.gdelt.bigquery.tasks.seeded_theme_discovery import (
    bigquery_gdelt_seeded_theme_discovery_task,
)
from kinetic.ingestion.warehouse.bigquery.client import (
    BigQueryEstimate,
    SafeQueryDecision,
    SafeQueryResult,
)
from kinetic.ingestion.warehouse.bigquery.sql_guardrails import validate_bigquery_sql

TABLE = "gdelt-bq.gdeltv2.gkg_partitioned"
_PARTITION = {"enabled": True, "column": "_PARTITIONTIME", "type": "timestamp"}


def _build(**kw):
    params = dict(
        table=TABLE,
        start_date="2026-06-01",
        end_date="2026-06-30",
        topic="semiconductors",
        seed_terms=["NVIDIA", "TSMC", "semiconductor"],
        partition_filter=_PARTITION,
    )
    params.update(kw)
    return build_gdelt_seeded_theme_discovery_query(**params)


# ── SQL builder ───────────────────────────────────────────────────────────────


def test_seeded_query_is_partition_pruned() -> None:
    sql = _build()
    assert '_PARTITIONTIME >= TIMESTAMP("2026-06-01")' in sql
    assert '_PARTITIONTIME < TIMESTAMP("2026-07-01")' in sql
    assert "DATE BETWEEN 20260601000000 AND 20260630235959" in sql


def test_seeded_query_ranks_deterministically() -> None:
    sql = _build()
    assert "GROUP BY theme" in sql
    assert "ORDER BY record_count DESC, theme" in sql
    assert "ORDER BY record_count DESC, theme_code" in sql


def test_seeded_query_extracts_day_and_seeds() -> None:
    sql = _build()
    assert "DIV(DATE, 1000000)" in sql
    # Seed literals are lowercased for the case-insensitive blob match.
    assert "'nvidia'" in sql and "'tsmc'" in sql and "'semiconductor'" in sql
    assert "matched_seed_terms" in sql
    assert "distinct_day_count" in sql


def test_seeded_query_defaults_to_v2organizations() -> None:
    assert "V2Organizations" in _build()


# ── seeded-v2 defect fixes ──────────────────────────────────────────────────


def test_v2_counts_unique_records_not_theme_occurrences() -> None:
    sql = _build()
    # Unique-record counting + per-record theme dedup (was COUNT(*) over unnest).
    assert "COUNT(DISTINCT rec_id) AS record_count" in sql
    assert "SELECT DISTINCT th FROM UNNEST(" in sql
    assert "GKGRECORDID" in sql
    assert "COUNT(*) AS record_count" not in sql


def test_v2_sample_sources_are_most_frequent_not_alphabetical() -> None:
    sql = _build()
    assert "APPROX_TOP_COUNT(source" in sql
    # The old alphabetical sample must be gone.
    assert "ARRAY_AGG(DISTINCT source IGNORE NULLS ORDER BY source" not in sql


def test_v2_intel_uses_token_boundary_not_raw_substring() -> None:
    sql = _build(seed_terms=["Intel"])
    assert r"(^|[^a-z0-9])intel([^a-z0-9]|$)" in sql
    # Intel must NOT be a raw substring predicate (which matched 'intelligence').
    assert "LIKE '%intel%'" not in sql


def test_v2_industry_phrase_still_substring() -> None:
    sql = _build(seed_terms=["semiconductor"])
    assert "LIKE '%semiconductor%'" in sql


def test_v2_emits_denominator_and_source_count() -> None:
    sql = _build()
    assert "total_seed_records" in sql
    assert "source_count" in sql


def test_v2_seed_specs_override_flat_terms() -> None:
    sql = build_gdelt_seeded_theme_discovery_query(
        table=TABLE,
        start_date="2026-06-01",
        end_date="2026-06-30",
        topic="semiconductors",
        seed_specs=[{"canonical": "asml", "kind": "company_alias", "aliases": ["asml"]}],
        partition_filter=_PARTITION,
    )
    assert r"(^|[^a-z0-9])asml([^a-z0-9]|$)" in sql


def test_seeded_query_no_select_star_and_passes_guardrails() -> None:
    sql = _build()
    assert "SELECT *" not in sql
    validate_bigquery_sql(sql, [TABLE])


def test_seeded_query_empty_seeds_rejected() -> None:
    with pytest.raises(ValueError, match="seed_terms"):
        _build(seed_terms=["", "  "])


def test_seeded_query_invalid_range_rejected() -> None:
    with pytest.raises(ValueError, match="date range"):
        _build(start_date="2026-06-30", end_date="2026-06-01")


# ── task (mocked client) ────────────────────────────────────────────────────

PARAMS = {
    "query_name": "semiconductors_seeded_theme_discovery_30d_partition",
    "topic": "semiconductors",
    "seed_terms": ["NVIDIA", "TSMC", "semiconductor"],
    "seed_search_columns": ["V2Organizations"],
    "partition_filter": _PARTITION,
    "window": {"preset": "last_30_days", "bucket": "1d"},
    "limit": 200,
    "sample_size": 3,
    "cost_controls": {
        "dry_run": True,
        "maximum_bytes_billed": 30_000_000_000,
        "execute_query": "",
        "use_cache": False,
    },
}

CFG = {"providers": {"bigquery": {"project_id": "test-project", "table": TABLE}}}


def _ctx(tmp_path: Path) -> RunContext:
    return RunContext(cfg=CFG, run_name="t", run_id="1", run_dir=tmp_path / "run")


def _params(**cost_overrides):
    p = copy.deepcopy(PARAMS)
    p["cost_controls"].update(cost_overrides)
    p["cost_controls"]["cost_policy_path"] = "configs/cost_policy.yaml"
    p["cost_controls"]["ledger_path"] = str(_params.ledger_path)
    return p


def _estimate(bytes_=5_000_000_000) -> BigQueryEstimate:
    from kinetic.ingestion.cost.estimate import (
        bytes_to_gib,
        bytes_to_tib,
        estimate_bigquery_cost_usd,
    )

    return BigQueryEstimate(
        total_bytes_processed=bytes_,
        estimated_cost_usd=estimate_bigquery_cost_usd(bytes_),
        estimated_gib=bytes_to_gib(bytes_),
        estimated_tib=bytes_to_tib(bytes_),
        maximum_bytes_billed=30_000_000_000,
    )


class _FakeClient:
    response = None
    raises = None

    def __init__(self, *args, **kwargs):
        pass

    def safe_query(self, **kwargs):
        if _FakeClient.raises is not None:
            raise _FakeClient.raises
        return _FakeClient.response


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch, tmp_path):
    _FakeClient.response = None
    _FakeClient.raises = None
    _params.ledger_path = tmp_path / "cost_ledger.jsonl"
    monkeypatch.setattr(task_mod, "SafeBigQueryClient", _FakeClient)
    yield


def _read(ctx: RunContext, name: str) -> dict:
    return json.loads((ctx.artifacts_dir / name).read_text(encoding="utf-8"))


def test_seeded_dry_run_writes_artifacts_no_candidates(tmp_path: Path) -> None:
    _FakeClient.response = SafeQueryResult(
        decision=SafeQueryDecision.DRY_RUN_ONLY,
        rows=[],
        cache_hit=False,
        estimate=_estimate(),
    )
    ctx = _ctx(tmp_path)
    bigquery_gdelt_seeded_theme_discovery_task(ctx, _params(dry_run=True))

    assert (ctx.artifacts_dir / "seeded_theme_discovery_sql.sql").is_file()
    assert (ctx.artifacts_dir / "seeded_theme_discovery_summary.json").is_file()
    assert not (ctx.artifacts_dir / "seeded_theme_candidates.jsonl").exists()

    summary = _read(ctx, "seeded_theme_discovery_summary.json")
    assert summary["matching_mode"] == "seed_record_substring"
    assert summary["seed_terms"] == ["NVIDIA", "TSMC", "semiconductor"]
    assert summary["seed_search_columns"] == ["V2Organizations"]
    assert summary["query_name"] == "semiconductors_seeded_theme_discovery_30d_partition"
    assert summary["bundle_modified"] is False


def test_seeded_execution_normalizes_candidates(tmp_path: Path) -> None:
    _FakeClient.response = SafeQueryResult(
        decision=SafeQueryDecision.ALLOW,
        rows=[
            {
                "theme_code": "ECON_SUBSIDIES",
                "record_count": 40,
                "distinct_day_count": 12,
                "matched_seed_terms": ["nvidia", "tsmc"],
                "sample_sources": ["reuters.com", "bloomberg.com"],
                "first_seen": 20260601,
                "last_seen": 20260629,
            },
            {
                "theme_code": "WB_2461_INDUSTRIAL",
                "record_count": "9",
                "distinct_day_count": "4",
                "matched_seed_terms": ["semiconductor"],
                "sample_sources": ["wsj.com"],
                "first_seen": 20260603,
                "last_seen": 20260628,
            },
        ],
        cache_hit=False,
        estimate=_estimate(),
    )
    ctx = _ctx(tmp_path)
    bigquery_gdelt_seeded_theme_discovery_task(ctx, _params(dry_run=False, execute_query="ENABLE"))

    rows = ctx.state["gdelt_seeded_theme_discovery"]
    assert rows[0]["theme_code"] == "ECON_SUBSIDIES"
    assert rows[0]["record_count"] == 40
    assert rows[1]["record_count"] == 9  # coerced from str
    assert rows[0]["matched_seed_terms"] == ["nvidia", "tsmc"]

    csv_text = (ctx.artifacts_dir / "seeded_theme_candidates.csv").read_text(encoding="utf-8")
    assert "theme_code,record_count,distinct_day_count,matched_seed_terms" in csv_text
    assert "nvidia|tsmc" in csv_text  # list flattened with '|'

    summary = _read(ctx, "seeded_theme_discovery_summary.json")
    assert summary["returned_theme_count"] == 2
    assert summary["bundle_modified"] is False


def test_seeded_empty_execution_warns(tmp_path: Path) -> None:
    _FakeClient.response = SafeQueryResult(
        decision=SafeQueryDecision.ALLOW,
        rows=[],
        cache_hit=False,
        estimate=_estimate(),
    )
    ctx = _ctx(tmp_path)
    bigquery_gdelt_seeded_theme_discovery_task(ctx, _params(dry_run=False, execute_query="ENABLE"))
    summary = _read(ctx, "seeded_theme_discovery_summary.json")
    assert summary["returned_theme_count"] == 0
    assert any("No seeded theme candidates" in w for w in summary["warnings"])


def test_seeded_missing_enable_blocks(tmp_path: Path) -> None:
    err = CostGuardrailError("needs confirmation")
    err.decision = "BLOCK_MISSING_EXECUTE_CONFIRMATION"
    err.estimate = _estimate()
    _FakeClient.raises = err
    ctx = _ctx(tmp_path)
    with pytest.raises(CostGuardrailError):
        bigquery_gdelt_seeded_theme_discovery_task(ctx, _params(dry_run=False, execute_query=""))
    decision = _read(ctx, "bigquery_cost_decision.json")
    assert decision["decision"] == "BLOCK_MISSING_EXECUTE_CONFIRMATION"


def test_seeded_missing_seeds_raises(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    p = _params()
    p["seed_terms"] = []
    with pytest.raises(Exception, match="seed_terms"):
        bigquery_gdelt_seeded_theme_discovery_task(ctx, p)


def test_seeded_task_is_registered() -> None:
    from kinetic.bootstrap import build_default_registry

    registry = build_default_registry()
    assert "news.gdelt.bigquery.discover_seeded_themes" in registry.task_ids()

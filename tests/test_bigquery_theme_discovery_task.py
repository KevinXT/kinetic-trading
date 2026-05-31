"""Tests for the bigquery_gdelt_theme_discovery pipeline task (mocked client)."""

import copy
import json
from pathlib import Path

import pytest
from common.errors import CostGuardrailError
from news_data.bigquery.client import BigQueryEstimate, SafeQueryDecision, SafeQueryResult
from news_data.task import bigquery_gdelt_theme_discovery as task_mod
from news_data.task.bigquery_gdelt_theme_discovery import bigquery_gdelt_theme_discovery_task
from pipeline_core.engine.context import RunContext

PARAMS = {
    "query_name": "debug_gdelt_themes_inflation_30d_partition",
    "topic": "inflation_rates",
    "theme_search_patterns": ["inflation", "econ", "interest"],
    "partition_filter": {"enabled": True, "column": "_PARTITIONTIME", "type": "timestamp"},
    "window": {"preset": "last_30_days", "bucket": "1d"},
    "limit": 100,
    "cost_controls": {
        "dry_run": True,
        "maximum_bytes_billed": 12_000_000_000,
        "execute_query": "",
        "use_cache": True,
    },
}

CFG = {
    "providers": {
        "bigquery": {
            "project_id": "test-project",
            "table": "gdelt-bq.gdeltv2.gkg_partitioned",
        }
    }
}


def _ctx(tmp_path: Path) -> RunContext:
    return RunContext(cfg=CFG, run_name="t", run_id="1", run_dir=tmp_path / "run")


def _params(**cost_overrides):
    p = copy.deepcopy(PARAMS)
    p["cost_controls"].update(cost_overrides)
    p["cost_controls"]["cost_policy_path"] = "configs/cost_policy.yaml"
    p["cost_controls"]["ledger_path"] = str(_params.ledger_path)
    return p


def _estimate(bytes_=842_000_000) -> BigQueryEstimate:
    from common.cost.estimate import bytes_to_gib, bytes_to_tib, estimate_bigquery_cost_usd

    return BigQueryEstimate(
        total_bytes_processed=bytes_,
        estimated_cost_usd=estimate_bigquery_cost_usd(bytes_),
        estimated_gib=bytes_to_gib(bytes_),
        estimated_tib=bytes_to_tib(bytes_),
        maximum_bytes_billed=12_000_000_000,
    )


class _FakeClient:
    instances = []
    response = None
    raises = None

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        _FakeClient.instances.append(self)

    def safe_query(self, **kwargs):
        self.last_call = kwargs
        if _FakeClient.raises is not None:
            raise _FakeClient.raises
        return _FakeClient.response


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch, tmp_path):
    _FakeClient.instances = []
    _FakeClient.response = None
    _FakeClient.raises = None
    _params.ledger_path = tmp_path / "cost_ledger.jsonl"
    monkeypatch.setattr(task_mod, "SafeBigQueryClient", _FakeClient)
    yield


def _read(ctx: RunContext, name: str) -> dict:
    return json.loads((ctx.artifacts_dir / name).read_text(encoding="utf-8"))


def test_dry_run_writes_artifacts_no_theme_rows(tmp_path: Path) -> None:
    _FakeClient.response = SafeQueryResult(
        decision=SafeQueryDecision.DRY_RUN_ONLY,
        rows=[],
        cache_hit=False,
        estimate=_estimate(),
        message="Dry-run only.",
    )
    ctx = _ctx(tmp_path)
    bigquery_gdelt_theme_discovery_task(ctx, _params(dry_run=True))

    assert (ctx.artifacts_dir / "bigquery_sql.sql").is_file()
    assert (ctx.artifacts_dir / "bigquery_dry_run_estimate.json").is_file()
    assert (ctx.artifacts_dir / "bigquery_cost_decision.json").is_file()
    assert (ctx.artifacts_dir / "bigquery_summary.json").is_file()

    assert "gdelt_theme_discovery" not in ctx.state
    assert not (ctx.artifacts_dir / "theme_discovery.jsonl").exists()

    summary = _read(ctx, "bigquery_summary.json")
    assert summary["row_count"] == 0
    assert summary["theme_search_patterns"] == ["inflation", "econ", "interest"]
    assert summary["topic"] == "inflation_rates"


def test_sql_artifact_is_discovery_shaped(tmp_path: Path) -> None:
    _FakeClient.response = SafeQueryResult(
        decision=SafeQueryDecision.DRY_RUN_ONLY,
        rows=[],
        cache_hit=False,
        estimate=_estimate(),
    )
    ctx = _ctx(tmp_path)
    bigquery_gdelt_theme_discovery_task(ctx, _params(dry_run=True))
    sql = (ctx.artifacts_dir / "bigquery_sql.sql").read_text(encoding="utf-8")
    assert "DATE BETWEEN" in sql
    assert "SELECT *" not in sql
    assert "UNNEST(themes) AS theme" in sql
    assert "REGEXP_REPLACE(V2Themes, r',\\d+;', ';')" in sql
    assert "LIMIT 100" in sql


def test_execution_writes_theme_artifacts(tmp_path: Path) -> None:
    _FakeClient.response = SafeQueryResult(
        decision=SafeQueryDecision.ALLOW,
        rows=[
            {"theme": "ECON_INFLATION", "cnt": 42},
            {"theme": "WB_135_ECON", "cnt": "7"},
        ],
        cache_hit=False,
        estimate=_estimate(),
    )
    ctx = _ctx(tmp_path)
    bigquery_gdelt_theme_discovery_task(ctx, _params(dry_run=False, execute_query="ENABLE"))

    rows = ctx.state["gdelt_theme_discovery"]
    assert rows[0] == {"theme": "ECON_INFLATION", "count": 42}
    assert rows[1] == {"theme": "WB_135_ECON", "count": 7}

    assert (ctx.artifacts_dir / "theme_discovery.jsonl").is_file()
    assert (ctx.artifacts_dir / "theme_discovery.csv").is_file()
    csv_text = (ctx.artifacts_dir / "theme_discovery.csv").read_text(encoding="utf-8")
    assert "theme,count" in csv_text

    summary = _read(ctx, "bigquery_summary.json")
    assert summary["row_count"] == 2
    assert summary["decision"] == "ALLOW"


def test_cache_hit_sets_theme_rows(tmp_path: Path) -> None:
    _FakeClient.response = SafeQueryResult(
        decision=SafeQueryDecision.USE_CACHE,
        rows=[{"theme": "ECON_INFLATION", "cnt": 5}],
        cache_hit=True,
        estimate=None,
    )
    ctx = _ctx(tmp_path)
    bigquery_gdelt_theme_discovery_task(ctx, _params(dry_run=True))
    rows = ctx.state["gdelt_theme_discovery"]
    assert rows[0]["count"] == 5
    summary = _read(ctx, "bigquery_summary.json")
    assert summary["cache_hit"] is True


def test_missing_enable_blocks_and_writes_artifacts(tmp_path: Path) -> None:
    err = CostGuardrailError("needs confirmation")
    err.decision = "BLOCK_MISSING_EXECUTE_CONFIRMATION"
    err.estimate = _estimate()
    err.ledger_record = None
    _FakeClient.raises = err

    ctx = _ctx(tmp_path)
    with pytest.raises(CostGuardrailError):
        bigquery_gdelt_theme_discovery_task(ctx, _params(dry_run=False, execute_query=""))

    decision = _read(ctx, "bigquery_cost_decision.json")
    assert decision["decision"] == "BLOCK_MISSING_EXECUTE_CONFIRMATION"
    summary = _read(ctx, "bigquery_summary.json")
    assert summary["theme_search_patterns"] == ["inflation", "econ", "interest"]


def test_missing_topic_raises(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    p = _params()
    p.pop("topic")
    with pytest.raises(Exception, match="topic"):
        bigquery_gdelt_theme_discovery_task(ctx, p)


def test_non_positive_limit_raises(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    p = _params()
    p["limit"] = 0
    with pytest.raises(Exception, match="limit"):
        bigquery_gdelt_theme_discovery_task(ctx, p)


def test_task_is_registered() -> None:
    import trading_platform  # noqa: F401  (triggers registration)
    from pipeline_core.tasks.registry import TASK_REGISTRY

    assert "bigquery_gdelt_theme_discovery" in TASK_REGISTRY


# ── cache-key regression: stale theme_discovery results are never reused ──────

from news_data.bigquery.cache import (  # noqa: E402
    bigquery_cache_path,
    build_cache_key,
    load_cached_rows,
    write_cached_rows,
)
from news_data.bigquery.gdelt_queries import (  # noqa: E402
    QUERY_BUILDER_VERSION,
    build_gdelt_theme_discovery_query,
)

_TABLE = "gdelt-bq.gdeltv2.gkg_partitioned"
_PF = {"enabled": True, "column": "_PARTITIONTIME", "type": "timestamp"}


def _discovery_key(*, patterns, builder_version=QUERY_BUILDER_VERSION, limit=100):
    """Replicate exactly how bigquery_gdelt_theme_discovery_task builds its key."""
    sql = build_gdelt_theme_discovery_query(
        table=_TABLE,
        start_date="20260501",
        end_date="20260530",
        topic="inflation_rates",
        search_patterns=patterns,
        theme_column="V2Themes",
        partition_filter=_PF,
        limit=limit,
    )
    return build_cache_key(
        provider="bigquery_gdelt_theme_discovery",
        table=_TABLE,
        topic="inflation_rates",
        query_terms=patterns,
        start_date="20260501",
        end_date="20260530",
        builder_version=f"{builder_version}-theme-discovery-limit{limit}",
        search_columns=["V2Themes"],
        partition_filter=_PF,
        query_name="debug_gdelt_themes_inflation_30d_partition",
        sql=sql,
    )


def test_changing_patterns_changes_cache_key() -> None:
    a = _discovery_key(patterns=["inflation", "econ"])
    b = _discovery_key(patterns=["fiscal", "tariff"])
    assert a != b
    assert bigquery_cache_path(a) != bigquery_cache_path(b)


def test_changing_builder_version_changes_cache_key() -> None:
    a = _discovery_key(patterns=["inflation"], builder_version="v4")
    b = _discovery_key(patterns=["inflation"], builder_version="v3")
    assert a != b


def test_changing_limit_changes_cache_key() -> None:
    assert _discovery_key(patterns=["inflation"], limit=100) != _discovery_key(
        patterns=["inflation"], limit=50
    )


def test_old_cache_not_reused_for_new_patterns(tmp_path: Path) -> None:
    # A stale (even 0-row) result cached under one pattern set must not be served
    # to a run with a different pattern set — different key -> different path.
    key_a = _discovery_key(
        patterns=["inflation", "econ", "interest", "cost", "central_bank", "prices"]
    )
    key_b = _discovery_key(patterns=["fiscal"])

    write_cached_rows(
        bigquery_cache_path(key_a, base_dir=tmp_path),
        [{"theme": "ECON_INFLATION", "cnt": 1}],
    )
    # A run with different patterns looks up key_b and finds nothing (miss).
    assert load_cached_rows(bigquery_cache_path(key_b, base_dir=tmp_path)) is None

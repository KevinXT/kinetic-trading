"""Tests for the bigquery_gdelt_counts pipeline task (mocked client)."""

import json
from pathlib import Path

import pytest
from common.errors import CostGuardrailError
from news_data.bigquery.client import BigQueryEstimate, SafeQueryDecision, SafeQueryResult
from news_data.task import bigquery_gdelt_counts as task_mod
from news_data.task.bigquery_gdelt_counts import bigquery_gdelt_counts_task
from pipeline_core.engine.context import RunContext

PARAMS = {
    "query_name": "inflation_rates_bigquery_30d",
    "topic": "inflation_rates",
    "query_terms": ["Federal Reserve", "inflation", "interest rates"],
    "window": {"preset": "last_30_days", "bucket": "1d"},
    "cost_controls": {
        "dry_run": True,
        "maximum_bytes_billed": 1_000_000_000,
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
    import copy

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
        maximum_bytes_billed=1_000_000_000,
    )


class _FakeClient:
    """Stand-in for SafeBigQueryClient injected via monkeypatch."""

    instances = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        _FakeClient.instances.append(self)

    # response is configured per-test via class attribute
    response = None
    raises = None

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


# ── dry-run ────────────────────────────────────────────────────────────────


def test_dry_run_writes_artifacts_no_feature_rows(tmp_path: Path) -> None:
    _FakeClient.response = SafeQueryResult(
        decision=SafeQueryDecision.DRY_RUN_ONLY,
        rows=[],
        cache_hit=False,
        estimate=_estimate(),
        message="Dry-run only.",
    )
    ctx = _ctx(tmp_path)
    bigquery_gdelt_counts_task(ctx, _params(dry_run=True))

    assert (ctx.artifacts_dir / "bigquery_sql.sql").is_file()
    assert (ctx.artifacts_dir / "bigquery_dry_run_estimate.json").is_file()
    assert (ctx.artifacts_dir / "bigquery_cost_decision.json").is_file()
    assert (ctx.artifacts_dir / "bigquery_summary.json").is_file()

    assert "topic_daily_features" not in ctx.state
    assert not (ctx.artifacts_dir / "bigquery_daily_counts.jsonl").exists()

    decision = _read(ctx, "bigquery_cost_decision.json")
    assert decision["decision"] == "DRY_RUN_ONLY"
    summary = _read(ctx, "bigquery_summary.json")
    assert summary["row_count"] == 0
    assert "dry_run=true" in summary["note"]


def test_sql_artifact_is_valid(tmp_path: Path) -> None:
    _FakeClient.response = SafeQueryResult(
        decision=SafeQueryDecision.DRY_RUN_ONLY,
        rows=[],
        cache_hit=False,
        estimate=_estimate(),
    )
    ctx = _ctx(tmp_path)
    bigquery_gdelt_counts_task(ctx, _params(dry_run=True))
    sql = (ctx.artifacts_dir / "bigquery_sql.sql").read_text(encoding="utf-8")
    assert "DATE BETWEEN" in sql
    assert "SELECT *" not in sql


# ── blocked execution ────────────────────────────────────────────────────


def test_missing_enable_blocks_and_writes_artifacts(tmp_path: Path) -> None:
    err = CostGuardrailError("needs confirmation")
    err.decision = "BLOCK_MISSING_EXECUTE_CONFIRMATION"
    err.estimate = _estimate()
    err.ledger_record = None
    _FakeClient.raises = err

    ctx = _ctx(tmp_path)
    with pytest.raises(CostGuardrailError):
        bigquery_gdelt_counts_task(ctx, _params(dry_run=False, execute_query=""))

    assert (ctx.artifacts_dir / "bigquery_sql.sql").is_file()
    decision = _read(ctx, "bigquery_cost_decision.json")
    assert decision["decision"] == "BLOCK_MISSING_EXECUTE_CONFIRMATION"
    assert "topic_daily_features" not in ctx.state


# ── allowed execution ─────────────────────────────────────────────────────


def test_execution_allowed_sets_feature_rows(tmp_path: Path) -> None:
    _FakeClient.response = SafeQueryResult(
        decision=SafeQueryDecision.ALLOW,
        rows=[
            {"date": 20260501, "article_count": 100},
            {"date": "2026-05-02", "article_count": "120"},
        ],
        cache_hit=False,
        estimate=_estimate(),
    )
    ctx = _ctx(tmp_path)
    bigquery_gdelt_counts_task(ctx, _params(dry_run=False, execute_query="ENABLE"))

    rows = ctx.state["topic_daily_features"]
    assert len(rows) == 2
    assert rows[0]["date"] == "2026-05-01"
    assert rows[0]["topic"] == "inflation_rates"
    assert rows[1]["article_count"] == 120

    assert (ctx.artifacts_dir / "bigquery_daily_counts.jsonl").is_file()
    assert (ctx.artifacts_dir / "bigquery_daily_counts.csv").is_file()
    summary = _read(ctx, "bigquery_summary.json")
    assert summary["row_count"] == 2
    assert summary["decision"] == "ALLOW"


# ── cache hit ──────────────────────────────────────────────────────────────


def test_cache_hit_sets_feature_rows(tmp_path: Path) -> None:
    _FakeClient.response = SafeQueryResult(
        decision=SafeQueryDecision.USE_CACHE,
        rows=[{"date": "2026-05-01", "article_count": 7}],
        cache_hit=True,
        estimate=None,
    )
    ctx = _ctx(tmp_path)
    bigquery_gdelt_counts_task(ctx, _params(dry_run=True))

    rows = ctx.state["topic_daily_features"]
    assert rows[0]["article_count"] == 7
    summary = _read(ctx, "bigquery_summary.json")
    assert summary["cache_hit"] is True
    assert summary["row_count"] == 1


# ── validation ─────────────────────────────────────────────────────────────


def test_missing_topic_raises(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    p = _params()
    p.pop("topic")
    with pytest.raises(Exception, match="topic"):
        bigquery_gdelt_counts_task(ctx, p)


def test_empty_terms_raises(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    p = _params()
    p["query_terms"] = []
    with pytest.raises(Exception, match="query_terms"):
        bigquery_gdelt_counts_task(ctx, p)


def test_non_daily_bucket_raises(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    p = _params()
    p["window"] = {"preset": "last_30_days", "bucket": "1h"}
    with pytest.raises(Exception, match="bucket"):
        bigquery_gdelt_counts_task(ctx, p)


# ── registration smoke test ────────────────────────────────────────────────


def test_task_is_registered() -> None:
    import trading_platform  # noqa: F401  (triggers registration)
    from pipeline_core.tasks.registry import TASK_REGISTRY

    assert "bigquery_gdelt_counts" in TASK_REGISTRY

"""Tests for the single-scan seeded-vs-background theme-scoring builder + task."""

import copy
import json
from pathlib import Path

import pytest

from kinetic.core.errors import CostGuardrailError
from kinetic.core.pipeline.context import RunContext
from kinetic.ingestion.news.gdelt.bigquery.queries import build_gdelt_seeded_theme_scoring_query
from kinetic.ingestion.news.gdelt.bigquery.tasks import seeded_theme_scoring as task_mod
from kinetic.ingestion.news.gdelt.bigquery.tasks.seeded_theme_scoring import (
    bigquery_gdelt_seeded_theme_scoring_task,
)
from kinetic.ingestion.news.gdelt.bigquery.theme_bundles import load_theme_bundles
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
        seed_terms=["semiconductor", "Intel", "NVIDIA"],
        partition_filter=_PARTITION,
    )
    params.update(kw)
    return build_gdelt_seeded_theme_scoring_query(**params)


# ── SQL builder ──────────────────────────────────────────────────────────────


def test_scoring_query_computes_seed_and_background_in_one_scan() -> None:
    sql = _build()
    assert "COUNT(DISTINCT IF(is_seed, rec_id, NULL)) AS seed_record_count" in sql
    assert "COUNT(DISTINCT rec_id) AS background_record_count" in sql
    # scoring-v3 derives denominators from a synthetic __TOTAL__ theme carried by
    # every record, NOT scalar sub-queries over `base` (which risked 2-3x bytes).
    assert "(SELECT COUNT(DISTINCT rec_id) FROM base)" not in sql
    assert "'__TOTAL__'" in sql
    assert "'__SEED__'" in sql


def test_scoring_query_emits_weekly_buckets_and_concentration() -> None:
    sql = _build()
    # 30-day window -> 5 weekly buckets (wk_0..wk_4).
    for w in range(5):
        assert f"AS wk_{w}" in sql
    assert "AS top_sources" in sql
    assert "AS top_days" in sql
    assert "AS periods_present" in sql
    assert "AS seed_hits" in sql
    assert "AS sample_record_ids" in sql
    # BigQuery requires APPROX_TOP_COUNT's size arg to be a bare literal, not an
    # expression like "5 + 1" (regression: scoring-v4). It also must exceed the
    # sample size so a real value survives after the NULL bucket is dropped.
    import re

    sizes = [int(m) for m in re.findall(r",\s*(\d+)\) AS top_(?:sources|days)", sql)]
    assert len(sizes) == 2 and all(s >= 2 for s in sizes)
    assert "+ 1)" not in sql


def test_scoring_query_applies_min_support_floor_but_exempts_sentinels() -> None:
    assert "HAVING seed_record_count >= 25 OR STARTS_WITH(theme, '__')" in _build(min_support=25)
    assert "HAVING seed_record_count >= 100 OR STARTS_WITH(theme, '__')" in _build(min_support=100)


def test_scoring_query_ranks_by_association_not_frequency() -> None:
    sql = _build(limit=500)
    # Sentinels first, then a lift-proportional association proxy (seed/background),
    # then support and theme code for a deterministic total order.
    assert "STARTS_WITH(p.theme, '__') DESC" in sql
    assert "SAFE_DIVIDE(p.seed_record_count, p.background_record_count) DESC" in sql
    # The HAVING support floor is applied before the LIMIT.
    assert sql.index("HAVING seed_record_count >= 25") < sql.index("LIMIT 5004")
    # The SQL LIMIT is only a safety cap: the FULL support-qualified family is
    # returned (candidate_limit default 5000 + 1 __TOTAL__ + 3 __SEED__ rows).
    assert "LIMIT 5004" in sql


def test_scoring_query_full_family_cap_independent_of_presentation_limit() -> None:
    # The presentation `limit` does NOT shrink the returned family; only
    # `candidate_limit` caps the returned rows (as a safety valve).
    assert "LIMIT 5004" in _build(limit=10)
    assert "LIMIT 5004" in _build(limit=500)
    assert "LIMIT 204" in _build(limit=500, candidate_limit=200)


def test_scoring_query_emits_exact_concentration_and_sample_records() -> None:
    sql = _build()
    assert "AS exact_top_source_count" in sql
    assert "AS exact_top_day_count" in sql
    assert "AS sample_records" in sql
    assert "FARM_FINGERPRINT(rec_id)" in sql
    # Corpus-level seed-composition denominators.
    assert "AS seed_match_count" in sql
    assert "AS multi_seed_record_count" in sql


def test_scoring_query_emits_document_identifier_for_auditable_evidence() -> None:
    sql = _build()
    # DocumentIdentifier (source URL) is carried into the sample_records struct so
    # representative evidence can be independently inspected.
    assert "DocumentIdentifier AS doc_id" in sql
    assert "doc_id" in sql
    assert "STRUCT(rec_id, source, day, doc_id, matched_seeds)" in sql


def test_scoring_query_emits_independent_qualified_family_count() -> None:
    sql = _build()
    # An independent qualified-family count (unaffected by the outer safety cap
    # LIMIT) so BH can refuse to run on a truncated family.
    assert "qualified_count AS (" in sql
    assert "COUNTIF(NOT STARTS_WITH(theme, '__')) AS n FROM per_theme" in sql
    assert "qc.n AS total_support_qualified_candidates" in sql
    assert "CROSS JOIN qualified_count qc" in sql
    validate_bigquery_sql(sql, [TABLE])


def test_scoring_query_partition_pruned_and_guardrail_safe() -> None:
    sql = _build()
    assert '_PARTITIONTIME >= TIMESTAMP("2026-06-01")' in sql
    assert "SELECT *" not in sql
    validate_bigquery_sql(sql, [TABLE])


def test_scoring_query_boundary_matches_intel() -> None:
    sql = _build()
    assert r"(^|[^a-z0-9])intel([^a-z0-9]|$)" in sql
    assert "LIKE '%intel%'" not in sql


def test_scoring_query_rejects_negative_min_support() -> None:
    with pytest.raises(ValueError):
        _build(min_support=-1)


# ── task (mocked client) ─────────────────────────────────────────────────────

PARAMS = {
    "query_name": "semiconductors_seeded_theme_scoring_30d_partition",
    "topic": "semiconductors",
    "seed_terms": ["semiconductor", "NVIDIA", "Intel"],
    "seed_search_columns": ["V2Organizations"],
    "partition_filter": _PARTITION,
    "window": {"preset": "last_30_days", "bucket": "1d"},
    "limit": 500,
    "sample_size": 5,
    "min_support": 25,
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


def _estimate(bytes_=9_000_000_000) -> BigQueryEstimate:
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


# Realistic result set: a __TOTAL__ denominator row, two __SEED__ diagnostic
# rows, one enriched theme and one ubiquitous generic theme.
def _weekly(n):
    return {f"wk_{w}": n // 5 for w in range(5)}


_EXEC_ROWS = [
    {
        "theme_code": "__TOTAL__",
        # Independent support-qualified family size (SQL qualified_count CTE),
        # broadcast onto every row by the CROSS JOIN. Two real candidates here.
        "total_support_qualified_candidates": 2,
        "seed_record_count": 5000,
        "background_record_count": 500000,
        "distinct_day_count": 30,
        "periods_present": 5,
        "source_count": 900,
        "seed_match_count": 6200,  # > total_seed (5000) because records match >1 seed
        "multi_seed_record_count": 1000,
        "top_sources": [{"value": "reuters.com", "n": 60}],
        "top_days": [{"value": "20260601", "n": 200}],
        "seed_hits": [{"seed": "intel", "n": 2000}, {"seed": "nvidia", "n": 1800}],
        "matched_seed_terms": ["intel", "nvidia", "semiconductor"],
        "sample_record_ids": ["a"],
        "sample_records": [],
        "exact_top_source_count": 60,
        "exact_top_day_count": 200,
        **_weekly(5000),
    },
    {"theme_code": "__SEED__intel", "seed_record_count": 2000, "background_record_count": 2000},
    {"theme_code": "__SEED__nvidia", "seed_record_count": 1800, "background_record_count": 1800},
    {
        "theme_code": "WB_2461_INDUSTRIAL",
        "seed_record_count": 400,
        "background_record_count": 800,
        "distinct_day_count": 28,
        "periods_present": 5,
        "source_count": 120,
        "top_sources": [{"value": "reuters.com", "n": 40}, {"value": "bloomberg.com", "n": 30}],
        "top_days": [{"value": "20260615", "n": 30}],
        "exact_top_source_count": 40,
        "exact_top_day_count": 30,
        "seed_hits": [
            {"seed": "intel", "n": 200},
            {"seed": "nvidia", "n": 180},
            {"seed": "semiconductor", "n": 120},
        ],
        "matched_seed_terms": ["nvidia", "intel", "semiconductor"],
        "sample_record_ids": ["20260601-1", "20260602-2"],
        "sample_records": [
            {
                "rec_id": "20260615000000-1",
                "source": "reuters.com",
                "day": 20260615,
                "doc_id": "https://reuters.com/tech/intel-nvidia-chip-2026",
                "matched_seeds": ["intel", "nvidia"],
            },
            {
                "rec_id": "20260602000000-2",
                "source": "bloomberg.com",
                "day": 20260602,
                "doc_id": "https://bloomberg.com/news/semis-2026",
                "matched_seeds": ["semiconductor"],
            },
        ],
        **_weekly(400),
    },
    {
        "theme_code": "TAX_ECON_PRICE",
        "seed_record_count": 3000,
        "background_record_count": 300000,
        "distinct_day_count": 30,
        "periods_present": 5,
        "source_count": 900,
        "top_sources": [{"value": "cnn.com", "n": 100}],
        "top_days": [{"value": "20260620", "n": 150}],
        "exact_top_source_count": 100,
        "exact_top_day_count": 150,
        "seed_hits": [{"seed": "intel", "n": 1500}, {"seed": "nvidia", "n": 1400}],
        "matched_seed_terms": ["nvidia", "intel"],
        "sample_record_ids": ["z1"],
        "sample_records": [
            {
                "rec_id": "20260620000000-9",
                "source": "cnn.com",
                "day": 20260620,
                "matched_seeds": ["intel"],
            }
        ],
        **_weekly(3000),
    },
]


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
    monkeypatch.setattr(task_mod.task, "SafeBigQueryClient", _FakeClient)
    yield


def _read(ctx: RunContext, name: str) -> dict:
    return json.loads((ctx.artifacts_dir / name).read_text(encoding="utf-8"))


def test_scoring_dry_run_writes_sql_and_summary(tmp_path: Path) -> None:
    _FakeClient.response = SafeQueryResult(
        decision=SafeQueryDecision.DRY_RUN_ONLY, rows=[], cache_hit=False, estimate=_estimate()
    )
    ctx = _ctx(tmp_path)
    bigquery_gdelt_seeded_theme_scoring_task(ctx, _params(dry_run=True))
    assert (ctx.artifacts_dir / "candidate_scoring_sql.sql").is_file()
    summary = _read(ctx, "theme_candidate_scoring_summary.json")
    assert summary["bundle_modified"] is False
    assert summary["matching_mode"] == "seed_record_boundary_v6"
    assert summary["organization_seeded_only"] is True


def test_scoring_execution_computes_lift_ranks_and_writes_artifacts(tmp_path: Path) -> None:
    _FakeClient.response = SafeQueryResult(
        decision=SafeQueryDecision.ALLOW,
        rows=copy.deepcopy(_EXEC_ROWS),
        cache_hit=False,
        estimate=_estimate(),
    )
    ctx = _ctx(tmp_path)
    bigquery_gdelt_seeded_theme_scoring_task(ctx, _params(dry_run=False, execute_query="ENABLE"))

    scores = ctx.state["gdelt_seeded_theme_scores"]
    industrial = next(r for r in scores if r["theme"] == "WB_2461_INDUSTRIAL")
    generic = next(r for r in scores if r["theme"] == "TAX_ECON_PRICE")

    # Sentinels are consumed for denominators, not emitted as candidates.
    assert {r["theme"] for r in scores} == {"WB_2461_INDUSTRIAL", "TAX_ECON_PRICE"}
    assert industrial["total_seed_records"] == 5000
    assert industrial["total_background_records"] == 500000

    # Enriched theme: prevalence 0.08 seed vs 0.0016 background -> lift ~50.
    assert industrial["lift"] is not None and industrial["lift"] > 10
    assert industrial["generic_noise_flag"] is False
    assert industrial["composite_score"] is not None
    assert industrial["confidence_interval_low"] is not None
    assert industrial["p_value"] is not None and industrial["adjusted_p_value"] is not None

    # Statistical ranking departs from raw frequency: the generic theme has the
    # largest raw count (frequency_rank 1) but a worse statistical rank.
    assert generic["frequency_rank"] == 1
    assert industrial["raw_statistical_rank"] == 1
    assert industrial["research_adjusted_rank"] == 1
    assert generic["generic_noise_flag"] is True
    assert generic["composite_score"] < industrial["composite_score"]
    assert industrial["screening_pass"] is True
    assert generic["screening_pass"] is False

    # All Phase-10 artifacts are produced.
    for name in (
        "theme_candidate_scores.csv",
        "theme_candidate_scores.jsonl",
        "theme_candidate_review.csv",
        "seed_match_diagnostics.csv",
        "seed_match_diagnostics.jsonl",
        "candidate_stability.csv",
        "candidate_source_concentration.csv",
        "candidate_representative_evidence.csv",
        "candidate_representative_evidence.jsonl",
        "research_quality_report.md",
    ):
        assert (ctx.artifacts_dir / name).is_file(), name

    # Phase 2 terminology: the report must describe multi-theme records as theme
    # co-occurrence and keep syndication probabilistic — never claim proven
    # article/record duplication, and never claim article-level uniqueness.
    report = (ctx.artifacts_dir / "research_quality_report.md").read_text(encoding="utf-8")
    low = report.lower()
    assert "theme co-occurrence" in low
    assert "probable syndication" in low
    assert "record-vs-article duplication" not in low
    assert "record vs article duplication" not in low
    for forbidden in (
        "syndication was proven",
        "duplication was proven",
        "article duplication was proven",
        "unique article",
        "distinct articles are",
    ):
        assert forbidden not in low, forbidden
    assert "cannot be measured exactly" in low

    diag = (ctx.artifacts_dir / "seed_match_diagnostics.csv").read_text(encoding="utf-8")
    assert "intel" in diag and "2000" in diag

    summary = _read(ctx, "theme_candidate_scoring_summary.json")
    assert summary["candidates_with_lift"] == 2
    assert summary["total_seed_records"] == 5000
    assert summary["bundle_modified"] is False
    # Candidate-family provenance (Phase 2): 2 support-qualified candidates, full
    # family returned (not truncated), BH over the complete family.
    assert summary["total_support_qualified_candidates"] == 2
    assert summary["result_truncated"] is False
    assert summary["multiple_testing_family_size"] == 2
    assert summary["multiple_testing_applied_before_limit"] is True
    # Full classification distribution includes every category (explicit zeros).
    from kinetic.processing.news.themes.classification import CATEGORIES

    assert set(summary["classification_distribution"]) == set(CATEGORIES)
    # Seed-composition denominators (Phase 5).
    assert summary["total_record_seed_matches"] == 6200
    assert summary["multi_seed_record_count"] == 1000
    assert summary["total_nonseed_background_records"] == 495000


def test_scoring_skips_approx_top_count_null_bucket(tmp_path: Path) -> None:
    # APPROX_TOP_COUNT counts NULL (non-seed rows) as its own bucket, which can
    # rank first. The scorer must skip it so concentration reflects the top real
    # source/day, not the non-seed count.
    rows = copy.deepcopy(_EXEC_ROWS)
    industrial = next(r for r in rows if r["theme_code"] == "WB_2461_INDUSTRIAL")
    industrial["top_sources"] = [{"value": None, "n": 999999}] + industrial["top_sources"]
    industrial["top_days"] = [{"value": None, "n": 999999}] + industrial["top_days"]
    _FakeClient.response = SafeQueryResult(
        decision=SafeQueryDecision.ALLOW, rows=rows, cache_hit=False, estimate=_estimate()
    )
    ctx = _ctx(tmp_path)
    bigquery_gdelt_seeded_theme_scoring_task(ctx, _params(dry_run=False, execute_query="ENABLE"))
    scored = next(
        r for r in ctx.state["gdelt_seeded_theme_scores"] if r["theme"] == "WB_2461_INDUSTRIAL"
    )
    # seed_record_count=400, top real source reuters=40 -> 0.1, top day=30 -> 0.075.
    # The APPROX share must skip the NULL bucket (n=999999) and use the real value.
    assert scored["approx_top_source_share"] == pytest.approx(0.1, abs=1e-9)
    assert scored["approx_top_day_share"] == pytest.approx(0.075, abs=1e-9)
    # Exact (from the extra SQL grain) agrees here.
    assert scored["exact_top_source_share"] == pytest.approx(0.1, abs=1e-9)
    assert scored["top_source_share"] == pytest.approx(0.1, abs=1e-9)
    assert scored["top_day_share"] == pytest.approx(0.075, abs=1e-9)
    assert "reuters.com" in scored["sample_sources"]


def test_scoring_missing_enable_blocks(tmp_path: Path) -> None:
    err = CostGuardrailError("needs confirmation")
    err.decision = "BLOCK_MISSING_EXECUTE_CONFIRMATION"
    err.estimate = _estimate()
    _FakeClient.raises = err
    ctx = _ctx(tmp_path)
    with pytest.raises(CostGuardrailError):
        bigquery_gdelt_seeded_theme_scoring_task(ctx, _params(dry_run=False, execute_query=""))
    decision = _read(ctx, "bigquery_cost_decision.json")
    assert decision["decision"] == "BLOCK_MISSING_EXECUTE_CONFIRMATION"


def test_scoring_task_registered_and_bundle_untouched() -> None:
    from kinetic.bootstrap import build_default_registry

    registry = build_default_registry()
    assert "news.gdelt.bigquery.score_seeded_themes" in registry.task_ids()
    # The production semiconductors bundle must remain empty (immutable here).
    bundles = load_theme_bundles("configs/gdelt_theme_bundles.yaml")
    assert bundles["semiconductors"] == []


# ── Phase 3: disjoint non-seed inference vs whole-corpus descriptive ──────────


def _run(tmp_path: Path, rows, **cost):
    _FakeClient.response = SafeQueryResult(
        decision=SafeQueryDecision.ALLOW,
        rows=copy.deepcopy(rows),
        cache_hit=False,
        estimate=_estimate(),
    )
    ctx = _ctx(tmp_path)
    bigquery_gdelt_seeded_theme_scoring_task(
        ctx, _params(dry_run=False, execute_query="ENABLE", **cost)
    )
    return ctx


def test_inferential_uses_disjoint_nonseed_groups(tmp_path: Path) -> None:
    ctx = _run(tmp_path, _EXEC_ROWS)
    ind = next(
        r for r in ctx.state["gdelt_seeded_theme_scores"] if r["theme"] == "WB_2461_INDUSTRIAL"
    )
    # a=400, N1=5000, all-corpus theme=800, N_corpus=500000.
    # Disjoint non-seed: c = 800-400 = 400, N0 = 500000-5000 = 495000.
    assert ind["all_corpus_theme_count"] == 800
    assert ind["all_corpus_record_count"] == 500000
    assert ind["nonseed_background_theme_count"] == 400
    assert ind["nonseed_background_record_count"] == 495000
    # Descriptive lift (vs 500k whole corpus) and inferential lift (vs 495k
    # non-seed) are genuinely DIFFERENT numbers, both reported.
    assert ind["descriptive_lift_vs_all_corpus"] == pytest.approx(50.0, rel=1e-3)
    assert ind["lift_vs_nonseed_background"] == pytest.approx(99.0, rel=1e-2)
    assert ind["descriptive_lift_vs_all_corpus"] != ind["lift_vs_nonseed_background"]
    # Backward-compatible alias tracks the inferential (corrected) lift.
    assert ind["lift"] == ind["lift_vs_nonseed_background"]


def test_inferential_honest_null_when_cells_invalid(tmp_path: Path) -> None:
    # Anomalous row: seed_record_count > whole-corpus theme count is impossible
    # in real data (background includes seeds), so the non-seed cell would be
    # negative -> honest nulls for inference, but descriptive still computes.
    rows = copy.deepcopy(_EXEC_ROWS)
    rows.append(
        {
            "theme_code": "WB_9999_ANOMALY",
            "seed_record_count": 900,
            "background_record_count": 800,  # < seed -> invalid non-seed cell
            "distinct_day_count": 10,
            "periods_present": 4,
            "source_count": 50,
            "top_sources": [{"value": "x.com", "n": 10}],
            "top_days": [{"value": "20260610", "n": 9}],
            "exact_top_source_count": 10,
            "exact_top_day_count": 9,
            "seed_hits": [{"seed": "intel", "n": 500}, {"seed": "nvidia", "n": 500}],
            "sample_record_ids": ["q1"],
            "sample_records": [],
            **_weekly(900),
        }
    )
    ctx = _run(tmp_path, rows)
    anom = next(
        r for r in ctx.state["gdelt_seeded_theme_scores"] if r["theme"] == "WB_9999_ANOMALY"
    )
    assert anom["nonseed_background_theme_count"] is None
    assert anom["lift_vs_nonseed_background"] is None
    assert anom["p_value"] is None
    assert anom["adjusted_p_value"] is None
    # Composite is null when the association metric is unavailable (Phase 6).
    assert anom["composite_score"] is None
    # Descriptive whole-corpus prevalence is still available.
    assert anom["all_corpus_theme_count"] == 800
    assert anom["descriptive_lift_vs_all_corpus"] is not None


# ── Phase 2: full family + BH + truncation ───────────────────────────────────


def test_bh_family_covers_full_returned_set_and_is_monotone(tmp_path: Path) -> None:
    ctx = _run(tmp_path, _EXEC_ROWS)
    scores = ctx.state["gdelt_seeded_theme_scores"]
    tested = [r for r in scores if r["p_value"] is not None]
    assert all(r["multiple_testing_family_size"] == len(tested) for r in tested)
    # Adjusted p-values are monotone non-decreasing in raw p rank and clipped.
    ordered = sorted(tested, key=lambda r: r["p_value"])
    adj = [r["adjusted_p_value"] for r in ordered]
    assert adj == sorted(adj)
    assert all(0.0 <= a <= 1.0 for a in adj)


def test_family_complete_below_cap_runs_bh(tmp_path: Path) -> None:
    ctx = _run(tmp_path, _EXEC_ROWS)  # independent count 2 << default cap 5000
    summary = _read(ctx, "theme_candidate_scoring_summary.json")
    assert summary["family_complete"] is True
    assert summary["statistical_completion_status"] == "complete"
    assert summary["family_cap_reached"] is False
    assert summary["result_truncated"] is False
    assert summary["bh_skipped_due_to_incomplete_family"] is False
    assert summary["multiple_testing_family_size"] == summary["valid_hypothesis_test_count"]


def test_family_exactly_at_cap_is_complete(tmp_path: Path) -> None:
    # Independent count (2) == cap (2): proven complete, BH runs.
    p = copy.deepcopy(PARAMS)
    p["candidate_limit"] = 2
    p["cost_controls"].update(dry_run=False, execute_query="ENABLE", use_cache=False)
    p["cost_controls"]["cost_policy_path"] = "configs/cost_policy.yaml"
    p["cost_controls"]["ledger_path"] = str(_params.ledger_path)
    _FakeClient.response = SafeQueryResult(
        decision=SafeQueryDecision.ALLOW,
        rows=copy.deepcopy(_EXEC_ROWS),
        cache_hit=False,
        estimate=_estimate(),
    )
    ctx = _ctx(tmp_path / "eq")
    bigquery_gdelt_seeded_theme_scoring_task(ctx, p)
    summary = _read(ctx, "theme_candidate_scoring_summary.json")
    assert summary["family_complete"] is True
    assert summary["statistical_completion_status"] == "complete"
    assert summary["bh_skipped_due_to_incomplete_family"] is False


def test_family_cap_exceeded_fails_closed(tmp_path: Path) -> None:
    # Independent count (2) > cap (1): FAIL-CLOSED — BH skipped, adjusted p null,
    # run marked statistically incomplete, verdict blocked.
    p = copy.deepcopy(PARAMS)
    p["candidate_limit"] = 1
    p["cost_controls"].update(dry_run=False, execute_query="ENABLE", use_cache=False)
    p["cost_controls"]["cost_policy_path"] = "configs/cost_policy.yaml"
    p["cost_controls"]["ledger_path"] = str(_params.ledger_path)
    _FakeClient.response = SafeQueryResult(
        decision=SafeQueryDecision.ALLOW,
        rows=copy.deepcopy(_EXEC_ROWS),
        cache_hit=False,
        estimate=_estimate(),
    )
    ctx = _ctx(tmp_path / "b")
    bigquery_gdelt_seeded_theme_scoring_task(ctx, p)
    summary = _read(ctx, "theme_candidate_scoring_summary.json")
    assert summary["family_complete"] is False
    assert summary["family_cap_reached"] is True
    assert summary["statistical_completion_status"] == "incomplete_family_cap_exceeded"
    assert summary["bh_skipped_due_to_incomplete_family"] is True
    assert summary["result_truncated"] is True
    assert summary["multiple_testing_family_size"] == 0
    assert any("STATISTICALLY INCOMPLETE" in w for w in summary["warnings"])
    # Every candidate keeps a null adjusted p-value when the family is incomplete.
    for r in ctx.state["gdelt_seeded_theme_scores"]:
        assert r["adjusted_p_value"] is None
        assert r["family_complete"] is False
    # Fail-closed still writes SQL + cost artifacts and never touches the bundle.
    assert (ctx.artifacts_dir / "candidate_scoring_sql.sql").is_file()
    assert (ctx.artifacts_dir / "bigquery_cost_decision.json").is_file()
    assert summary["bundle_modified"] is False


def test_family_count_unavailable_uses_row_overflow(tmp_path: Path) -> None:
    # Older query shape without the independent count column: fall back to
    # limit-plus-one row-overflow detection. cap=1, 2 candidate rows -> incomplete.
    rows = copy.deepcopy(_EXEC_ROWS)
    for r in rows:
        r.pop("total_support_qualified_candidates", None)
    p = copy.deepcopy(PARAMS)
    p["candidate_limit"] = 1
    p["cost_controls"].update(dry_run=False, execute_query="ENABLE", use_cache=False)
    p["cost_controls"]["cost_policy_path"] = "configs/cost_policy.yaml"
    p["cost_controls"]["ledger_path"] = str(_params.ledger_path)
    _FakeClient.response = SafeQueryResult(
        decision=SafeQueryDecision.ALLOW, rows=rows, cache_hit=False, estimate=_estimate()
    )
    ctx = _ctx(tmp_path / "u")
    bigquery_gdelt_seeded_theme_scoring_task(ctx, p)
    summary = _read(ctx, "theme_candidate_scoring_summary.json")
    assert summary["family_complete"] is False
    assert summary["statistical_completion_status"] == "incomplete_family_count_unavailable"
    assert summary["bh_skipped_due_to_incomplete_family"] is True


def test_sentinels_not_counted_as_candidates(tmp_path: Path) -> None:
    ctx = _run(tmp_path, _EXEC_ROWS)
    summary = _read(ctx, "theme_candidate_scoring_summary.json")
    # 1 __TOTAL__ + 2 __SEED__ = 3 sentinels; 2 real candidates.
    assert summary["sentinel_row_count"] == 3
    assert summary["total_support_qualified_candidates"] == 2
    assert summary["scored_candidate_count"] == 2


# ── Phase 5: seed-composition denominators ───────────────────────────────────


def test_seed_diagnostics_two_explicit_denominators(tmp_path: Path) -> None:
    ctx = _run(tmp_path, _EXEC_ROWS)
    diag = [
        json.loads(line)
        for line in (ctx.artifacts_dir / "seed_match_diagnostics.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    intel = next(d for d in diag if d["canonical_seed"] == "intel")
    # Unique-record share uses total seed records (5000) -> OVERLAPPING.
    assert intel["unique_seed_records_matching_entity"] == 2000
    assert intel["share_of_unique_seed_records_matching_entity"] == pytest.approx(0.4, abs=1e-6)
    # Match share uses total record-seed matches (6200) -> sums to ~1 across seeds.
    assert intel["share_of_all_record_seed_matches"] == pytest.approx(2000 / 6200, abs=1e-6)
    # Overlapping unique shares do NOT sum to 1 (that's the whole point).
    assert sum(d["share_of_unique_seed_records_matching_entity"] for d in diag) != pytest.approx(
        1.0
    )


# ── Phase 4: concentration provenance ────────────────────────────────────────


def test_concentration_records_approx_and_exact_with_provenance(tmp_path: Path) -> None:
    ctx = _run(tmp_path, _EXEC_ROWS)
    ind = next(
        r for r in ctx.state["gdelt_seeded_theme_scores"] if r["theme"] == "WB_2461_INDUSTRIAL"
    )
    assert ind["approx_top_source_share"] is not None
    assert ind["exact_top_source_share"] is not None
    assert ind["concentration_metric_validated_exactly"] is True
    assert ind["concentration_pass_changed_after_exact_check"] in (True, False)


def test_exact_check_flips_concentration_pass(tmp_path: Path) -> None:
    # APPROX says the top source is fine (<0.5); EXACT reveals near-total source
    # concentration -> concentration decision must flip and be RECORDED.
    rows = copy.deepcopy(_EXEC_ROWS)
    ind = next(r for r in rows if r["theme_code"] == "WB_2461_INDUSTRIAL")
    ind["top_sources"] = [{"value": "reuters.com", "n": 40}]  # approx share 0.1 (pass)
    ind["exact_top_source_count"] = 380  # exact share 0.95 (fail)
    ctx = _run(tmp_path, rows)
    scored = next(
        r for r in ctx.state["gdelt_seeded_theme_scores"] if r["theme"] == "WB_2461_INDUSTRIAL"
    )
    assert scored["approx_top_source_share"] == pytest.approx(0.1, abs=1e-9)
    assert scored["exact_top_source_share"] == pytest.approx(0.95, abs=1e-9)
    assert scored["concentration_eligibility"] is False
    assert scored["concentration_pass_changed_after_exact_check"] is True


# ── Phase 6: eligibility tiers + ranking integrity ───────────────────────────


def test_eligibility_tiers_and_deterministic_ranks(tmp_path: Path) -> None:
    ctx = _run(tmp_path, _EXEC_ROWS)
    scores = ctx.state["gdelt_seeded_theme_scores"]
    ind = next(r for r in scores if r["theme"] == "WB_2461_INDUSTRIAL")
    gen = next(r for r in scores if r["theme"] == "TAX_ECON_PRICE")
    assert ind["statistical_eligibility"] is True
    assert ind["stability_eligibility"] is True
    assert ind["concentration_eligibility"] is True
    assert ind["manual_review_priority"] == "high"
    # Generic theme (lift ~1) fails the statistical screen and is not high.
    assert gen["statistical_eligibility"] is False
    assert gen["manual_review_priority"] != "high"
    # Three distinct rankings all present and deterministic (1-indexed, unique).
    for key in ("raw_frequency_rank", "raw_statistical_rank", "research_adjusted_rank"):
        ranks = sorted(r[key] for r in scores)
        assert ranks == list(range(1, len(scores) + 1))
    # No candidate disappears because of a generic label.
    assert gen in scores


# ── Phase 7: representative evidence artifact ─────────────────────────────────


def test_evidence_artifact_schema_and_deterministic_sampling(tmp_path: Path) -> None:
    ctx = _run(tmp_path, _EXEC_ROWS)
    evidence = [
        json.loads(line)
        for line in (ctx.artifacts_dir / "candidate_representative_evidence.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert evidence, "expected representative evidence rows"
    required = {
        "theme",
        "record_id",
        "record_date",
        "weekly_bucket",
        "source",
        "document_identifier",
        "document_url",
        "matched_seed",
        "canonical_entity",
        "all_matched_seeds",
        "all_matched_entities",
        "sample_selection_method",
        "sample_stratum",
        "evidence_metadata_available",
        "manual_interpretation",
        "identity_context_or_incidental",
        "manual_review_limitations",
    }
    for row in evidence:
        assert required.issubset(row.keys())
        assert row["sample_selection_method"] == "deterministic_hash_farmfingerprint_gkgrecordid"
        # Interpretation columns are never fabricated by the pipeline.
        assert row["manual_interpretation"] is None
        assert row["identity_context_or_incidental"] is None
        assert row["manual_review_limitations"] and "article body" in (
            row["manual_review_limitations"].lower()
        )
    # DocumentIdentifier (URL) is carried from the scan, populating both fields.
    ind = [r for r in evidence if r["theme"] == "WB_2461_INDUSTRIAL"]
    with_url = next(r for r in ind if r["record_id"] == "20260615000000-1")
    assert with_url["document_identifier"] == "https://reuters.com/tech/intel-nvidia-chip-2026"
    assert with_url["document_url"] == with_url["document_identifier"]
    assert with_url["evidence_metadata_available"] is True
    assert with_url["all_matched_seeds"] == ["intel", "nvidia"]
    assert "top_" in with_url["sample_stratum"] or "manual" in with_url["sample_stratum"]
    # Records come from the hash-sampled set, not an alphabetical pick.
    ind_records = {r["record_id"] for r in ind}
    assert "20260615000000-1" in ind_records


# ── Phase 3 (task wiring): sparse-aware hypothesis-test selection ─────────────


def test_candidate_emits_contingency_and_test_selection(tmp_path: Path) -> None:
    ctx = _run(tmp_path, _EXEC_ROWS)
    ind = next(
        r for r in ctx.state["gdelt_seeded_theme_scores"] if r["theme"] == "WB_2461_INDUSTRIAL"
    )
    # Disjoint 2x2: a=400, b=5000-400, c=800-400=400, d=495000-400.
    assert (ind["contingency_a"], ind["contingency_c"]) == (400, 400)
    assert ind["contingency_b"] == 4600 and ind["contingency_d"] == 494600
    assert ind["hypothesis_test"] in {"two_proportion_z_test", "fisher_exact_two_sided"}
    assert ind["hypothesis_test_reason"]
    assert ind["expected_cell_threshold"] == 5.0
    assert ind["minimum_expected_cell_count"] is not None
    assert ind["p_value_valid"] is True and ind["p_value"] is not None


def test_sparse_candidate_uses_fisher_and_counts_in_bh(tmp_path: Path) -> None:
    # A low-support, low-with-theme candidate -> sparse -> Fisher exact.
    rows = copy.deepcopy(_EXEC_ROWS)
    rows.append(
        {
            "theme_code": "WB_5555_RARE",
            "seed_record_count": 30,
            "background_record_count": 33,  # c = 3 non-seed with theme -> sparse
            "distinct_day_count": 8,
            "periods_present": 4,
            "source_count": 12,
            "top_sources": [{"value": "x.com", "n": 5}],
            "top_days": [{"value": "20260610", "n": 4}],
            "exact_top_source_count": 5,
            "exact_top_day_count": 4,
            "seed_hits": [{"seed": "intel", "n": 20}, {"seed": "nvidia", "n": 15}],
            "sample_record_ids": ["r1"],
            "sample_records": [],
            **_weekly(30),
        }
    )
    ctx = _run(tmp_path, rows)
    rare = next(r for r in ctx.state["gdelt_seeded_theme_scores"] if r["theme"] == "WB_5555_RARE")
    assert rare["hypothesis_test"] == "fisher_exact_two_sided"
    assert rare["p_value_valid"] is True and rare["p_value"] is not None
    summary = _read(ctx, "theme_candidate_scoring_summary.json")
    # BH family size counts only valid p-values.
    assert summary["valid_hypothesis_test_count"] == summary["multiple_testing_family_size"]
    assert summary["hypothesis_test_distribution"].get("fisher_exact_two_sided", 0) >= 1


def test_invalid_table_excluded_from_bh_family(tmp_path: Path) -> None:
    rows = copy.deepcopy(_EXEC_ROWS)
    rows.append(
        {
            "theme_code": "WB_9999_ANOMALY",
            "seed_record_count": 900,
            "background_record_count": 800,  # < seed -> invalid non-seed cell
            "distinct_day_count": 10,
            "periods_present": 4,
            "source_count": 50,
            "top_sources": [{"value": "x.com", "n": 10}],
            "top_days": [{"value": "20260610", "n": 9}],
            "exact_top_source_count": 10,
            "exact_top_day_count": 9,
            "seed_hits": [{"seed": "intel", "n": 500}, {"seed": "nvidia", "n": 500}],
            "sample_record_ids": ["q1"],
            "sample_records": [],
            **_weekly(900),
        }
    )
    ctx = _run(tmp_path, rows)
    anom = next(
        r for r in ctx.state["gdelt_seeded_theme_scores"] if r["theme"] == "WB_9999_ANOMALY"
    )
    assert anom["hypothesis_test"] == "unavailable_invalid_table"
    assert anom["p_value"] is None and anom["p_value_valid"] is False
    assert anom["adjusted_p_value"] is None
    summary = _read(ctx, "theme_candidate_scoring_summary.json")
    # 3 candidates, one invalid -> valid test count = 2 = BH family size.
    assert summary["invalid_or_unavailable_test_count"] >= 1
    assert summary["valid_hypothesis_test_count"] == summary["multiple_testing_family_size"]


# ── Phase 5 (task wiring): concentration coverage + provenance ───────────────


def test_concentration_coverage_counts_and_provenance(tmp_path: Path) -> None:
    ctx = _run(tmp_path, _EXEC_ROWS)
    summary = _read(ctx, "theme_candidate_scoring_summary.json")
    assert summary["total_candidate_count"] == 2
    # Exact source/day/seed available for every candidate in the fixture.
    assert summary["candidates_with_exact_source_concentration"] == 2
    assert summary["candidates_with_exact_day_concentration"] == 2
    assert summary["candidates_with_exact_seed_concentration"] == 2
    assert summary["source_screen_uses_exact_when_available"] is True
    assert summary["exact_concentration_selection_rule"]
    ind = next(
        r for r in ctx.state["gdelt_seeded_theme_scores"] if r["theme"] == "WB_2461_INDUSTRIAL"
    )
    assert ind["top_source_share_provenance"] == "exact"
    assert ind["top_day_share_provenance"] == "exact"
    assert ind["top_seed_share_provenance"] == "exact"
    assert ind["top_source_share_used_for_screening"] == ind["exact_top_source_share"]


def test_concentration_approximate_fallback_labeled(tmp_path: Path) -> None:
    # Drop the exact source grain for one candidate: screening falls back to the
    # APPROX sketch and the provenance must say so (never claimed exact).
    rows = copy.deepcopy(_EXEC_ROWS)
    ind = next(r for r in rows if r["theme_code"] == "WB_2461_INDUSTRIAL")
    ind.pop("exact_top_source_count", None)
    ctx = _run(tmp_path, rows)
    scored = next(
        r for r in ctx.state["gdelt_seeded_theme_scores"] if r["theme"] == "WB_2461_INDUSTRIAL"
    )
    assert scored["exact_top_source_share"] is None
    assert scored["top_source_share_provenance"] == "approximate"
    assert scored["top_source_share_used_for_screening"] == scored["approx_top_source_share"]
    summary = _read(ctx, "theme_candidate_scoring_summary.json")
    assert summary["candidates_with_exact_source_concentration"] == 1

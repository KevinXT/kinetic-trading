"""Tests for the generated study report, contrast exports, and pre-build checks.

Covers: required benchmark/instrument bar validation, BigQuery daily-approximation
alignment, non-overclaiming report wording, and that the complete committed
offline fixture can generate a representative study report deterministically.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kinetic.core.pipeline.context import RunContext
from kinetic.processing.cross_asset.validation import assert_required_bar_symbols
from kinetic.research.tasks import build_news_market_dataset_task

FIXED_CLOCK = lambda: datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)  # noqa: E731

STUDY_PARAMS = {
    "counts_path": "tests/fixtures/research/semiconductor_study_bigquery_counts.json",
    "bars_path": "tests/fixtures/research/semiconductor_study_market_bars.json",
    "mappings_path": "configs/research/topic_instrument_mappings.yaml",
    "feature_catalog_path": "configs/research/news_market_feature_catalog.yaml",
    "alignment_policy": "conservative_calendar_day_v2",
    "cutoff_buffer_seconds": 300,
    "dataset_version": "semiconductors-news-market-study-v1",
    "forward_horizon": 5,
    "event_threshold": 1.5,
    "event_minimum_sample": 15,
    "bootstrap_seed": 12345,
    "bootstrap_block_length": 5,
    "bootstrap_iterations": 200,
    "require_symbols": ["AMD", "NVDA", "SMH", "QQQ"],
    "study_window": {"start": "2025-07-01", "end": "2026-06-30"},
    "news_window": {"start": "2025-07-01", "end": "2026-06-30"},
    "market_window": {"start": "2025-05-15", "end": "2026-07-10"},
    "study_report_name": "semiconductor_attention_study.md",
    "study_report_title": "Semiconductor news-attention market study (v1)",
    "study_topic": "semiconductors",
}

FORBIDDEN_CLAIM_TOKENS = ("profitable", "predictive alpha", "causal", "successful strategy")


def _run(tmp_path: Path) -> RunContext:
    ctx = RunContext(cfg={}, run_name="t", run_id="r", run_dir=tmp_path / "run")
    build_news_market_dataset_task(ctx, dict(STUDY_PARAMS), clock=FIXED_CLOCK)
    return ctx


def test_required_bar_symbols_validation() -> None:
    assert_required_bar_symbols({"AMD", "NVDA", "SMH", "QQQ"}, ["AMD", "QQQ"])
    with pytest.raises(ValueError, match="required market bars are missing"):
        assert_required_bar_symbols({"AMD", "NVDA"}, ["AMD", "NVDA", "SMH", "QQQ"])


def test_build_fails_when_required_benchmark_bar_absent(tmp_path: Path) -> None:
    params = dict(STUDY_PARAMS)
    params["require_symbols"] = ["AMD", "NVDA", "SMH", "QQQ", "MISSING_BENCH"]
    ctx = RunContext(cfg={}, run_name="t", run_id="r", run_dir=tmp_path / "run")
    with pytest.raises(ValueError, match="required market bars are missing"):
        build_news_market_dataset_task(ctx, params, clock=FIXED_CLOCK)


def test_bigquery_counts_use_daily_approximation(tmp_path: Path) -> None:
    ctx = _run(tmp_path)
    rows = ctx.state["news_market_observations"]
    assert rows
    assert all(r["alignment_policy"] == "conservative_calendar_day_v2" for r in rows)
    assert all(r["alignment_precision"] == "daily_approximation" for r in rows)


def test_report_generated_and_representative(tmp_path: Path) -> None:
    ctx = _run(tmp_path)
    report_path = ctx.artifacts_dir / "semiconductor_attention_study.md"
    assert report_path.is_file()
    text = report_path.read_text(encoding="utf-8")
    for section in (
        "## Data coverage",
        "## Event definition",
        "## Primary H1 results",
        "## Secondary outcomes",
        "## Interpretation",
        "## Limitations",
    ):
        assert section in text
    assert "news_attention_zscore_30 >= 1.5" in text

    contrasts = json.loads(
        (ctx.artifacts_dir / "event_control_contrasts.json").read_text(encoding="utf-8")
    )["contrasts"]
    pooled_abs = next(
        c
        for c in contrasts
        if c["grouping"] == "pooled" and c["response"] == "target_session_absolute_return"
    )
    # Representative: both groups clear the minimum inference sample and the
    # primary pooled contrast is eligible with an adjusted p-value.
    assert pooled_abs["event_date_count"] >= 15
    assert pooled_abs["control_date_count"] >= 15
    assert pooled_abs["inferential_status"] == "eligible"
    assert pooled_abs["adjusted_p_value"] is not None


def test_report_has_no_profitability_alpha_or_causal_claims(tmp_path: Path) -> None:
    ctx = _run(tmp_path)
    text = (
        (ctx.artifacts_dir / "semiconductor_attention_study.md").read_text(encoding="utf-8").lower()
    )
    for token in FORBIDDEN_CLAIM_TOKENS:
        assert token not in text, f"forbidden claim token in report: {token!r}"


def test_study_report_and_contrasts_are_byte_stable(tmp_path: Path) -> None:
    ctx_a = _run(tmp_path / "a")
    ctx_b = _run(tmp_path / "b")
    for name in (
        "semiconductor_attention_study.md",
        "event_control_contrasts.json",
        "event_control_contrasts.csv",
    ):
        a = (ctx_a.artifacts_dir / name).read_bytes()
        b = (ctx_b.artifacts_dir / name).read_bytes()
        assert a == b, f"{name} is not byte-stable across fixed-clock reruns"

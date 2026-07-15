"""Integration tests: task registration, artifacts, offline determinism."""

import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline_core.engine.context import RunContext
from research_data.task import build_news_market_dataset_task

FIXED_CLOCK = lambda: datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)  # noqa: E731

DEMO_PARAMS = {
    "articles_path": "tests/fixtures/research/semiconductors_articles.json",
    "bars_path": "tests/fixtures/research/market_bars.json",
    "mappings_path": "configs/research/topic_instrument_mappings.yaml",
    "feature_catalog_path": "configs/research/news_market_feature_catalog.yaml",
    "alignment_policy": "session_information_window_v2",
    "cutoff_buffer_seconds": 300,
    "dataset_version": "news-market-v2",
    "forward_horizon": 5,
    "event_threshold": 2.0,
    "event_minimum_sample": 5,
    "max_records": 250,
}

EXPECTED_ARTIFACTS = {
    "news_topic_daily_features.jsonl",
    "session_news_features.jsonl",
    "market_session_features.jsonl",
    "news_market_observations.jsonl",
    "news_market_observations.csv",
    "feature_catalog.json",
    "dataset_manifest.json",
    "join_summary.json",
    "event_study_events.jsonl",
    "event_study_summary.json",
    "event_study_summary.csv",
    "research_limitations.md",
}


def _run(tmp_path: Path) -> RunContext:
    ctx = RunContext(cfg={}, run_name="t", run_id="r", run_dir=tmp_path / "run")
    build_news_market_dataset_task(ctx, dict(DEMO_PARAMS), clock=FIXED_CLOCK)
    return ctx


def test_task_registered_in_platform() -> None:
    import trading_platform  # noqa: F401  (import triggers registration)
    from pipeline_core.tasks.registry import TASK_REGISTRY

    assert "build_news_market_dataset" in TASK_REGISTRY


def test_task_produces_state_and_artifacts(tmp_path: Path) -> None:
    ctx = _run(tmp_path)
    for key in (
        "news_topic_daily_features",
        "session_news_features",
        "market_session_features",
        "news_market_observations",
        "event_study_results",
    ):
        assert key in ctx.state and ctx.state[key]
    produced = {p.name for p in ctx.artifacts_dir.iterdir()}
    assert EXPECTED_ARTIFACTS.issubset(produced)


def test_observations_are_deterministically_ordered(tmp_path: Path) -> None:
    ctx = _run(tmp_path)
    rows = ctx.state["news_market_observations"]
    keys = [
        (r["session_date"], r["topic"], r["symbol"], r["news_measurement_method"]) for r in rows
    ]
    assert keys == sorted(keys)


def test_fixed_clock_rerun_is_byte_stable(tmp_path: Path) -> None:
    ctx_a = _run(tmp_path / "a")
    ctx_b = _run(tmp_path / "b")
    for name in EXPECTED_ARTIFACTS:
        a = (ctx_a.artifacts_dir / name).read_bytes()
        b = (ctx_b.artifacts_dir / name).read_bytes()
        assert a == b, f"artifact {name} is not byte-stable across reruns"


def test_manifest_uses_fixed_clock(tmp_path: Path) -> None:
    ctx = _run(tmp_path)
    manifest = json.loads((ctx.artifacts_dir / "dataset_manifest.json").read_text())
    assert manifest["generated_at"] == "2026-07-01T12:00:00Z"


def test_modern_ingestion_clock_is_not_historical_feature_availability(tmp_path: Path) -> None:
    ctx = _run(tmp_path)
    row = ctx.state["news_market_observations"][0]
    assert row["availability_assumption"] == "publication_timestamp_proxy_v1"
    assert row["feature_available_at"] == row["feature_cutoff"]
    assert row["feature_available_at"] != "2026-07-01T12:00:00Z"


def test_naive_same_date_join_would_be_caught() -> None:
    # A naive same-date join (news date D predicts close of D) would put a
    # same-session return into inputs. The audit forbids that. Confirm no input
    # key carries a forward-return name in the real dataset.
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        ctx = _run(Path(d))
        rows = ctx.state["news_market_observations"]
        for r in rows:
            for key in r["inputs"]:
                assert not key.startswith(("target_", "fwd_", "next_session"))


def test_current_artifacts_use_v2_semantic_names_and_prefixed_csv(tmp_path: Path) -> None:
    ctx = _run(tmp_path)
    rows = ctx.state["news_market_observations"]
    serialized = json.dumps(rows)
    assert "canonical_article" not in serialized
    assert "next_session_return" not in serialized
    assert "fwd_5" not in serialized
    assert "abnormal_return" not in serialized
    assert "news_title_deduplicated_article_count" in serialized
    assert "target_session_return" in serialized
    header = (
        (ctx.artifacts_dir / "news_market_observations.csv")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert "input.news_attention_count" in header
    assert "target.target_session_return" in header
    assert "mkt_volume" not in header  # contemporaneous fields are not predictive exports

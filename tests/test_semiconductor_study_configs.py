"""Config-parsing guards for the semiconductor attention study.

These assert the study configs are well-formed, use the daily approximation and
partition pruning, keep credentials out of config, and that the semiconductors
theme bundle is a review-gated placeholder until real discovery codes are added.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from common.config_builder import load_yaml
from common.errors import ConfigError
from news_data.bigquery.theme_bundles import get_bundle_themes, load_theme_bundles

RESEARCH_DIR = Path("configs/research")

THEME_DRYRUN = "semiconductors_theme_discovery_90d_dryrun.yaml"
THEME_EXECUTE = "semiconductors_theme_discovery_90d_execute.yaml"
COUNT_DRYRUN = "semiconductors_bigquery_1y_partition_dryrun.yaml"
COUNT_EXECUTE = "semiconductors_bigquery_1y_partition_execute.yaml"
ALPACA = "semiconductors_alpaca_bars.yaml"
STUDY_V1 = "semiconductors_news_market_study_v1.yaml"
STUDY_OFFLINE = "semiconductors_news_market_study_offline.yaml"

# Targeted, semiconductor-specific patterns. The bare terms `foundry`, `wafer`,
# and `lithography` were intentionally removed (unsafe substrings), and discovery
# now runs in token mode so a pattern must match a whole `_`-delimited segment.
EXPECTED_THEME_PATTERNS = {
    "semiconductor",
    "semiconductors",
    "microelectronics",
    "microprocessor",
    "microprocessors",
    "integrated_circuit",
    "integrated_circuits",
    "silicon_chip",
    "computer_chip",
    "chipmaker",
    "chipmakers",
    "chip_manufacturing",
    "wafer_fabrication",
    "photolithography",
    "fabless",
}
UNSAFE_BARE_PATTERNS = {"foundry", "wafer", "lithography"}


def _ingest(cfg: dict) -> dict:
    return (cfg.get("pipeline", {}) or {}).get("ingest", {}) or {}


def _strategy(cfg: dict) -> dict:
    return (cfg.get("pipeline", {}) or {}).get("strategy", {}) or {}


def test_all_study_configs_exist() -> None:
    for name in (
        THEME_DRYRUN,
        THEME_EXECUTE,
        COUNT_DRYRUN,
        COUNT_EXECUTE,
        ALPACA,
        STUDY_V1,
        STUDY_OFFLINE,
    ):
        assert (RESEARCH_DIR / name).is_file(), f"missing config {name}"


@pytest.mark.parametrize("name", [THEME_DRYRUN, THEME_EXECUTE])
def test_theme_discovery_configs(name: str) -> None:
    cfg = load_yaml(RESEARCH_DIR / name)
    ingest = _ingest(cfg)
    assert ingest.get("source") == "bigquery_gdelt_theme_discovery"
    assert ingest.get("topic") == "semiconductors"
    patterns = ingest.get("theme_search_patterns", [])
    assert set(patterns) == EXPECTED_THEME_PATTERNS
    # No unsafe bare substring pattern survives.
    assert not (set(patterns) & UNSAFE_BARE_PATTERNS)
    # Discovery uses safer token matching, not substring.
    assert ingest.get("match_mode") == "token"
    # The rejected false positive is recorded so a reappearance is flagged.
    assert "TAX_FNCACT_FOUNDRYMAN" in (ingest.get("known_rejected_theme_codes") or [])
    pf = ingest.get("partition_filter", {}) or {}
    assert pf.get("enabled") is True and pf.get("column") == "_PARTITIONTIME"
    cc = ingest.get("cost_controls", {}) or {}
    assert cc.get("use_cache") is False
    assert isinstance(cc.get("maximum_bytes_billed"), int) and cc["maximum_bytes_billed"] > 0


def test_theme_discovery_execute_requires_typed_gate() -> None:
    dry = _ingest(load_yaml(RESEARCH_DIR / THEME_DRYRUN)).get("cost_controls", {})
    ex = _ingest(load_yaml(RESEARCH_DIR / THEME_EXECUTE)).get("cost_controls", {})
    assert dry.get("dry_run") is True and dry.get("execute_query", "") != "ENABLE"
    assert ex.get("dry_run") is False and ex.get("execute_query") == "ENABLE"


@pytest.mark.parametrize("name", [COUNT_DRYRUN, COUNT_EXECUTE])
def test_count_configs_use_partition_and_daily_window(name: str) -> None:
    cfg = load_yaml(RESEARCH_DIR / name)
    ingest = _ingest(cfg)
    assert ingest.get("source") == "bigquery_gdelt_counts"
    assert ingest.get("topic") == "semiconductors"
    assert ingest.get("theme_bundle") == "semiconductors"
    assert ingest.get("search_columns") == ["V2Themes"]
    window = ingest.get("window", {}) or {}
    assert window.get("start") == "2025-07-01"
    assert window.get("end") == "2026-06-30"
    assert window.get("bucket") == "1d"
    pf = ingest.get("partition_filter", {}) or {}
    assert pf.get("enabled") is True and pf.get("column") == "_PARTITIONTIME"


def test_count_execute_requires_typed_gate_and_dryrun_is_dry() -> None:
    dry = _ingest(load_yaml(RESEARCH_DIR / COUNT_DRYRUN)).get("cost_controls", {})
    ex = _ingest(load_yaml(RESEARCH_DIR / COUNT_EXECUTE)).get("cost_controls", {})
    assert dry.get("dry_run") is True and dry.get("execute_query", "") != "ENABLE"
    assert ex.get("dry_run") is False and ex.get("execute_query") == "ENABLE"
    for cc in (dry, ex):
        assert isinstance(cc.get("maximum_bytes_billed"), int) and cc["maximum_bytes_billed"] > 0


def test_alpaca_config_symbols_identity_and_no_credentials() -> None:
    cfg = load_yaml(RESEARCH_DIR / ALPACA)
    request = _ingest(cfg).get("request", {}) or {}
    assert request.get("symbols") == ["AMD", "NVDA", "SMH", "QQQ"]
    assert request.get("timeframe") == "1Day"
    assert request.get("feed") == "iex"
    assert request.get("adjustment") == "all"
    assert request.get("sort") == "asc"
    assert request.get("start") == "2025-05-15T00:00:00Z"
    assert request.get("end") == "2026-07-10T23:59:59Z"
    alpaca = ((cfg.get("providers", {}) or {}).get("market", {}) or {}).get("alpaca", {}) or {}
    # Credentials are referenced by env var NAME only; never inlined.
    assert alpaca.get("key_id_env") == "ALPACA_API_KEY_ID"
    assert alpaca.get("secret_key_env") == "ALPACA_API_SECRET_KEY"
    raw = (RESEARCH_DIR / ALPACA).read_text(encoding="utf-8")
    for secretish in ("api_key:", "secret_key:", "AKIA", "key_id:"):
        assert secretish not in raw


@pytest.mark.parametrize("name", [STUDY_V1, STUDY_OFFLINE])
def test_study_build_configs_are_fixed_and_conservative(name: str) -> None:
    cfg = load_yaml(RESEARCH_DIR / name)
    strat = _strategy(cfg)
    assert strat.get("type") == "build_news_market_dataset"
    assert strat.get("alignment_policy") == "conservative_calendar_day_v2"
    assert strat.get("dataset_version") == "semiconductors-news-market-study-v1"
    assert strat.get("event_threshold") == 1.5
    assert strat.get("event_minimum_sample") == 15
    assert strat.get("bootstrap_seed") == 12345
    assert strat.get("bootstrap_block_length") == 5
    assert strat.get("bootstrap_iterations") == 2000
    assert "QQQ" in strat.get("require_symbols", [])
    assert strat.get("study_report_name") == "semiconductor_attention_study.md"
    assert strat.get("study_window", {}).get("start") == "2025-07-01"
    assert strat.get("study_window", {}).get("end") == "2026-06-30"


def test_v1_consumes_executed_artifacts_not_raw_alignment() -> None:
    strat = _strategy(load_yaml(RESEARCH_DIR / STUDY_V1))
    assert "bigquery_daily_counts.jsonl" in strat.get("counts_path", "")
    assert strat.get("bars_path", "").endswith(".jsonl")
    # Must not silently downgrade from the session-information window here.
    assert strat.get("alignment_policy") != "session_information_window_v2"


def test_semiconductors_theme_bundle_is_review_gated_placeholder() -> None:
    bundles = load_theme_bundles()
    assert "semiconductors" in bundles
    assert bundles["semiconductors"] == []  # placeholder: no validated codes yet
    with pytest.raises(ConfigError, match="placeholder pending human review"):
        get_bundle_themes("semiconductors")

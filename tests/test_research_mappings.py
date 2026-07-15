"""Tests for topic->instrument mapping loading and validation."""

from datetime import date
from pathlib import Path

import pytest
from research_data.mappings import active_mappings_for_topic, load_topic_instrument_mappings

REPO_MAPPINGS = "configs/research/topic_instrument_mappings.yaml"


def _write(tmp_path: Path, body: str) -> str:
    path = tmp_path / "m.yaml"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_repo_mappings_load_and_order_deterministically() -> None:
    version, mappings = load_topic_instrument_mappings(REPO_MAPPINGS)
    assert version == "news-market-mapping-v1"
    keys = [(m.topic, m.instrument_id) for m in mappings]
    assert keys == sorted(keys)
    semis = active_mappings_for_topic(mappings, "semiconductors", date(2026, 6, 1))
    assert {m.symbol for m in semis} == {"SMH", "NVDA", "AMD"}


def test_duplicate_mapping_rejected(tmp_path: Path) -> None:
    entry = (
        "      - instrument_id: a\n"
        "        symbol: SMH\n"
        "        relationship: sector_etf\n"
        "        exposure_type: direct_sector\n"
        "        weight: 1.0\n"
        "        benchmark_symbol: QQQ\n"
    )
    body = "mapping_version: v1\ntopics:\n  semiconductors:\n    instruments:\n" + entry + entry
    with pytest.raises(ValueError, match="duplicate"):
        load_topic_instrument_mappings(_write(tmp_path, body))


def test_benchmark_self_reference_rejected_for_company(tmp_path: Path) -> None:
    body = (
        "mapping_version: v1\n"
        "topics:\n"
        "  semiconductors:\n"
        "    instruments:\n"
        "      - instrument_id: nvda\n"
        "        symbol: NVDA\n"
        "        relationship: constituent\n"
        "        exposure_type: company\n"
        "        weight: 1.0\n"
        "        benchmark_symbol: NVDA\n"
    )
    with pytest.raises(ValueError, match="self-reference"):
        load_topic_instrument_mappings(_write(tmp_path, body))


def test_broad_market_may_self_benchmark(tmp_path: Path) -> None:
    body = (
        "mapping_version: v1\n"
        "topics:\n"
        "  ai:\n"
        "    instruments:\n"
        "      - instrument_id: qqq\n"
        "        symbol: QQQ\n"
        "        relationship: broad_market\n"
        "        exposure_type: broad_index\n"
        "        weight: 1.0\n"
        "        benchmark_symbol: QQQ\n"
    )
    _, mappings = load_topic_instrument_mappings(_write(tmp_path, body))
    assert mappings[0].benchmark_symbol == "QQQ"


def test_invalid_validity_window_rejected(tmp_path: Path) -> None:
    body = (
        "mapping_version: v1\n"
        "topics:\n"
        "  t:\n"
        "    instruments:\n"
        "      - instrument_id: a\n"
        "        symbol: X\n"
        "        relationship: sector_etf\n"
        "        exposure_type: direct_sector\n"
        "        weight: 1.0\n"
        "        benchmark_symbol: SPY\n"
        "        valid_from: 2026-12-31\n"
        "        valid_to: 2026-01-01\n"
    )
    with pytest.raises(ValueError, match="valid_from"):
        load_topic_instrument_mappings(_write(tmp_path, body))


def test_inactive_mapping_excluded_by_date(tmp_path: Path) -> None:
    body = (
        "mapping_version: v1\n"
        "topics:\n"
        "  t:\n"
        "    instruments:\n"
        "      - instrument_id: a\n"
        "        symbol: X\n"
        "        relationship: sector_etf\n"
        "        exposure_type: direct_sector\n"
        "        weight: 1.0\n"
        "        benchmark_symbol: SPY\n"
        "        valid_from: 2026-06-01\n"
        "        valid_to: 2026-06-30\n"
    )
    _, mappings = load_topic_instrument_mappings(_write(tmp_path, body))
    assert active_mappings_for_topic(mappings, "t", date(2026, 5, 1)) == []
    assert len(active_mappings_for_topic(mappings, "t", date(2026, 6, 15))) == 1

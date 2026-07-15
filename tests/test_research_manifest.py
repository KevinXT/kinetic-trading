"""Tests for the dataset manifest: fingerprint, counts, no secrets/paths."""

import json
from datetime import date, datetime, timezone

from research_data.builder import build_dataset
from research_data.calendar import MarketCalendar
from research_data.catalog import load_feature_catalog
from research_data.manifest import config_fingerprint
from research_data.models import TopicInstrumentMapping

CAL = MarketCalendar()
CATALOG = load_feature_catalog("configs/research/news_market_feature_catalog.yaml")
OBS = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _mapping():
    return TopicInstrumentMapping(
        mapping_version="v1",
        topic="semiconductors",
        instrument_id="nvda",
        symbol="NVDA",
        relationship="constituent",
        exposure_type="company",
        weight=1.0,
        benchmark_symbol="QQQ",
        sector_benchmark_symbol=None,
        valid_from=None,
        valid_to=None,
        research_rationale="t",
    )


def _bar(symbol, sess, c):
    ts = datetime(sess.year, sess.month, sess.day, 4, tzinfo=timezone.utc)
    return {
        "symbol": symbol,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "timeframe": "1Day",
        "open": c,
        "high": c * 1.02,
        "low": c * 0.98,
        "close": c,
        "volume": 1000,
        "vwap": c,
        "trade_count": 50,
        "provider": "alpaca",
        "feed": "iex",
        "adjustment": "all",
        "currency": "USD",
        "retrieved_at": "2026-07-01T00:00:00Z",
    }


def _build():
    sess = CAL.sessions_between(date(2026, 6, 1), date(2026, 6, 26))
    bars = []
    p = 100.0
    for s in sess:
        bars += [_bar("NVDA", s, p), _bar("QQQ", s, p)]
        p *= 1.01
    articles = [
        {
            "title": f"chip {s}",
            "domain": "a.com",
            "source_country": "United States",
            "published_at": f"{s.isoformat()}T12:00:00Z",
            "topics": ["semiconductors"],
            "primary_topic": "semiconductors",
            "query": "chip",
        }
        for s in sess
    ]
    return build_dataset(
        dataset_version="v1",
        feature_version="fv1",
        topic_taxonomy_version="t1",
        calendar=CAL,
        catalog=CATALOG,
        mapping_version="v1",
        mappings=(_mapping(),),
        bars=bars,
        articles=articles,
        alignment_policy_name="session_information_window_v2",
        observed_at=OBS,
        generated_at=OBS,
        forward_horizon=5,
    )


def test_config_fingerprint_is_stable_and_order_independent() -> None:
    a = config_fingerprint({"x": 1, "y": 2})
    b = config_fingerprint({"y": 2, "x": 1})
    assert a == b


def test_manifest_counts_and_versions() -> None:
    result = _build()
    m = result.manifest.to_dict()
    assert m["row_counts"]["news_market_observations"] == len(result.observations)
    assert m["feature_catalog_version"] == CATALOG.catalog_version
    assert m["source_schema_versions"]["market"] == "financial-data.v1"
    assert m["news_measurement_methods"] == ["gdelt_doc_artlist"]
    assert m["cutoff_buffer_seconds"] == 300
    assert m["availability_assumptions"] == ["publication_timestamp_proxy_v1"]
    assert m["alignment_precision_counts"] == {"article_timestamp": len(result.observations)}
    assert m["market_calendar_name"] == "us_equity_session_calendar_v1"
    assert m["bootstrap_unit"] == "session_date"
    assert m["bootstrap_block_length"] == 5
    assert m["hypothesis_registry_version"] == "news-market-hypotheses-v2"


def test_manifest_has_no_secrets_or_absolute_paths() -> None:
    result = _build()
    text = json.dumps(result.manifest.to_dict())
    lowered = text.lower()
    for banned in ("api_key", "secret", "password", "/users/", "/home/", "token"):
        assert banned not in lowered


def test_manifest_records_missingness_and_splits() -> None:
    result = _build()
    m = result.manifest.to_dict()
    assert "missingness_counts" in m
    assert m["splits"]["policy"] == "chronological_60_20_20_with_embargo"

"""Tests for the joined dataset and its leakage protections."""

import math
from datetime import date, datetime, timezone

import pytest
from research_data.builder import build_dataset
from research_data.calendar import MarketCalendar
from research_data.catalog import load_feature_catalog
from research_data.models import TopicInstrumentMapping
from research_data.validation import (
    assert_no_leakage,
    audit_dataset,
    predictive_feature_row,
)

CAL = MarketCalendar()
CATALOG = load_feature_catalog("configs/research/news_market_feature_catalog.yaml")
OBS = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _mapping(
    topic, symbol, instrument_id, relationship="constituent", exposure="company", bench="QQQ"
):
    return TopicInstrumentMapping(
        mapping_version="v1",
        topic=topic,
        instrument_id=instrument_id,
        symbol=symbol,
        relationship=relationship,
        exposure_type=exposure,
        weight=1.0,
        benchmark_symbol=bench,
        sector_benchmark_symbol=None,
        valid_from=None,
        valid_to=None,
        research_rationale="test",
    )


def _bar(symbol, sess, c, o=None, v=1000):
    o = o if o is not None else c
    ts = datetime(sess.year, sess.month, sess.day, 4, tzinfo=timezone.utc)
    return {
        "symbol": symbol,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "timeframe": "1Day",
        "open": o,
        "high": max(o, c) * 1.02,
        "low": min(o, c) * 0.98,
        "close": c,
        "volume": v,
        "vwap": c,
        "trade_count": 50,
        "provider": "alpaca",
        "feed": "iex",
        "adjustment": "all",
        "currency": "USD",
        "retrieved_at": "2026-07-01T00:00:00Z",
    }


def _article(title, domain, iso, topics=None):
    return {
        "title": title,
        "domain": domain,
        "source_country": "United States",
        "published_at": iso,
        "topics": topics or ["semiconductors"],
        "primary_topic": (topics or ["semiconductors"])[0],
        "query": "chip",
    }


def _sessions(start, end):
    return CAL.sessions_between(start, end)


def _build(articles, bars, mappings, **kw):
    return build_dataset(
        dataset_version="v1",
        feature_version="fv1",
        topic_taxonomy_version="t1",
        calendar=CAL,
        catalog=CATALOG,
        mapping_version="v1",
        mappings=tuple(mappings),
        bars=bars,
        articles=articles or None,
        alignment_policy_name="session_information_window_v2",
        observed_at=OBS,
        generated_at=OBS,
        forward_horizon=5,
        **kw,
    )


def test_intraday_news_does_not_predict_same_session_close() -> None:
    sess = _sessions(date(2026, 6, 15), date(2026, 6, 26))
    bars = []
    price = 100.0
    for s in sess:
        bars.append(_bar("NVDA", s, price))
        bars.append(_bar("QQQ", s, price))
        price *= 1.01
    # Article published Monday 15:00 UTC = 11:00 ET (intraday, after cutoff).
    articles = [_article("chip midday", "a.com", "2026-06-15T15:00:00Z")]
    result = _build(articles, bars, [_mapping("semiconductors", "NVDA", "nvda")])
    # No observation is aligned to Monday 6/15 from an intraday-Monday article.
    monday = [o for o in result.observations if o.session_date.isoformat() == "2026-06-15"]
    assert monday == []
    tuesday = [o for o in result.observations if o.session_date.isoformat() == "2026-06-16"]
    assert len(tuesday) == 1
    assert tuesday[0].feature_available_at <= tuesday[0].feature_cutoff
    assert tuesday[0].feature_cutoff < tuesday[0].target_session_open


def test_full_dataset_passes_leakage_audit() -> None:
    sess = _sessions(date(2026, 6, 1), date(2026, 6, 26))
    bars = []
    p = 100.0
    for s in sess:
        bars.append(_bar("NVDA", s, p))
        bars.append(_bar("QQQ", s, p))
        p *= 1.005
    articles = [_article("chip", "a.com", f"{s.isoformat()}T12:00:00Z") for s in sess]
    result = _build(articles, bars, [_mapping("semiconductors", "NVDA", "nvda")])
    assert audit_dataset(result.observations) == []
    assert_no_leakage(result.observations)  # does not raise


def test_no_target_key_appears_in_inputs() -> None:
    sess = _sessions(date(2026, 6, 1), date(2026, 6, 12))
    bars = []
    p = 100.0
    for s in sess:
        bars += [_bar("NVDA", s, p), _bar("QQQ", s, p)]
        p *= 1.01
    articles = [_article("chip", "a.com", f"{s.isoformat()}T12:00:00Z") for s in sess]
    result = _build(articles, bars, [_mapping("semiconductors", "NVDA", "nvda")])
    for obs in result.observations:
        for key in obs.inputs:
            assert not key.startswith(("target_", "fwd_", "next_session"))


def test_one_topic_maps_to_multiple_instruments() -> None:
    sess = _sessions(date(2026, 6, 1), date(2026, 6, 12))
    bars = []
    p = 100.0
    for s in sess:
        for sym in ("NVDA", "AMD", "QQQ"):
            bars.append(_bar(sym, s, p))
        p *= 1.01
    articles = [_article("chip", "a.com", f"{s.isoformat()}T12:00:00Z") for s in sess]
    mappings = [
        _mapping("semiconductors", "NVDA", "nvda"),
        _mapping("semiconductors", "AMD", "amd"),
    ]
    result = _build(articles, bars, mappings)
    day = [o for o in result.observations if o.session_date.isoformat() == sess[5].isoformat()]
    assert {o.symbol for o in day} == {"NVDA", "AMD"}


def test_forward_cumulative_return_uses_prior_base_and_later_sessions() -> None:
    sess = _sessions(date(2026, 6, 1), date(2026, 6, 26))
    bars = []
    closes = {}
    p = 100.0
    for s in sess:
        closes[s] = p
        bars += [_bar("NVDA", s, p), _bar("QQQ", s, p)]
        p *= 1.02
    articles = [_article("chip", "a.com", f"{s.isoformat()}T12:00:00Z") for s in sess]
    result = _build(articles, bars, [_mapping("semiconductors", "NVDA", "nvda")])
    # Find an observation with a complete forward-5 horizon.
    target = next(
        o
        for o in result.observations
        if o.quality.get("target_through_plus_4_complete")
        and o.targets.get("target_through_plus_4_cumulative_return") is not None
    )
    s = target.session_date
    idx = sess.index(s)
    base = closes[sess[idx - 1]]
    end = closes[sess[idx + 4]]
    assert math.isclose(
        target.targets["target_through_plus_4_cumulative_return"],
        end / base - 1,
        rel_tol=1e-9,
    )


def test_target_session_and_plus_4_boundaries_are_explicit() -> None:
    sess = _sessions(date(2026, 6, 1), date(2026, 6, 26))
    bars = []
    instrument_closes = {}
    benchmark_closes = {}
    for index, session in enumerate(sess):
        instrument_closes[session] = 100.0 + index * 2
        benchmark_closes[session] = 100.0 + index
        bars += [
            _bar("NVDA", session, instrument_closes[session]),
            _bar("QQQ", session, benchmark_closes[session]),
        ]
    articles = [_article("chip", "a.com", f"{s.isoformat()}T12:00:00Z") for s in sess]
    result = _build(articles, bars, [_mapping("semiconductors", "NVDA", "nvda")])
    obs = next(
        row
        for row in result.observations
        if row.quality["target_through_plus_4_complete"] and sess.index(row.session_date) >= 1
    )
    index = sess.index(obs.session_date)
    expected_target = instrument_closes[sess[index]] / instrument_closes[sess[index - 1]] - 1
    expected_benchmark = benchmark_closes[sess[index]] / benchmark_closes[sess[index - 1]] - 1
    expected_plus_4 = instrument_closes[sess[index + 4]] / instrument_closes[sess[index - 1]] - 1
    assert math.isclose(obs.targets["target_session_return"], expected_target)
    assert math.isclose(
        obs.targets["target_session_benchmark_adjusted_return"],
        expected_target - expected_benchmark,
    )
    assert math.isclose(obs.targets["target_through_plus_4_cumulative_return"], expected_plus_4)


def test_missing_market_bar_is_recorded_not_invented() -> None:
    sess = _sessions(date(2026, 6, 1), date(2026, 6, 12))
    bars = []
    p = 100.0
    for s in sess:
        # NVDA missing on the 5th session.
        if s != sess[5]:
            bars.append(_bar("NVDA", s, p))
        bars.append(_bar("QQQ", s, p))
        p *= 1.01
    articles = [_article("chip", "a.com", f"{s.isoformat()}T12:00:00Z") for s in sess]
    result = _build(articles, bars, [_mapping("semiconductors", "NVDA", "nvda")])
    missing = [o for o in result.observations if o.session_date == sess[5] and o.symbol == "NVDA"]
    assert len(missing) == 1
    assert missing[0].quality["market_coverage_status"] == "market_bar_missing"
    # No forward return is invented for a missing bar; completeness is False.
    assert missing[0].targets.get("target_session_return") is None
    assert missing[0].quality["target_session_complete"] is False


def test_deterministic_rerun_is_byte_stable() -> None:
    sess = _sessions(date(2026, 6, 1), date(2026, 6, 12))
    bars = []
    p = 100.0
    for s in sess:
        bars += [_bar("NVDA", s, p), _bar("QQQ", s, p)]
        p *= 1.01
    articles = [_article("chip", "a.com", f"{s.isoformat()}T12:00:00Z") for s in sess]
    a = _build(articles, bars, [_mapping("semiconductors", "NVDA", "nvda")])
    b = _build(articles, bars, [_mapping("semiconductors", "NVDA", "nvda")])
    assert [o.to_dict() for o in a.observations] == [o.to_dict() for o in b.observations]
    assert a.manifest.config_fingerprint == b.manifest.config_fingerprint


def test_predictive_export_excludes_targets_and_contemporaneous() -> None:
    sess = _sessions(date(2026, 6, 1), date(2026, 6, 12))
    bars = []
    for session in sess:
        bars += [_bar("NVDA", session, 100), _bar("QQQ", session, 100)]
    result = _build(
        [_article("chip", "a.com", f"{session.isoformat()}T12:00:00Z") for session in sess],
        bars,
        [_mapping("semiconductors", "NVDA", "nvda")],
    )
    exported = predictive_feature_row(result.observations[0], CATALOG)
    assert "target_session_return" not in exported
    assert "mkt_volume" not in exported
    assert "news_attention_count" in exported


def test_bigquery_exact_alignment_fails_without_explicit_downgrade() -> None:
    sessions = _sessions(date(2026, 6, 1), date(2026, 6, 12))
    bars = [
        bar
        for session in sessions
        for bar in (_bar("NVDA", session, 100), _bar("QQQ", session, 100))
    ]
    with pytest.raises(ValueError, match="cannot use session_information_window_v2"):
        _build(
            [],
            bars,
            [_mapping("semiconductors", "NVDA", "nvda")],
            count_rows=[
                {
                    "topic": "semiconductors",
                    "date": "2026-06-01",
                    "article_count": 2,
                }
            ],
        )


def test_mixed_sources_preserve_per_row_alignment_precision_with_downgrade() -> None:
    sessions = _sessions(date(2026, 6, 1), date(2026, 6, 12))
    bars = [
        bar
        for session in sessions
        for bar in (_bar("NVDA", session, 100), _bar("QQQ", session, 100))
    ]
    result = _build(
        [_article("chip", "a.com", "2026-06-01T12:00:00Z")],
        bars,
        [_mapping("semiconductors", "NVDA", "nvda")],
        count_rows=[
            {
                "topic": "semiconductors",
                "date": "2026-06-01",
                "article_count": 2,
            }
        ],
        allow_alignment_downgrade=True,
    )
    precisions = {
        row.news_measurement_method: row.alignment_precision for row in result.observations
    }
    assert precisions == {
        "gdelt_doc_artlist": "article_timestamp",
        "bigquery_gdelt_counts": "daily_approximation",
    }
    bq_row = next(
        row for row in result.observations if row.news_measurement_method == "bigquery_gdelt_counts"
    )
    assert bq_row.lineage["alignment_downgraded"] is True
    assert result.manifest.alignment_precision_counts["daily_approximation"] > 0
    assert any("downgraded" in warning for warning in result.manifest.coverage_warnings)

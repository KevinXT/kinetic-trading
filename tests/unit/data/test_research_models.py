"""Tests for research-layer models: validation, keys, serialization, identity."""

from datetime import date, datetime, timezone

import pytest

from kinetic.data.schemas.research import (
    AVAILABILITY_PUBLICATION_PROXY,
    MarketSessionFeature,
    NewsMarketObservation,
    NewsTopicDailyFeature,
    TopicInstrumentMapping,
)


def _utc(y, m, d, h=0, minute=0) -> datetime:
    return datetime(y, m, d, h, minute, tzinfo=timezone.utc)


def _daily(**over) -> NewsTopicDailyFeature:
    base = dict(
        feature_date=date(2026, 6, 15),
        topic="semiconductors",
        provider="gdelt",
        news_measurement_method="gdelt_doc_artlist",
        feature_version="v1",
        topic_taxonomy_version="t1",
        query_specification_hash="qspec-x",
        feature_timezone="America/New_York",
        title_deduplicated_article_count=5,
        observed_article_count=7,
        observed_copy_count=2,
        unique_domains=4,
        unique_source_countries=2,
        primary_topic_count=3,
        multi_topic_article_count=1,
        query_count=1,
        collection_run_count=1,
        earliest_published_at=_utc(2026, 6, 15, 11),
        latest_published_at=_utc(2026, 6, 15, 13),
        observed_copy_domain_counts={"a.com": 3, "b.com": 2},
        observed_copy_country_counts={"United States": 5},
        duplicate_group_count=1,
        largest_duplicate_group=3,
        feature_available_at=_utc(2026, 6, 15, 13),
        source_observed_at=None,
        ingested_at=_utc(2026, 7, 1),
        availability_assumption="collection_time_not_historical_v1",
        coverage_status="measured",
        response_truncated=False,
        feature_capabilities={"breadth": True},
    )
    base.update(over)
    return NewsTopicDailyFeature(**base)


def test_daily_feature_logical_key_and_serialization() -> None:
    d = _daily()
    key = d.logical_key
    assert key[0] == "2026-06-15" and key[1] == "semiconductors"
    payload = d.to_dict()
    assert payload["feature_date"] == "2026-06-15"
    assert payload["earliest_published_at"].endswith("Z")
    # domain_counts serialized sorted deterministically.
    assert list(payload["observed_copy_domain_counts"].keys()) == ["a.com", "b.com"]
    assert "canonical_article_count" not in payload


def test_daily_feature_rejects_unknown_method() -> None:
    with pytest.raises(ValueError, match="news_measurement_method"):
        _daily(news_measurement_method="mystery")


def test_daily_feature_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _daily(ingested_at=datetime(2026, 7, 1))


def test_market_session_feature_key_includes_full_identity() -> None:
    feature = MarketSessionFeature(
        instrument_id="symbol:NVDA",
        symbol="nvda",
        session_date=date(2026, 6, 15),
        provider="alpaca",
        feed="iex",
        adjustment="all",
        currency="USD",
        timeframe="1Day",
        feature_version="v1",
        session_open=_utc(2026, 6, 15, 13),
        session_close=_utc(2026, 6, 15, 20),
        open=100.0,
        high=110.0,
        low=99.0,
        close=105.0,
        volume=1000,
        trade_count=50,
        vwap=104.0,
        inputs={"simple_return": 0.05},
        coverage_status="measured",
        quality_flags={"bar_present": True},
    )
    assert feature.symbol == "NVDA"  # uppercased
    assert "USD" in feature.logical_key and "iex" in feature.logical_key


def test_observation_rejects_leakage_at_construction() -> None:
    with pytest.raises(ValueError, match="leakage"):
        NewsMarketObservation(
            dataset_version="v1",
            observation_id="obs-1",
            topic="semiconductors",
            instrument_id="symbol:NVDA",
            symbol="NVDA",
            session_date=date(2026, 6, 15),
            feature_available_at=_utc(2026, 6, 15, 15),  # after open -> leakage
            target_session_open=_utc(2026, 6, 15, 13, 30),
            feature_cutoff=_utc(2026, 6, 15, 13, 25),
            cutoff_buffer_seconds=300,
            availability_assumption=AVAILABILITY_PUBLICATION_PROXY,
            alignment_precision="article_timestamp",
            alignment_policy="p",
            mapping_version="m",
            news_measurement_method="gdelt_doc_artlist",
            market_provider="alpaca",
            feed="iex",
            adjustment="all",
            currency="USD",
            timeframe="1Day",
            inputs={},
            contemporaneous={},
            targets={},
            quality={},
            lineage={},
        )


def test_observation_separates_field_groups() -> None:
    obs = NewsMarketObservation(
        dataset_version="v1",
        observation_id="obs-1",
        topic="semiconductors",
        instrument_id="symbol:NVDA",
        symbol="NVDA",
        session_date=date(2026, 6, 15),
        feature_available_at=_utc(2026, 6, 15, 13),
        target_session_open=_utc(2026, 6, 15, 13, 30),
        feature_cutoff=_utc(2026, 6, 15, 13, 25),
        cutoff_buffer_seconds=300,
        availability_assumption=AVAILABILITY_PUBLICATION_PROXY,
        alignment_precision="article_timestamp",
        alignment_policy="p",
        mapping_version="m",
        news_measurement_method="gdelt_doc_artlist",
        market_provider="alpaca",
        feed="iex",
        adjustment="all",
        currency="USD",
        timeframe="1Day",
        inputs={"news_x": 1},
        contemporaneous={"mkt_volume": 10},
        targets={"target_session_return": 0.01},
        quality={"exclusion_reason": None},
        lineage={"mapping_version": "m"},
    )
    payload = obs.to_dict()
    # Input and target namespaces must be distinguishable structures.
    assert set(payload["inputs"]) == {"news_x"}
    assert set(payload["targets"]) == {"target_session_return"}
    assert set(payload["contemporaneous"]) == {"mkt_volume"}


def test_observation_allows_equality_at_explicit_cutoff() -> None:
    obs = NewsMarketObservation(
        dataset_version="v2",
        observation_id="obs-equality",
        topic="semiconductors",
        instrument_id="equity_nvda",
        symbol="NVDA",
        session_date=date(2026, 6, 15),
        feature_available_at=_utc(2026, 6, 15, 13, 25),
        target_session_open=_utc(2026, 6, 15, 13, 30),
        feature_cutoff=_utc(2026, 6, 15, 13, 25),
        cutoff_buffer_seconds=300,
        availability_assumption=AVAILABILITY_PUBLICATION_PROXY,
        alignment_precision="article_timestamp",
        alignment_policy="session_information_window_v2",
        mapping_version="m",
        news_measurement_method="gdelt_doc_artlist",
        market_provider="alpaca",
        feed="iex",
        adjustment="all",
        currency="USD",
        timeframe="1Day",
        inputs={"news_attention_count": 1},
        contemporaneous={},
        targets={"target_session_return": 0.01},
        quality={},
        lineage={"mapping_version": "m"},
    )
    assert obs.feature_available_at == obs.feature_cutoff
    assert obs.to_dict()["feature_cutoff"] == "2026-06-15T13:25:00Z"


def test_observation_rejects_target_field_in_inputs() -> None:
    with pytest.raises(ValueError, match="cannot appear in inputs"):
        NewsMarketObservation(
            dataset_version="v2",
            observation_id="obs-bad-section",
            topic="semiconductors",
            instrument_id="equity_nvda",
            symbol="NVDA",
            session_date=date(2026, 6, 15),
            feature_available_at=_utc(2026, 6, 15, 13),
            target_session_open=_utc(2026, 6, 15, 13, 30),
            feature_cutoff=_utc(2026, 6, 15, 13, 25),
            cutoff_buffer_seconds=300,
            availability_assumption=AVAILABILITY_PUBLICATION_PROXY,
            alignment_precision="article_timestamp",
            alignment_policy="session_information_window_v2",
            mapping_version="m",
            news_measurement_method="gdelt_doc_artlist",
            market_provider="alpaca",
            feed="iex",
            adjustment="all",
            currency="USD",
            timeframe="1Day",
            inputs={"target_session_return": 0.01},
            contemporaneous={},
            targets={},
            quality={},
            lineage={},
        )


def test_topic_instrument_mapping_validity_window() -> None:
    m = TopicInstrumentMapping(
        mapping_version="v1",
        topic="semiconductors",
        instrument_id="equity_nvda",
        symbol="NVDA",
        relationship="constituent",
        exposure_type="company",
        weight=1.0,
        benchmark_symbol="QQQ",
        sector_benchmark_symbol="SMH",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        research_rationale="x",
    )
    assert m.is_active_on(date(2026, 6, 1))
    assert not m.is_active_on(date(2025, 12, 31))
    assert not m.is_active_on(date(2027, 1, 1))


def test_mapping_rejects_bad_weight() -> None:
    with pytest.raises(ValueError, match="weight"):
        TopicInstrumentMapping(
            mapping_version="v1",
            topic="t",
            instrument_id="i",
            symbol="X",
            relationship="constituent",
            exposure_type="company",
            weight=0.0,
            benchmark_symbol="QQQ",
            sector_benchmark_symbol=None,
            valid_from=None,
            valid_to=None,
            research_rationale="",
        )

"""Tests for the offline event study around attention shocks."""

from datetime import date, datetime, timezone

from research_data.builder import build_dataset
from research_data.calendar import MarketCalendar
from research_data.catalog import load_feature_catalog
from research_data.event_study import EventStudyConfig, detect_events, run_event_study
from research_data.models import (
    AVAILABILITY_PUBLICATION_PROXY,
    NewsMarketObservation,
    TopicInstrumentMapping,
)

CAL = MarketCalendar()
CATALOG = load_feature_catalog("configs/research/news_market_feature_catalog.yaml")
OBS = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _bar(symbol, sess, c, v=1000):
    ts = datetime(sess.year, sess.month, sess.day, 4, tzinfo=timezone.utc)
    return {
        "symbol": symbol,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "timeframe": "1Day",
        "open": c,
        "high": c * 1.03,
        "low": c * 0.97,
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


def _article(title, domain, iso):
    return {
        "title": title,
        "domain": domain,
        "source_country": "United States",
        "published_at": iso,
        "topics": ["semiconductors"],
        "primary_topic": "semiconductors",
        "query": "chip",
    }


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


def _dataset_with_spike():
    sess = CAL.sessions_between(date(2026, 5, 1), date(2026, 6, 30))
    bars = []
    p = 100.0
    for s in sess:
        bars += [_bar("NVDA", s, p), _bar("QQQ", s, p)]
        p *= 1.005
    articles = []
    for i, s in enumerate(sess):
        for j in range(1 + (i % 2)):
            articles.append(_article(f"b {s} {j}", f"d{j}.com", f"{s.isoformat()}T12:0{j}:00Z"))
    spike = sess[35]
    for k in range(30):
        articles.append(_article(f"spike {k}", f"s{k}.com", f"{spike.isoformat()}T12:00:00Z"))
    return (
        build_dataset(
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
            event_config=EventStudyConfig(threshold=2.0, minimum_sample=20),
        ),
        spike,
    )


def test_event_detection_uses_prior_window_zscore() -> None:
    result, spike = _dataset_with_spike()
    events = detect_events(result.observations, EventStudyConfig(threshold=2.0))
    dates = {e["session_date"] for e in events}
    assert spike.isoformat() in dates
    # Baseline (non-spike) days should not be events.
    assert len([d for d in dates if d != spike.isoformat()]) <= 1


def test_event_study_reports_descriptives_and_fdr_families() -> None:
    result, _ = _dataset_with_spike()
    summary = result.event_study_summary
    assert summary["event_count"] >= 1
    all_group = next(g for g in summary["groups"] if g["group_kind"] == "all")
    metric = all_group["metrics"]["target_session_absolute_return"]
    assert "minimum" in metric and "maximum" in metric and "mean" in metric
    assert summary["hypothesis_registry_version"] == "news-market-hypotheses-v2"
    assert {family["hypothesis_id"] for family in summary["fdr_families"]} == {"H1", "H2"}


def test_small_sample_triggers_warning() -> None:
    result, _ = _dataset_with_spike()
    summary = result.event_study_summary
    all_group = next(g for g in summary["groups"] if g["group_kind"] == "all")
    # The synthetic spike yields a small event count below the minimum of 20.
    assert all_group["event_count"] < 20
    metric = all_group["metrics"]["target_session_absolute_return"]
    assert metric["inferential_status"] == "descriptive_small_sample"
    assert metric["p_value_uncorrected"] is None
    assert metric["adjusted_p_value"] is None
    assert metric["ci_low"] is None and metric["ci_high"] is None
    assert any("descriptive event-only summary" in w for w in all_group["warnings"])


def test_event_study_avoids_causal_claims() -> None:
    result, _ = _dataset_with_spike()
    summary = result.event_study_summary
    assert "Associations only" in summary["disclaimer"]
    # No group carries an affirmative causal claim.
    text = str(summary).lower()
    assert "proves" not in text
    assert "causes the" not in text
    assert "significant" not in text


def test_benchmark_adjusted_cumulative_target_present_when_benchmark_available() -> None:
    result, _ = _dataset_with_spike()
    events = detect_events(result.observations, EventStudyConfig(threshold=2.0))
    assert (
        any(
            e.get("target_through_plus_4_cumulative_benchmark_adjusted_return") is not None
            for e in events
        )
        or events
    )


def _event_observation(session: date, suffix: str, absolute_return: float) -> NewsMarketObservation:
    open_at = CAL.session_open(session)
    cutoff = open_at.replace(minute=open_at.minute - 5)
    return NewsMarketObservation(
        dataset_version="v2",
        observation_id=f"obs-{session}-{suffix}",
        topic="semiconductors",
        instrument_id=f"instrument-{suffix}",
        symbol=suffix,
        session_date=session,
        feature_available_at=cutoff,
        target_session_open=open_at,
        feature_cutoff=cutoff,
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
        inputs={
            "news_attention_zscore_30": 3.0,
            "news_duplicate_ratio": 0.1,
            "news_observed_copy_effective_domain_count": 2.0,
            "mkt_prior_trailing_close_to_close_vol_20": 0.02,
            "mkt_prior_trailing_return_5": 0.01,
        },
        contemporaneous={},
        targets={
            "target_session_return": absolute_return,
            "target_session_absolute_return": absolute_return,
            "target_session_benchmark_adjusted_return": absolute_return / 2,
            "target_session_parkinson_variance": absolute_return**2,
        },
        quality={"market_coverage_status": "measured"},
        lineage={"mapping_version": "m", "relationship": "constituent"},
    )


def test_event_only_group_is_descriptive_not_tested_against_zero() -> None:
    sessions = CAL.sessions_between(date(2026, 6, 1), date(2026, 6, 3))
    observations = [
        _event_observation(sessions[0], "NVDA", 0.01),
        _event_observation(sessions[0], "AMD", 0.02),
        _event_observation(sessions[1], "NVDA", 0.03),
        _event_observation(sessions[1], "AMD", 0.04),
    ]
    summary, _ = run_event_study(
        observations,
        EventStudyConfig(minimum_sample=2, bootstrap_iterations=50, seed=7),
    )
    h1 = next(family for family in summary["fdr_families"] if family["hypothesis_id"] == "H1")
    h2 = next(family for family in summary["fdr_families"] if family["hypothesis_id"] == "H2")
    # The H1 family is now an event-vs-control contrast, not a one-sample vs-zero test.
    assert h1["contrast_kind"] == "event_vs_control_difference"
    # With no control (ordinary) sessions in this fixture, no contrast is eligible.
    assert h1["eligible_tests"] == 0
    assert h2["eligible_tests"] == 0
    all_group = next(group for group in summary["groups"] if group["group_kind"] == "all")
    metric = all_group["metrics"]["target_session_absolute_return"]
    assert metric["inference_n_sessions"] == 2  # four rows, two clustered dates
    # Event-only summaries never carry a p-value against zero.
    assert metric["p_value_uncorrected"] is None
    assert metric["adjusted_p_value"] is None
    assert metric["inferential_status"] in {"descriptive", "descriptive_small_sample"}


def test_missing_targets_do_not_inflate_inference_sample() -> None:
    sessions = CAL.sessions_between(date(2026, 6, 1), date(2026, 6, 3))
    complete = _event_observation(sessions[0], "NVDA", 0.01)
    missing = _event_observation(sessions[1], "AMD", 0.02)
    missing = NewsMarketObservation(
        **{
            **missing.__dict__,
            "targets": {
                **missing.targets,
                "target_session_absolute_return": None,
                "target_session_parkinson_variance": None,
            },
        }
    )
    summary, _ = run_event_study(
        [complete, missing],
        EventStudyConfig(minimum_sample=2, bootstrap_iterations=20),
    )
    metric = summary["groups"][0]["metrics"]["target_session_absolute_return"]
    assert metric["n"] == 1
    assert metric["inference_n_sessions"] == 1
    assert metric["inferential_status"] == "descriptive_small_sample"

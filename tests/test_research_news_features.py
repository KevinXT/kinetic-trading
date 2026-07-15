"""Tests for news feature building across DOC and BigQuery measurement paths."""

from datetime import datetime, timezone

import pytest
from research_data.alignment import (
    ConservativeCalendarDayPolicy,
    SessionInformationWindowPolicy,
)
from research_data.calendar import MarketCalendar
from research_data.news_features import build_news_features

CAL = MarketCalendar()
POLICY = SessionInformationWindowPolicy(CAL)
OBSERVED = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _article(
    title, domain, day_hour, topics=None, country="United States", primary="semiconductors"
):
    return {
        "provider": "gdelt",
        "query": "chip",
        "title": title,
        "domain": domain,
        "source_country": country,
        "published_at": day_hour,
        "topics": topics or ["semiconductors"],
        "primary_topic": primary,
    }


def _doc(articles, **kw):
    return build_news_features(
        method="gdelt_doc_artlist",
        policy=POLICY,
        calendar=CAL,
        provider="gdelt",
        feature_version="v1",
        topic_taxonomy_version="t1",
        observed_at=OBSERVED,
        articles=articles,
        **kw,
    )


def _session(sessions, session_date, topic="semiconductors"):
    return next(
        s for s in sessions if s.session_date.isoformat() == session_date and s.topic == topic
    )


def test_title_deduplicated_vs_observed_with_duplicates() -> None:
    # Three articles pre-open Monday; two share a title (syndication).
    articles = [
        _article("Chip demand rises", "a.com", "2026-06-15T12:00:00Z"),
        _article("Chip demand rises", "b.com", "2026-06-15T12:05:00Z"),
        _article("New fab announced", "c.com", "2026-06-15T12:10:00Z"),
    ]
    _, sessions = _doc(articles)
    s = _session(sessions, "2026-06-15")
    assert s.inputs["observed_article_count"] == 3
    assert s.inputs["title_deduplicated_article_count"] == 2
    assert s.inputs["attention_count"] == 2
    assert s.inputs["observed_copy_count"] == 1
    assert abs(s.inputs["duplicate_ratio"] - (1 / 3)) < 1e-9
    assert s.inputs["unique_domains"] == 3


def test_multi_topic_article_fans_out() -> None:
    articles = [
        _article(
            "AI chip roadmap", "a.com", "2026-06-15T12:00:00Z", topics=["semiconductors", "ai"]
        )
    ]
    _, sessions = _doc(articles)
    topics = {s.topic for s in sessions}
    assert topics == {"semiconductors", "ai"}
    for s in sessions:
        assert s.inputs["multi_topic_share"] == 1.0


def test_weekend_articles_aggregate_into_monday_with_source_dates() -> None:
    articles = [
        _article("Sat news", "a.com", "2026-06-13T16:00:00Z"),
        _article("Sun news", "b.com", "2026-06-14T16:00:00Z"),
        _article("Mon premarket", "c.com", "2026-06-15T12:00:00Z"),
    ]
    _, sessions = _doc(articles)
    s = _session(sessions, "2026-06-15")
    assert s.source_feature_dates == ("2026-06-13", "2026-06-14", "2026-06-15")
    assert s.source_date_count == 3
    assert s.inputs["title_deduplicated_article_count"] == 3


def test_publication_span_hours() -> None:
    articles = [
        _article("a", "a.com", "2026-06-15T12:00:00Z"),
        _article("b", "b.com", "2026-06-15T13:20:00Z"),
    ]
    _, sessions = _doc(articles)
    s = _session(sessions, "2026-06-15")
    assert abs(s.inputs["publication_span_hours"] - (4 / 3)) < 1e-9


def test_truncation_flag_when_records_hit_cap() -> None:
    articles = [_article(f"t{i}", f"d{i}.com", "2026-06-15T12:00:00Z") for i in range(5)]
    _, sessions = _doc(articles, max_records=5)
    s = _session(sessions, "2026-06-15")
    assert s.quality_flags["response_truncated"] is True
    assert s.coverage_status == "provider_truncated"


def test_lag_zscore_excludes_current_observation() -> None:
    # Flat prior counts then a spike; z-score must be computed from prior only.
    articles = []
    sessions_list = CAL.sessions_between(
        __import__("datetime").date(2026, 5, 1), __import__("datetime").date(2026, 6, 30)
    )
    for i, sess in enumerate(sessions_list[:12]):
        n = 1 + (i % 2)
        for j in range(n):
            articles.append(
                _article(f"base {sess} {j}", f"d{j}.com", f"{sess.isoformat()}T12:0{j}:00Z")
            )
    spike_day = sessions_list[12]
    for k in range(20):
        articles.append(_article(f"spike {k}", f"s{k}.com", f"{spike_day.isoformat()}T12:00:00Z"))
    _, sessions = _doc(articles)
    spike = _session(sessions, spike_day.isoformat())
    z = spike.inputs["attention_zscore_7"]
    assert z is not None and z > 3  # large positive shock vs prior baseline


def test_bigquery_path_marks_unsupported_fields_null() -> None:
    rows = [
        {"topic": "semiconductors", "date": "2026-06-12", "article_count": 5, "query_terms": ["x"]},
        {"topic": "semiconductors", "date": "2026-06-15", "article_count": 0, "query_terms": ["x"]},
    ]
    daily, sessions = build_news_features(
        method="bigquery_gdelt_counts",
        policy=ConservativeCalendarDayPolicy(CAL),
        calendar=CAL,
        provider="bigquery_gdelt",
        feature_version="v1",
        topic_taxonomy_version="t1",
        observed_at=OBSERVED,
        count_rows=rows,
    )
    # Unsupported richer fields must be null, not zero.
    for s in sessions:
        assert s.inputs["unique_domains"] is None
        assert s.inputs["duplicate_ratio"] is None
        assert s.inputs["observed_copy_domain_hhi"] is None
        assert s.feature_capabilities["breadth"] is False
    assert all(s.inputs["title_deduplicated_article_count"] is None for s in sessions)
    assert any(s.inputs["attention_count"] == 5 for s in sessions)


def test_bigquery_measured_zero_distinct_from_unavailable() -> None:
    rows = [
        {"topic": "semiconductors", "date": "2026-06-12", "article_count": 0, "query_terms": ["x"]},
    ]
    daily, _ = build_news_features(
        method="bigquery_gdelt_counts",
        policy=ConservativeCalendarDayPolicy(CAL),
        calendar=CAL,
        provider="bigquery_gdelt",
        feature_version="v1",
        topic_taxonomy_version="t1",
        observed_at=OBSERVED,
        count_rows=rows,
    )
    row = daily[0]
    assert row.observed_article_count == 0
    assert row.title_deduplicated_article_count is None
    assert row.coverage_status == "measured_zero"  # measured, not missing
    assert row.unique_domains is None  # genuinely unmeasurable -> null


def test_bigquery_rejects_exact_timestamp_window() -> None:
    with pytest.raises(ValueError, match="cannot use session_information_window_v2"):
        build_news_features(
            method="bigquery_gdelt_counts",
            policy=POLICY,
            calendar=CAL,
            provider="bigquery_gdelt",
            feature_version="v2",
            topic_taxonomy_version="t1",
            observed_at=OBSERVED,
            count_rows=[
                {
                    "topic": "semiconductors",
                    "date": "2026-06-12",
                    "article_count": 1,
                }
            ],
        )


def test_doc_supports_conservative_daily_alignment() -> None:
    _, sessions = build_news_features(
        method="gdelt_doc_artlist",
        policy=ConservativeCalendarDayPolicy(CAL),
        calendar=CAL,
        provider="gdelt",
        feature_version="v2",
        topic_taxonomy_version="t1",
        observed_at=OBSERVED,
        articles=[_article("chip", "a.com", "2026-06-15T12:00:00Z")],
    )
    assert sessions[0].alignment_precision == "daily_approximation"
    assert sessions[0].availability_assumption == "publication_timestamp_proxy_v1"


def test_observed_copy_concentration_includes_syndicated_copies() -> None:
    articles = [
        _article("same title", "dominant.com", "2026-06-15T12:00:00Z"),
        _article("same title", "dominant.com", "2026-06-15T12:01:00Z"),
        _article("independent", "other.com", "2026-06-15T12:02:00Z"),
    ]
    _, sessions = _doc(articles)
    values = _session(sessions, "2026-06-15").inputs
    assert values["title_deduplicated_article_count"] == 2
    # Observed-copy shares are 2/3 and 1/3: HHI=5/9, not title-level 1/2.
    assert abs(values["observed_copy_domain_hhi"] - (5 / 9)) < 1e-9
    assert "domain_hhi" not in values


@pytest.mark.parametrize(
    ("prior_count", "field"),
    [
        (6, "attention_zscore_7"),
        (29, "attention_zscore_30"),
    ],
)
def test_attention_zscore_requires_full_valid_history(prior_count: int, field: str) -> None:
    sessions_list = CAL.sessions_between(
        __import__("datetime").date(2026, 1, 2),
        __import__("datetime").date(2026, 4, 30),
    )
    articles = []
    for index, session in enumerate(sessions_list[: prior_count + 1]):
        count = 1 + (index % 2)
        for copy in range(count):
            articles.append(
                _article(
                    f"{session}-{copy}",
                    f"d{copy}.com",
                    f"{session.isoformat()}T12:00:00Z",
                )
            )
    _, features = _doc(articles)
    current = _session(features, sessions_list[prior_count].isoformat())
    assert current.inputs[field] is None


def test_zero_dispersion_and_truncated_history_produce_null_zscores() -> None:
    session_dates = CAL.sessions_between(
        __import__("datetime").date(2026, 5, 1),
        __import__("datetime").date(2026, 6, 30),
    )[:31]
    articles = [
        _article(f"{session}", "a.com", f"{session.isoformat()}T12:00:00Z")
        for session in session_dates
    ]
    _, flat = _doc(articles)
    assert _session(flat, session_dates[-1].isoformat()).inputs["attention_zscore_30"] is None
    _, truncated = _doc(articles, max_records=len(articles))
    final = _session(truncated, session_dates[-1].isoformat())
    assert final.inputs["attention_zscore_30"] is None
    assert final.quality_flags["attention_baseline_eligible"] is False


def test_bigquery_measured_zero_is_valid_attention_baseline_value() -> None:
    session_dates = CAL.sessions_between(
        __import__("datetime").date(2026, 1, 2),
        __import__("datetime").date(2026, 3, 31),
    )[:31]
    rows = [
        {
            "topic": "semiconductors",
            # Daily D maps to the next session, but ordering is preserved.
            "date": session.isoformat(),
            "article_count": index % 3,
        }
        for index, session in enumerate(session_dates)
    ]
    _, features = build_news_features(
        method="bigquery_gdelt_counts",
        policy=ConservativeCalendarDayPolicy(CAL),
        calendar=CAL,
        provider="bigquery_gdelt",
        feature_version="v2",
        topic_taxonomy_version="t1",
        observed_at=OBSERVED,
        count_rows=rows,
    )
    final = sorted(features, key=lambda feature: feature.session_date)[-1]
    assert final.inputs["attention_history_len"] == 30
    assert final.inputs["attention_zscore_30"] is not None

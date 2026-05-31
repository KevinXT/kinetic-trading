"""Tests for the BigQuery GDELT daily-counts normalizer."""

from datetime import date

from news_data.bigquery.normalize_counts import normalize_gdelt_daily_counts

TERMS = ["inflation", "Federal Reserve"]


def test_empty_rows_returns_empty() -> None:
    assert normalize_gdelt_daily_counts([], "inflation_rates", TERMS) == []


def test_normalizes_integer_date_format() -> None:
    rows = [{"date": 20260503, "article_count": 102}]
    out = normalize_gdelt_daily_counts(rows, "inflation_rates", TERMS)
    assert out[0]["date"] == "2026-05-03"


def test_normalizes_date_object() -> None:
    rows = [{"date": date(2026, 5, 3), "article_count": 7}]
    out = normalize_gdelt_daily_counts(rows, "inflation_rates", TERMS)
    assert out[0]["date"] == "2026-05-03"


def test_passthrough_iso_date() -> None:
    rows = [{"date": "2026-05-03", "article_count": 4}]
    out = normalize_gdelt_daily_counts(rows, "inflation_rates", TERMS)
    assert out[0]["date"] == "2026-05-03"


def test_article_count_coerced_to_int() -> None:
    rows = [{"date": "2026-05-03", "article_count": "102"}]
    out = normalize_gdelt_daily_counts(rows, "inflation_rates", TERMS)
    assert out[0]["article_count"] == 102
    assert isinstance(out[0]["article_count"], int)


def test_preserves_topic_and_terms_and_nulls() -> None:
    rows = [{"date": "2026-05-03", "article_count": 1}]
    out = normalize_gdelt_daily_counts(rows, "inflation_rates", TERMS)
    row = out[0]
    assert row["topic"] == "inflation_rates"
    assert row["provider"] == "bigquery_gdelt"
    assert row["mode"] == "daily_counts"
    assert row["query_terms"] == TERMS
    assert row["source_count"] is None
    assert row["avg_sentiment"] is None
    assert row["coverage_share"] is None
    assert "ingested_at" in row


def test_skips_non_dict_rows() -> None:
    rows = [{"date": "2026-05-03", "article_count": 1}, "garbage", None]
    out = normalize_gdelt_daily_counts(rows, "inflation_rates", TERMS)
    assert len(out) == 1

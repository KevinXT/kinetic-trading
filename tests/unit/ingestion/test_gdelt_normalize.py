"""Tests for kinetic.ingestion.news.gdelt.normalize — GDELT article normalization layer."""

from kinetic.ingestion.news.gdelt.normalize import (
    _clean_string,
    _parse_gdelt_seen_date,
    normalize_article,
    normalize_articles,
)

# ── sample fixtures ───────────────────────────────────────────────────────


FULL_RAW_ARTICLE = {
    "title": "  AI Drives Market Rally  ",
    "url": "https://example.com/article",
    "url_mobile": "https://m.example.com/article",
    "domain": "Example.com",
    "language": "English",
    "sourcecountry": "United States",
    "socialimage": "https://example.com/img.jpg",
    "seendate": "20260508T143000Z",
}

MINIMAL_RAW_ARTICLE = {
    "title": "Minimal Article",
    "url": "https://example.com/minimal",
}


# ── _clean_string ─────────────────────────────────────────────────────────


def test_clean_string_strips_whitespace() -> None:
    assert _clean_string("  hello  ") == "hello"


def test_clean_string_returns_empty_for_none() -> None:
    assert _clean_string(None) == ""


def test_clean_string_coerces_non_string() -> None:
    assert _clean_string(42) == "42"


def test_clean_string_returns_empty_for_empty() -> None:
    assert _clean_string("") == ""


# ── _parse_gdelt_seen_date ────────────────────────────────────────────────


def test_parse_valid_gdelt_date() -> None:
    assert _parse_gdelt_seen_date("20260508T143000Z") == "2026-05-08T14:30:00Z"


def test_parse_midnight_date() -> None:
    assert _parse_gdelt_seen_date("20260101T000000Z") == "2026-01-01T00:00:00Z"


def test_parse_returns_none_for_empty() -> None:
    assert _parse_gdelt_seen_date("") is None
    assert _parse_gdelt_seen_date(None) is None


def test_parse_returns_none_for_invalid_format() -> None:
    assert _parse_gdelt_seen_date("May 8 2026") is None
    assert _parse_gdelt_seen_date("not-a-date") is None


def test_parse_returns_none_for_whitespace_only() -> None:
    assert _parse_gdelt_seen_date("   ") is None


# ── normalize_article: full record ────────────────────────────────────────


def test_full_record_normalizes_correctly() -> None:
    result = normalize_article(FULL_RAW_ARTICLE, query="artificial intelligence")

    assert result["provider"] == "gdelt"
    assert result["query"] == "artificial intelligence"
    assert result["title"] == "AI Drives Market Rally"
    assert result["url"] == "https://example.com/article"
    assert result["mobile_url"] == "https://m.example.com/article"
    assert result["domain"] == "example.com"
    assert result["language"] == "English"
    assert result["source_country"] == "United States"
    assert result["image_url"] == "https://example.com/img.jpg"
    assert result["published_at"] == "2026-05-08T14:30:00Z"
    assert result["raw_seen_date"] == "20260508T143000Z"


# ── field renaming ────────────────────────────────────────────────────────


def test_field_renaming_from_gdelt_schema() -> None:
    result = normalize_article(FULL_RAW_ARTICLE)

    assert "socialimage" not in result
    assert "image_url" in result
    assert "sourcecountry" not in result
    assert "source_country" in result
    assert "url_mobile" not in result
    assert "mobile_url" in result
    assert "seendate" not in result
    assert "raw_seen_date" in result


# ── missing optional fields ───────────────────────────────────────────────


def test_missing_optional_fields_default_to_empty_or_none() -> None:
    result = normalize_article(MINIMAL_RAW_ARTICLE)

    assert result["title"] == "Minimal Article"
    assert result["url"] == "https://example.com/minimal"
    assert result["mobile_url"] == ""
    assert result["domain"] == ""
    assert result["language"] == ""
    assert result["source_country"] == ""
    assert result["image_url"] == ""
    assert result["published_at"] is None
    assert result["raw_seen_date"] == ""


# ── provider metadata ────────────────────────────────────────────────────


def test_provider_always_set_to_gdelt() -> None:
    assert normalize_article({})["provider"] == "gdelt"


def test_query_preserved_in_output() -> None:
    assert normalize_article({}, query="  market crash ")["query"] == "market crash"


def test_query_defaults_to_empty() -> None:
    assert normalize_article({})["query"] == ""


# ── domain lowercased ─────────────────────────────────────────────────────


def test_domain_lowercased() -> None:
    article = {"domain": "  CNN.Com  "}
    assert normalize_article(article)["domain"] == "cnn.com"


# ── empty strings normalized consistently ─────────────────────────────────


def test_empty_strings_consistent_across_text_fields() -> None:
    result = normalize_article({})
    text_fields = [
        "title",
        "url",
        "mobile_url",
        "domain",
        "language",
        "source_country",
        "image_url",
        "raw_seen_date",
    ]
    for field in text_fields:
        assert result[field] == "", f"{field} should be empty string, got {result[field]!r}"


# ── timestamp normalization ───────────────────────────────────────────────


def test_published_at_is_iso8601_utc() -> None:
    article = {"seendate": "20260315T093045Z"}
    result = normalize_article(article)
    assert result["published_at"] == "2026-03-15T09:30:45Z"
    assert result["published_at"].endswith("Z")


def test_invalid_seendate_produces_none_published_at() -> None:
    article = {"seendate": "garbage-value"}
    result = normalize_article(article)
    assert result["published_at"] is None
    assert result["raw_seen_date"] == "garbage-value"


# ── ingested_at preservation ─────────────────────────────────────────────


def test_fresh_article_without_ingested_at_generates_timestamp() -> None:
    result = normalize_article({"title": "Fresh"})
    assert result["ingested_at"]
    assert result["ingested_at"].endswith("Z")


def test_existing_ingested_at_preserved_exactly() -> None:
    original_ts = "2026-01-15T09:00:00Z"
    article = {"title": "Cached", "ingested_at": original_ts}
    result = normalize_article(article)
    assert result["ingested_at"] == original_ts


def test_empty_string_ingested_at_generates_new_timestamp() -> None:
    article = {"title": "Empty IA", "ingested_at": ""}
    result = normalize_article(article)
    assert result["ingested_at"]
    assert result["ingested_at"] != ""
    assert result["ingested_at"].endswith("Z")


def test_none_ingested_at_generates_new_timestamp() -> None:
    article = {"title": "None IA", "ingested_at": None}
    result = normalize_article(article)
    assert result["ingested_at"]
    assert result["ingested_at"].endswith("Z")


# ── normalize_articles: batch behavior ────────────────────────────────────


def test_normalize_articles_processes_multiple_records() -> None:
    raw = [FULL_RAW_ARTICLE, MINIMAL_RAW_ARTICLE]
    results = normalize_articles(raw, query="batch test")

    assert len(results) == 2
    assert results[0]["title"] == "AI Drives Market Rally"
    assert results[1]["title"] == "Minimal Article"
    assert all(r["query"] == "batch test" for r in results)


def test_normalize_articles_skips_non_dict_items() -> None:
    raw = [FULL_RAW_ARTICLE, "not a dict", 42, None, MINIMAL_RAW_ARTICLE]
    results = normalize_articles(raw)

    assert len(results) == 2


def test_normalize_articles_empty_input_returns_empty_list() -> None:
    assert normalize_articles([]) == []

"""Tests for the GDELT theme-discovery SQL builder."""

import pytest

from kinetic.ingestion.news.gdelt.bigquery.queries import (
    DEFAULT_THEME_SEARCH_PATTERNS,
    build_gdelt_theme_discovery_query,
)
from kinetic.ingestion.warehouse.bigquery.sql_guardrails import validate_bigquery_sql

TABLE = "gdelt-bq.gdeltv2.gkg_partitioned"
_PARTITION = {"enabled": True, "column": "_PARTITIONTIME", "type": "timestamp"}


def _build(**kw):
    params = dict(
        table=TABLE,
        start_date="2026-05-01",
        end_date="2026-05-30",
        topic="inflation_rates",
        partition_filter=_PARTITION,
    )
    params.update(kw)
    return build_gdelt_theme_discovery_query(**params)


def test_includes_partition_lower_and_upper_bounds() -> None:
    sql = _build()
    assert '_PARTITIONTIME >= TIMESTAMP("2026-05-01")' in sql
    # Exclusive upper bound = requested end (05-30) + 1 day.
    assert '_PARTITIONTIME < TIMESTAMP("2026-05-31")' in sql


def test_includes_date_between() -> None:
    # GKG DATE is a 14-digit YYYYMMDDHHMMSS int, not 8-digit YYYYMMDD.
    sql = _build()
    assert "DATE BETWEEN 20260501000000 AND 20260530235959" in sql
    assert "DATE BETWEEN 20260501 AND 20260530" not in sql


def test_uses_gdelt_normalization() -> None:
    assert "REGEXP_REPLACE(V2Themes, r',\\d+;', ';')" in _build()


def test_uses_unnest_themes() -> None:
    sql = _build()
    assert "UNNEST(themes) AS theme" in sql


def test_includes_length_guard() -> None:
    assert "LENGTH(V2Themes) > 1" in _build()


def test_no_select_star() -> None:
    assert "SELECT *" not in _build()


def test_limit_is_included() -> None:
    assert "LIMIT 100" in _build()


def test_custom_limit() -> None:
    assert "LIMIT 25" in _build(limit=25)


def test_default_patterns_present() -> None:
    sql = _build()
    for pattern in DEFAULT_THEME_SEARCH_PATTERNS:
        assert f"LOWER(theme) LIKE '%{pattern}%'" in sql


def test_configurable_patterns() -> None:
    sql = _build(search_patterns=["fiscal", "tariff"])
    assert "LOWER(theme) LIKE '%fiscal%'" in sql
    assert "LOWER(theme) LIKE '%tariff%'" in sql
    assert "%inflation%" not in sql


def test_patterns_are_sanitized() -> None:
    # Uppercasing is normalized to lowercase; quotes are escaped so a pattern
    # can't break out of the literal.
    sql = _build(search_patterns=["INFLATION", "a'b"])
    assert "LOWER(theme) LIKE '%inflation%'" in sql
    assert "LOWER(theme) LIKE '%a''b%'" in sql


def test_empty_patterns_rejected() -> None:
    with pytest.raises(ValueError, match="search_patterns"):
        _build(search_patterns=["", "   "])


def test_invalid_date_range_rejected() -> None:
    with pytest.raises(ValueError, match="date range"):
        _build(start_date="2026-05-30", end_date="2026-05-01")


def test_non_positive_limit_rejected() -> None:
    with pytest.raises(ValueError, match="limit"):
        _build(limit=0)


def test_passes_guardrails() -> None:
    # Semicolons inside the raw regex literal must not trip the multi-statement
    # guardrail, and the query must otherwise satisfy all guardrails.
    validate_bigquery_sql(_build(), [TABLE])


def test_guardrails_accept_without_partition_filter() -> None:
    # DATE BETWEEN alone still satisfies the date-filter guardrail.
    validate_bigquery_sql(_build(partition_filter=None), [TABLE])


# ── matching modes ────────────────────────────────────────────────────────────


def test_default_mode_is_substring_backward_compatible() -> None:
    # No match_mode given -> the historical LIKE '%pattern%' behavior, unchanged.
    sql = _build(search_patterns=["foundry"])
    assert "LOWER(theme) LIKE '%foundry%'" in sql
    assert "-- match_mode: substring" in sql


def test_token_mode_rejects_foundryman_for_foundry() -> None:
    # foundry as a whole `_`-delimited token must NOT match FOUNDRYMAN.
    sql = _build(search_patterns=["foundry"], match_mode="token")
    assert "REGEXP_CONTAINS(LOWER(theme), r'(^|_)foundry(_|$)')" in sql
    assert "LIKE '%foundry%'" not in sql
    assert "-- match_mode: token" in sql


def test_exact_mode_matches_whole_code() -> None:
    sql = _build(search_patterns=["econ_inflation"], match_mode="exact")
    assert "LOWER(theme) = 'econ_inflation'" in sql
    assert "-- match_mode: exact" in sql


def test_token_mode_pattern_is_delimiter_safe_no_regex_injection() -> None:
    # A pattern with regex metacharacters cannot reach the generated SQL.
    with pytest.raises(ValueError, match="delimiter-safe"):
        _build(search_patterns=["foundry.*"], match_mode="token")


def test_unknown_match_mode_rejected() -> None:
    with pytest.raises(ValueError, match="match_mode"):
        _build(search_patterns=["semiconductor"], match_mode="fuzzy")


def test_token_mode_passes_guardrails() -> None:
    validate_bigquery_sql(
        _build(search_patterns=["semiconductor", "integrated_circuit"], match_mode="token"),
        [TABLE],
    )

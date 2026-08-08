"""Tests for the GDELT daily-counts SQL builder."""

import pytest

from kinetic.ingestion.news.gdelt.bigquery.queries import build_gdelt_daily_counts_query

TABLE = "gdelt-bq.gdeltv2.gkg_partitioned"
TERMS = ["Federal Reserve", "inflation", "interest rates"]


def _build(**kw):
    params = dict(
        table=TABLE,
        start_date="2026-05-01",
        end_date="2026-05-30",
        query_terms=TERMS,
        topic="inflation_rates",
    )
    params.update(kw)
    return build_gdelt_daily_counts_query(**params)


def test_contains_date_filter() -> None:
    # GKG DATE is a 14-digit YYYYMMDDHHMMSS int, not 8-digit YYYYMMDD.
    sql = _build()
    assert "DATE BETWEEN 20260501000000 AND 20260530235959" in sql
    assert "DATE BETWEEN 20260501 AND 20260530" not in sql


def test_does_not_contain_select_star() -> None:
    assert "SELECT *" not in _build()


def test_includes_terms() -> None:
    # Free-text columns (e.g. AllNames) use case-insensitive substring LIKE.
    sql = _build(search_columns=["AllNames"])
    assert "%federal reserve%" in sql
    assert "%inflation%" in sql
    assert "%interest rates%" in sql


def test_groups_and_orders_by_date() -> None:
    sql = _build()
    assert "GROUP BY date" in sql
    assert "ORDER BY date" in sql


def test_uses_configured_table() -> None:
    assert f"`{TABLE}`" in _build()


def test_accepts_yyyymmdd_inputs() -> None:
    sql = _build(start_date="20260101", end_date="20260131")
    assert "DATE BETWEEN 20260101000000 AND 20260131235959" in sql


def test_empty_terms_rejected() -> None:
    with pytest.raises(ValueError, match="query_terms"):
        _build(query_terms=[])


def test_blank_terms_rejected() -> None:
    with pytest.raises(ValueError, match="query_terms"):
        _build(query_terms=["  ", ""])


def test_invalid_date_range_rejected() -> None:
    with pytest.raises(ValueError, match="date range"):
        _build(start_date="2026-05-30", end_date="2026-05-01")


def test_invalid_date_format_rejected() -> None:
    with pytest.raises(ValueError, match="invalid date"):
        _build(start_date="May 1", end_date="2026-05-30")


def test_custom_search_columns() -> None:
    sql = _build(search_columns=["AllNames"])
    assert "LOWER(AllNames) LIKE" in sql
    assert "LOWER(V2Themes)" not in sql


def test_default_searches_only_v2themes() -> None:
    # Default keeps scan cost low: only the compact V2Themes column is scanned.
    sql = _build()
    assert "REGEXP_REPLACE(V2Themes, r',\\d+;', ';')" in sql
    # Broad free-text columns must not be scanned unless explicitly configured.
    assert "AllNames" not in sql


def test_does_not_include_allnames_unless_configured() -> None:
    # No search_columns given -> AllNames absent.
    assert "AllNames" not in _build()
    assert "AllNames" not in _build(search_columns=["V2Themes"])
    # Explicitly configured -> AllNames present.
    assert "AllNames" in _build(search_columns=["V2Themes", "AllNames"])


def _references(sql: str, column: str) -> bool:
    # A column is "scanned" if it appears in either match form (free-text LOWER
    # LIKE, or normalized theme-code UNNEST via REGEXP_REPLACE).
    return f"LOWER({column})" in sql or f"REGEXP_REPLACE({column}," in sql


def test_sql_only_references_configured_columns() -> None:
    known_text_columns = ["V2Themes", "AllNames", "V2Persons", "V2Organizations", "Themes"]
    sql = _build(search_columns=["V2Themes"])
    referenced = [c for c in known_text_columns if _references(sql, c)]
    assert referenced == ["V2Themes"]

    sql_multi = _build(search_columns=["V2Themes", "V2Organizations"])
    referenced_multi = {c for c in known_text_columns if _references(sql_multi, c)}
    assert referenced_multi == {"V2Themes", "V2Organizations"}


def test_theme_code_term_uses_normalized_unnest() -> None:
    sql = _build(query_terms=["ECON_INFLATION"], search_columns=["V2Themes"])
    # GDELT-recommended normalized UNNEST match, NOT exact equality or
    # raw REGEXP_CONTAINS.
    assert "REGEXP_REPLACE(V2Themes, r',\\d+;', ';')" in sql
    assert (
        "UNNEST(SPLIT(RTRIM(REGEXP_REPLACE(V2Themes, r',\\d+;', ';'), ';'), ';')) AS theme" in sql
    )
    assert "WHERE theme IN ('ECON_INFLATION')" in sql
    assert "REGEXP_CONTAINS" not in sql
    assert "V2Themes = " not in sql
    assert "LIKE '%econ_inflation%'" not in sql


def test_themes_column_also_uses_normalized_unnest() -> None:
    sql = _build(query_terms=["ECON_INFLATION"], search_columns=["Themes"])
    assert "REGEXP_REPLACE(Themes, r',\\d+;', ';')" in sql
    assert "WHERE theme IN ('ECON_INFLATION')" in sql


def test_theme_multiple_terms_use_single_in_list() -> None:
    # A bundle of theme codes is matched in one EXISTS with a de-duplicated IN list.
    sql = _build(
        query_terms=["ECON_INFLATION", "ECON_INTEREST_RATES"],
        search_columns=["V2Themes"],
    )
    assert "WHERE theme IN ('ECON_INFLATION', 'ECON_INTEREST_RATES')" in sql
    # Only one EXISTS predicate for the single theme column.
    assert sql.count("EXISTS (") == 1


def test_theme_literal_escapes_quotes_no_breakout() -> None:
    # Injection safety: single quotes are doubled, backslashes dropped, so the
    # term can't break out of the string literal.
    sql = _build(query_terms=["A.B'C)"], search_columns=["V2Themes"])
    assert "theme IN ('A.B''C)')" in sql
    # No stray unescaped quote that could terminate the literal early.
    assert "'A.B'C" not in sql


def test_theme_and_free_text_columns_mix() -> None:
    # V2Themes -> normalized UNNEST; AllNames -> LIKE, in the same query.
    sql = _build(query_terms=["ECON_INFLATION"], search_columns=["V2Themes", "AllNames"])
    assert "WHERE theme IN ('ECON_INFLATION')" in sql
    assert "LOWER(AllNames) LIKE '%econ_inflation%'" in sql


def test_theme_config_keeps_partition_and_date_filters() -> None:
    sql = _build(
        start_date="2026-05-01",
        end_date="2026-05-30",
        query_terms=["ECON_INFLATION"],
        search_columns=["V2Themes"],
        partition_filter=_PARTITION,
    )
    assert '_PARTITIONTIME >= TIMESTAMP("2026-05-01")' in sql
    assert '_PARTITIONTIME < TIMESTAMP("2026-05-31")' in sql
    assert "DATE BETWEEN 20260501000000 AND 20260530235959" in sql
    assert "WHERE theme IN ('ECON_INFLATION')" in sql
    assert "SELECT *" not in sql


def test_theme_match_passes_guardrails() -> None:
    # The EXISTS/UNNEST predicate (with ';' inside regex literals) must pass the
    # SQL guardrails, including the multi-statement check.
    from kinetic.ingestion.warehouse.bigquery.sql_guardrails import validate_bigquery_sql

    sql = _build(
        query_terms=["ECON_INFLATION", "ECON_INTEREST_RATES"],
        search_columns=["V2Themes"],
        partition_filter=_PARTITION,
    )
    validate_bigquery_sql(sql, [TABLE])


_PARTITION = {"enabled": True, "column": "_PARTITIONTIME", "type": "timestamp"}


def test_partition_filter_includes_lower_bound() -> None:
    sql = _build(start_date="2026-05-24", end_date="2026-05-30", partition_filter=_PARTITION)
    assert '_PARTITIONTIME >= TIMESTAMP("2026-05-24")' in sql


def test_partition_filter_includes_exclusive_upper_bound() -> None:
    # Exclusive end = requested end (05-30) + 1 day = 05-31.
    sql = _build(start_date="2026-05-24", end_date="2026-05-30", partition_filter=_PARTITION)
    assert '_PARTITIONTIME < TIMESTAMP("2026-05-31")' in sql


def test_partition_filter_keeps_date_between() -> None:
    sql = _build(start_date="2026-05-24", end_date="2026-05-30", partition_filter=_PARTITION)
    assert "DATE BETWEEN 20260524000000 AND 20260530235959" in sql
    assert "DATE BETWEEN 20260524 AND 20260530" not in sql


def test_partition_filter_no_select_star() -> None:
    sql = _build(partition_filter=_PARTITION)
    assert "SELECT *" not in sql


def test_partition_filter_disabled_omits_partitiontime() -> None:
    assert "_PARTITIONTIME" not in _build()
    assert "_PARTITIONTIME" not in _build(partition_filter={"enabled": False})


def test_partition_filter_exclusive_end_crosses_month() -> None:
    sql = _build(start_date="2026-05-25", end_date="2026-05-31", partition_filter=_PARTITION)
    assert '_PARTITIONTIME < TIMESTAMP("2026-06-01")' in sql


def test_partition_filter_date_type_uses_date_cast() -> None:
    sql = _build(
        start_date="2026-05-24",
        end_date="2026-05-30",
        partition_filter={"enabled": True, "column": "_PARTITIONDATE", "type": "date"},
    )
    assert '_PARTITIONDATE >= DATE("2026-05-24")' in sql
    assert '_PARTITIONDATE < DATE("2026-05-31")' in sql


def test_partition_filter_invalid_type_rejected() -> None:
    with pytest.raises(ValueError, match="partition_filter.type"):
        _build(partition_filter={"enabled": True, "type": "month"})


def test_partition_filtered_sql_passes_guardrails() -> None:
    from kinetic.ingestion.warehouse.bigquery.sql_guardrails import validate_bigquery_sql

    validate_bigquery_sql(_build(partition_filter=_PARTITION), [TABLE])


def test_passes_own_guardrails() -> None:
    # The generated SQL must satisfy the SQL guardrails.
    from kinetic.ingestion.warehouse.bigquery.sql_guardrails import validate_bigquery_sql

    validate_bigquery_sql(_build(), [TABLE])


def test_term_with_quote_is_escaped() -> None:
    # Free-text LIKE path doubles single quotes for a safe literal.
    sql = _build(query_terms=["O'Brien"], search_columns=["AllNames"])
    assert "%o''brien%" in sql


def test_builder_version_is_v4() -> None:
    from kinetic.ingestion.news.gdelt.bigquery.queries import QUERY_BUILDER_VERSION

    assert QUERY_BUILDER_VERSION == "v4"


def test_v4_date_bounds_change_cache_sql_hash() -> None:
    # The v4 14-digit DATE bounds produce a different SQL hash (and cache key)
    # than the old v3 8-digit bounds, so a stale v3 0-row cache is unreachable.
    from kinetic.ingestion.news.gdelt.bigquery.queries import QUERY_BUILDER_VERSION
    from kinetic.ingestion.warehouse.bigquery.cache import build_cache_key

    sql = _build(partition_filter=_PARTITION)
    # Reconstruct what the old (v3) 8-digit DATE bounds would have produced.
    old_sql = sql.replace("20260501000000", "20260501").replace("20260530235959", "20260530")
    assert old_sql != sql

    base = dict(
        provider="bigquery_gdelt",
        table=TABLE,
        topic="inflation_rates",
        query_terms=TERMS,
        start_date="20260501",
        end_date="20260530",
    )
    new_key = build_cache_key(**base, builder_version=QUERY_BUILDER_VERSION, sql=sql)
    old_key = build_cache_key(**base, builder_version="v3", sql=old_sql)
    assert new_key != old_key

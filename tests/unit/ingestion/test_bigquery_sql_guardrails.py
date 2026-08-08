"""Tests for BigQuery SQL guardrails."""

import pytest

from kinetic.ingestion.warehouse.bigquery.sql_guardrails import validate_bigquery_sql

SAFE_SQL = (
    "SELECT DATE AS date, COUNT(*) AS article_count\n"
    "FROM `gdelt-bq.gdeltv2.gkg_partitioned`\n"
    "WHERE DATE BETWEEN 20260501 AND 20260530\n"
    "  AND (LOWER(V2Themes) LIKE '%inflation%')\n"
    "GROUP BY date\nORDER BY date"
)


def test_safe_aggregate_query_allowed() -> None:
    # Should not raise.
    validate_bigquery_sql(SAFE_SQL)


def test_select_star_blocked() -> None:
    sql = "SELECT * FROM `t` WHERE DATE BETWEEN 1 AND 2"
    with pytest.raises(ValueError, match="SELECT \\*"):
        validate_bigquery_sql(sql)


def test_missing_where_blocked() -> None:
    sql = "SELECT date FROM `t` GROUP BY date"
    with pytest.raises(ValueError, match="WHERE"):
        validate_bigquery_sql(sql)


def test_missing_date_filter_blocked() -> None:
    sql = "SELECT date, COUNT(*) FROM `t` WHERE LOWER(x) LIKE '%a%' GROUP BY date"
    with pytest.raises(ValueError, match="date filter"):
        validate_bigquery_sql(sql)


def test_partitiontime_satisfies_date_filter() -> None:
    sql = "SELECT col FROM `t` WHERE _PARTITIONTIME > '2026-01-01'"
    validate_bigquery_sql(sql)


def test_table_suffix_satisfies_date_filter() -> None:
    sql = "SELECT col FROM `t` WHERE _TABLE_SUFFIX BETWEEN '20260101' AND '20260131'"
    validate_bigquery_sql(sql)


@pytest.mark.parametrize(
    "keyword",
    ["DELETE", "UPDATE", "INSERT", "CREATE", "DROP", "MERGE", "ALTER"],
)
def test_ddl_dml_blocked(keyword: str) -> None:
    sql = f"{keyword} something FROM `t` WHERE DATE BETWEEN 1 AND 2"
    with pytest.raises(ValueError, match="not allowed"):
        validate_bigquery_sql(sql)


def test_multi_statement_blocked() -> None:
    sql = SAFE_SQL + "; SELECT date FROM `t2` WHERE DATE BETWEEN 1 AND 2"
    with pytest.raises(ValueError, match="multi-statement"):
        validate_bigquery_sql(sql)


def test_trailing_semicolon_allowed() -> None:
    validate_bigquery_sql(SAFE_SQL + ";")


def test_allowed_tables_enforced() -> None:
    with pytest.raises(ValueError, match="allowed tables"):
        validate_bigquery_sql(SAFE_SQL, ["some-other.table"])


def test_allowed_tables_match_passes() -> None:
    validate_bigquery_sql(SAFE_SQL, ["gdelt-bq.gdeltv2.gkg_partitioned"])


def test_empty_sql_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        validate_bigquery_sql("   ")

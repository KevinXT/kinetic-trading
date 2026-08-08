"""Tests for the BigQuery GDELT counts local result cache."""

from pathlib import Path

from kinetic.ingestion.warehouse.bigquery.cache import (
    bigquery_cache_path,
    build_cache_key,
    load_cached_rows,
    write_cached_rows,
)

KEY_ARGS = dict(
    provider="bigquery_gdelt",
    table="gdelt-bq.gdeltv2.gkg_partitioned",
    topic="inflation_rates",
    query_terms=["Federal Reserve", "inflation"],
    start_date="20260501",
    end_date="20260530",
    builder_version="v1",
)


def test_cache_key_is_stable() -> None:
    assert build_cache_key(**KEY_ARGS) == build_cache_key(**KEY_ARGS)


def test_cache_key_ignores_term_order_and_case() -> None:
    a = build_cache_key(**KEY_ARGS)
    args = dict(KEY_ARGS, query_terms=["inflation", "FEDERAL RESERVE"])
    assert build_cache_key(**args) == a


def test_cache_key_changes_with_window() -> None:
    a = build_cache_key(**KEY_ARGS)
    b = build_cache_key(**dict(KEY_ARGS, end_date="20260601"))
    assert a != b


def test_cache_key_changes_with_builder_version() -> None:
    a = build_cache_key(**KEY_ARGS)
    b = build_cache_key(**dict(KEY_ARGS, builder_version="v2"))
    assert a != b


def test_v3_and_v4_cache_keys_differ() -> None:
    # The v4 bump (14-digit GKG DATE bounds) must not reuse v3 cache entries.
    a = build_cache_key(**dict(KEY_ARGS, builder_version="v3"))
    b = build_cache_key(**dict(KEY_ARGS, builder_version="v4"))
    assert a != b


def test_cache_key_changes_with_search_columns() -> None:
    a = build_cache_key(**dict(KEY_ARGS, search_columns=["V2Themes"]))
    b = build_cache_key(**dict(KEY_ARGS, search_columns=["V2Themes", "AllNames"]))
    assert a != b


def test_cache_key_ignores_search_column_order() -> None:
    a = build_cache_key(**dict(KEY_ARGS, search_columns=["V2Themes", "AllNames"]))
    b = build_cache_key(**dict(KEY_ARGS, search_columns=["AllNames", "V2Themes"]))
    assert a == b


def test_cache_key_changes_when_partition_filter_enabled() -> None:
    off = build_cache_key(**dict(KEY_ARGS, partition_filter={"enabled": False}))
    on = build_cache_key(
        **dict(
            KEY_ARGS,
            partition_filter={"enabled": True, "column": "_PARTITIONTIME", "type": "timestamp"},
        )
    )
    assert off != on


def test_cache_key_changes_with_partition_column() -> None:
    a = build_cache_key(
        **dict(KEY_ARGS, partition_filter={"enabled": True, "column": "_PARTITIONTIME"})
    )
    b = build_cache_key(
        **dict(KEY_ARGS, partition_filter={"enabled": True, "column": "_PARTITIONDATE"})
    )
    assert a != b


def test_cache_key_default_matches_disabled_partition_filter() -> None:
    a = build_cache_key(**KEY_ARGS)
    b = build_cache_key(**dict(KEY_ARGS, partition_filter={"enabled": False}))
    assert a == b


def test_cache_key_changes_with_query_name() -> None:
    a = build_cache_key(**KEY_ARGS)
    b = build_cache_key(**dict(KEY_ARGS, query_name="debug_gdelt_themes"))
    assert a != b


def test_cache_key_changes_with_sql() -> None:
    a = build_cache_key(**dict(KEY_ARGS, sql="SELECT theme FROM t WHERE DATE BETWEEN 1 AND 2"))
    b = build_cache_key(**dict(KEY_ARGS, sql="SELECT theme FROM t WHERE DATE BETWEEN 1 AND 3"))
    assert a != b


def test_cache_key_omitting_query_name_and_sql_is_backward_compatible() -> None:
    # Callers that don't pass query_name/sql (e.g. the daily-count task) keep
    # the prior stable key.
    a = build_cache_key(**KEY_ARGS)
    b = build_cache_key(**dict(KEY_ARGS, query_name=None, sql=None))
    assert a == b


def test_cache_miss_returns_none(tmp_path: Path) -> None:
    assert load_cached_rows(tmp_path / "missing.jsonl") is None


def test_cache_hit_loads_rows(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    rows = [{"date": "2026-05-01", "article_count": 3}]
    write_cached_rows(path, rows)
    assert load_cached_rows(path) == rows


def test_write_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deep" / "c.jsonl"
    write_cached_rows(path, [{"date": "2026-05-01", "article_count": 1}])
    assert path.is_file()


def test_bigquery_cache_path_layout() -> None:
    p = bigquery_cache_path("abc123")
    assert p == Path("data/cache/bigquery_gdelt_counts/abc123.jsonl")

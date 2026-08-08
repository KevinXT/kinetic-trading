"""Tests for GDELT theme bundles (loading + resolution)."""

from pathlib import Path

import pytest

from kinetic.core.errors import ConfigError
from kinetic.ingestion.news.gdelt.bigquery.queries import (
    QUERY_BUILDER_VERSION,
    build_gdelt_daily_counts_query,
)
from kinetic.ingestion.news.gdelt.bigquery.theme_bundles import (
    DEFAULT_THEME_BUNDLES_PATH,
    get_bundle_themes,
    load_theme_bundles,
    resolve_query_terms,
)
from kinetic.ingestion.warehouse.bigquery.cache import build_cache_key

INFLATION_THEMES = [
    "ECON_INFLATION",
    "ECON_INTEREST_RATES",
    "ECON_COST_OF_LIVING",
    "TAX_FNCACT_CENTRAL_BANK",
]


def test_default_bundle_file_loads() -> None:
    bundles = load_theme_bundles(DEFAULT_THEME_BUNDLES_PATH)
    assert "inflation_rates" in bundles


def test_inflation_bundle_contains_expected_codes() -> None:
    themes = get_bundle_themes("inflation_rates")
    for code in INFLATION_THEMES:
        assert code in themes


def test_theme_bundle_resolves_to_query_terms() -> None:
    terms = resolve_query_terms(query_terms=None, theme_bundle="inflation_rates")
    assert terms == INFLATION_THEMES


def test_direct_query_terms_unchanged() -> None:
    terms = resolve_query_terms(query_terms=["ECON_INFLATION"], theme_bundle=None)
    assert terms == ["ECON_INFLATION"]


def test_both_merged_deduplicated_terms_first() -> None:
    terms = resolve_query_terms(
        query_terms=["MY_CUSTOM_CODE", "ECON_INFLATION"],
        theme_bundle="inflation_rates",
    )
    # Explicit terms first, then bundle themes not already present (case-insensitive).
    assert terms[0] == "MY_CUSTOM_CODE"
    assert terms[1] == "ECON_INFLATION"
    # ECON_INFLATION appears once despite being in both.
    assert terms.count("ECON_INFLATION") == 1
    assert "ECON_INTEREST_RATES" in terms


def test_unknown_bundle_raises() -> None:
    with pytest.raises(ConfigError, match="unknown theme_bundle"):
        resolve_query_terms(query_terms=None, theme_bundle="does_not_exist")


def test_missing_bundle_file_raises() -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_theme_bundles("configs/does_not_exist_bundles.yaml")


def test_bundle_supports_bare_list(tmp_path: Path) -> None:
    p = tmp_path / "bundles.yaml"
    p.write_text(
        "theme_bundles:\n  custom:\n    - CODE_A\n    - CODE_B\n",
        encoding="utf-8",
    )
    assert get_bundle_themes("custom", p) == ["CODE_A", "CODE_B"]


def test_bundle_themes_generate_safe_sql() -> None:
    terms = get_bundle_themes("inflation_rates")
    sql = build_gdelt_daily_counts_query(
        table="gdelt-bq.gdeltv2.gkg_partitioned",
        start_date="2026-05-01",
        end_date="2026-05-30",
        query_terms=terms,
        search_columns=["V2Themes"],
        partition_filter={"enabled": True, "column": "_PARTITIONTIME", "type": "timestamp"},
    )
    from kinetic.ingestion.warehouse.bigquery.sql_guardrails import validate_bigquery_sql

    validate_bigquery_sql(sql, ["gdelt-bq.gdeltv2.gkg_partitioned"])
    for code in INFLATION_THEMES:
        assert f"'{code}'" in sql


def test_cache_key_reflects_resolved_terms_and_builder_version() -> None:
    base = dict(
        provider="bigquery_gdelt",
        table="gdelt-bq.gdeltv2.gkg_partitioned",
        topic="inflation_rates",
        start_date="20260501",
        end_date="20260530",
        builder_version=QUERY_BUILDER_VERSION,
    )
    one = build_cache_key(query_terms=["ECON_INFLATION"], **base)
    bundle = build_cache_key(query_terms=get_bundle_themes("inflation_rates"), **base)
    assert one != bundle

    # Builder version is part of the key (v3 differs from a hypothetical v2).
    v2 = build_cache_key(
        query_terms=get_bundle_themes("inflation_rates"),
        **dict(base, builder_version="v2"),
    )
    assert bundle != v2

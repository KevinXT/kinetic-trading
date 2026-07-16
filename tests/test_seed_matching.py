"""Tests for the normalized, disambiguated seed-matching layer."""

import re

import pytest
from news_data.bigquery.seed_matching import (
    COMPANY_ALIAS,
    INDUSTRY_PHRASE,
    SeedTerm,
    classify_seed,
    matched_seeds_array_expr,
    normalize_seed,
    resolve_seed_terms,
    seed_match_predicate,
    seed_terms_from_flat,
)

BLOB = "lower_blob"


def _boundary(alias: str, text: str) -> bool:
    return re.search(rf"(^|[^a-z0-9]){re.escape(alias)}([^a-z0-9]|$)", text) is not None


# ── normalization ────────────────────────────────────────────────────────────


def test_normalize_lowercases_and_collapses_space() -> None:
    assert normalize_seed("  Samsung   Electronics ") == "samsung electronics"


@pytest.mark.parametrize("bad", ["intel%", "a'b", "x;y", "chip*", ""])
def test_normalize_rejects_unsafe_characters(bad: str) -> None:
    with pytest.raises(ValueError):
        normalize_seed(bad)


# ── classification ───────────────────────────────────────────────────────────


def test_classify_company_vs_phrase() -> None:
    assert classify_seed("NVIDIA") == COMPANY_ALIAS
    assert classify_seed("intel") == COMPANY_ALIAS
    assert classify_seed("semiconductor") == INDUSTRY_PHRASE
    assert classify_seed("wafer fabrication") == INDUSTRY_PHRASE
    assert classify_seed("chipmaker") == INDUSTRY_PHRASE


# ── boundary matching rejects ambiguous substrings ───────────────────────────


def test_intel_boundary_rejects_intelligence() -> None:
    seed = SeedTerm("intel", COMPANY_ALIAS, ["intel"])
    pred = seed_match_predicate(BLOB, seed)
    assert "REGEXP_CONTAINS" in pred
    # The emitted regex must reject 'intelligence' but accept 'intel corp'.
    assert not _boundary("intel", "central intelligence agency")
    assert not _boundary("intel", "artificial intelligence")
    assert _boundary("intel", "intel corp")
    assert _boundary("intel", "intel")


def test_amd_and_micron_boundaries() -> None:
    assert not _boundary("amd", "camden council")
    assert not _boundary("amd", "hamden")
    assert _boundary("amd", "amd")
    assert not _boundary("micron", "omicron variant")
    assert not _boundary("micron", "microns")
    assert _boundary("micron", "micron technology")


def test_industry_phrase_uses_like_substring() -> None:
    seed = SeedTerm("semiconductor", INDUSTRY_PHRASE, ["semiconductor"])
    pred = seed_match_predicate(BLOB, seed)
    assert "LIKE '%semiconductor%'" in pred
    assert "REGEXP_CONTAINS" not in pred


def test_company_alias_multiple_aliases_ored() -> None:
    seed = SeedTerm("intel", COMPANY_ALIAS, ["intel corp", "intel"])
    pred = seed_match_predicate(BLOB, seed)
    assert pred.count("REGEXP_CONTAINS") == 2
    assert " OR " in pred


# ── matched-seeds array + resolution ─────────────────────────────────────────


def test_matched_seeds_array_uses_canonical_labels() -> None:
    seeds = [
        SeedTerm("semiconductor", INDUSTRY_PHRASE, ["semiconductor"]),
        SeedTerm("nvidia", COMPANY_ALIAS, ["nvidia"]),
    ]
    expr = matched_seeds_array_expr(BLOB, seeds)
    assert "['semiconductor']" in expr
    assert "['nvidia']" in expr
    assert "ARRAY_CONCAT" in expr


def test_flat_seed_terms_are_classified_and_deduped() -> None:
    seeds = seed_terms_from_flat(["Intel", "intel", "semiconductor"])
    canon = [s.canonical for s in seeds]
    assert canon == ["intel", "semiconductor"]  # deduped, order preserved
    assert seeds[0].kind == COMPANY_ALIAS
    assert seeds[1].kind == INDUSTRY_PHRASE


def test_resolve_prefers_specs_over_flat() -> None:
    seeds = resolve_seed_terms(
        seed_specs=[{"canonical": "asml", "kind": "company_alias"}],
        seed_terms=["ignored"],
    )
    assert [s.canonical for s in seeds] == ["asml"]


def test_resolve_requires_something() -> None:
    with pytest.raises(ValueError):
        resolve_seed_terms(seed_specs=None, seed_terms=None)


def test_bad_seed_kind_rejected() -> None:
    with pytest.raises(ValueError):
        SeedTerm("intel", "not_a_kind")

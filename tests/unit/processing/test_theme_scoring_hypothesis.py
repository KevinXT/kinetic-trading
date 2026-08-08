"""Unit tests for sparse-aware 2x2 hypothesis-test selection + exact Fisher test.

These cover the pure, dependency-free statistics added for scoring-v6: expected
cell counts, the two-sided Fisher exact test (validated against known reference
values), deterministic test selection, and Benjamini-Hochberg edge cases.
"""

import pytest

from kinetic.processing.news.themes.scoring import (
    EXPECTED_CELL_THRESHOLD,
    benjamini_hochberg,
    expected_cell_counts,
    fisher_exact_two_sided,
    select_hypothesis_test,
    two_proportion_z_test,
)

# ── Fisher exact test (reference values) ─────────────────────────────────────


@pytest.mark.parametrize(
    "table,expected",
    [
        ((8, 2, 1, 5), 0.034965),  # classic scipy example
        ((3, 1, 1, 3), 0.485714),
        ((1, 9, 11, 3), 0.002759),  # two-sided (NOT the one-sided 0.001346)
        ((10, 10, 10, 10), 1.0),
    ],
)
def test_fisher_matches_reference_values(table, expected) -> None:
    a, b, c, d = table
    assert fisher_exact_two_sided(a, b, c, d) == pytest.approx(expected, abs=1e-6)


def test_fisher_bounded_and_symmetric_in_rows() -> None:
    p = fisher_exact_two_sided(8, 2, 1, 5)
    assert 0.0 <= p <= 1.0
    # Swapping the two rows leaves a 2x2 Fisher p-value unchanged.
    assert fisher_exact_two_sided(1, 5, 8, 2) == pytest.approx(p, abs=1e-12)


def test_fisher_degenerate_margin_is_one() -> None:
    assert fisher_exact_two_sided(5, 0, 5, 0) == 1.0
    assert fisher_exact_two_sided(0, 5, 0, 5) == 1.0


def test_fisher_negative_cell_is_none() -> None:
    assert fisher_exact_two_sided(-1, 5, 2, 3) is None


# ── expected cell counts ─────────────────────────────────────────────────────


def test_expected_cell_counts() -> None:
    # 2x2 with row totals (10, 90), col totals (20, 80), grand 100.
    ea, eb, ec, ed = expected_cell_counts(5, 5, 15, 75)
    assert ea == pytest.approx(2.0)  # 10*20/100
    assert eb == pytest.approx(8.0)
    assert ec == pytest.approx(18.0)
    assert ed == pytest.approx(72.0)
    assert min(ea, eb, ec, ed) == pytest.approx(2.0)


def test_expected_cell_counts_empty_table_none() -> None:
    assert expected_cell_counts(0, 0, 0, 0) is None


# ── deterministic test selection ─────────────────────────────────────────────


def test_dense_table_selects_z_test() -> None:
    # Big, balanced counts -> all expected cells well above 5 -> z-test.
    r = select_hypothesis_test(500, 10000, 500, 10000)
    assert r.hypothesis_test == "two_proportion_z_test"
    assert r.minimum_expected_cell_count >= EXPECTED_CELL_THRESHOLD
    assert r.p_value_valid and 0.0 <= r.p_value <= 1.0
    # Matches the standalone z-test on the same disjoint groups.
    assert r.p_value == pytest.approx(two_proportion_z_test(500, 10000, 500, 10000))


def test_sparse_table_selects_fisher() -> None:
    # a=25 support but tiny with-theme column -> min expected cell < 5 -> Fisher.
    r = select_hypothesis_test(25, 40000, 3, 7_000_000)
    assert r.hypothesis_test == "fisher_exact_two_sided"
    assert r.minimum_expected_cell_count < EXPECTED_CELL_THRESHOLD
    assert r.p_value_valid and 0.0 <= r.p_value <= 1.0
    assert (r.contingency_a, r.contingency_b, r.contingency_c, r.contingency_d) == (
        25,
        39975,
        3,
        6_999_997,
    )


def test_invalid_table_emits_unavailable_and_null() -> None:
    # with-theme count exceeds its group total -> invalid, no p-value.
    r = select_hypothesis_test(50, 40, 3, 100)
    assert r.hypothesis_test == "unavailable_invalid_table"
    assert r.p_value is None and r.p_value_valid is False
    assert r.p_value_unavailable_reason


def test_missing_inputs_unavailable() -> None:
    r = select_hypothesis_test(None, 40000, 3, 7_000_000)
    assert r.hypothesis_test == "unavailable_invalid_table"
    assert r.p_value is None and not r.p_value_valid


def test_sparse_without_exact_test_emits_null_not_ztest() -> None:
    # If no exact implementation were available, a sparse table must NOT silently
    # fall back to the z-test — it emits a null p-value with a precise reason.
    r = select_hypothesis_test(25, 40000, 3, 7_000_000, fisher_available=False)
    assert r.hypothesis_test == "unavailable_sparse_without_exact_test"
    assert r.p_value is None and not r.p_value_valid
    assert "sparse" in r.p_value_unavailable_reason


def test_threshold_is_deterministic_not_favorable() -> None:
    # A borderline table just under threshold picks Fisher; just over picks z.
    # The choice depends only on expected counts, not on which p is smaller.
    r_sparse = select_hypothesis_test(4, 100, 4, 100)  # min expected = 8*100/... small
    r_dense = select_hypothesis_test(50, 100, 50, 100)
    assert r_dense.hypothesis_test == "two_proportion_z_test"
    assert r_sparse.hypothesis_test in {
        "two_proportion_z_test",
        "fisher_exact_two_sided",
    }
    # Whichever it is, it is driven purely by the expected-cell minimum.
    if r_sparse.minimum_expected_cell_count >= EXPECTED_CELL_THRESHOLD:
        assert r_sparse.hypothesis_test == "two_proportion_z_test"
    else:
        assert r_sparse.hypothesis_test == "fisher_exact_two_sided"


# ── Benjamini-Hochberg edge cases ────────────────────────────────────────────


def test_bh_empty_valid_family() -> None:
    assert benjamini_hochberg([None, None, None]) == [None, None, None]


def test_bh_single_valid_pvalue() -> None:
    out = benjamini_hochberg([None, 0.01, None])
    assert out[0] is None and out[2] is None
    assert out[1] == pytest.approx(0.01)  # m=1 -> adjusted == raw


def test_bh_mixed_valid_and_null_excludes_nulls_from_m() -> None:
    # Two valid p-values; nulls do not inflate the family size m.
    out = benjamini_hochberg([0.01, None, 0.04])
    assert out[1] is None
    # m=2: rank-2 raw 0.04 -> 0.04; rank-1 raw 0.01 -> min(0.02, 0.04) = 0.02.
    assert out[2] == pytest.approx(0.04)
    assert out[0] == pytest.approx(0.02)


def test_bh_monotone_bounded_and_ties() -> None:
    raw = [0.9, 0.5, 0.5, 0.01, 0.2]
    out = benjamini_hochberg(raw)
    assert all(o is not None for o in out)
    assert all(0.0 <= o <= 1.0 for o in out)
    # Sorted by raw p, adjusted values are non-decreasing.
    order = sorted(range(len(raw)), key=lambda i: raw[i])
    adj_sorted = [out[i] for i in order]
    assert adj_sorted == sorted(adj_sorted)


def test_bh_deterministic_under_ties() -> None:
    raw = [0.5, 0.5, 0.5]
    assert benjamini_hochberg(raw) == benjamini_hochberg(list(raw))


# ── Independent Fisher-exact validation (Phase 3) ────────────────────────────
#
# The production ``fisher_exact_two_sided`` sums hypergeometric point
# probabilities in log-space (``math.lgamma``). To validate it *independently*
# (not merely "the unit tests pass"), the helper below recomputes the same
# two-sided convention with EXACT rational arithmetic (``fractions.Fraction`` +
# ``math.comb``) — a completely different numerical path with no floating point.
# Convention (stated explicitly): two-sided Fisher exact p-value = the sum of the
# probabilities of all feasible fixed-margin tables whose point probability is
# <= that of the observed table. This matches ``scipy.stats.fisher_exact(...,
# alternative="two-sided")`` and R's ``fisher.test``. SciPy is NOT installed and
# is NOT added as a dependency; the hard-coded literals below were produced by
# SciPy/R and are cross-checked here by the exact-rational path.

from fractions import Fraction  # noqa: E402
from math import comb  # noqa: E402

from kinetic.processing.news.themes.scoring import fisher_exact_two_sided as _fisher  # noqa: E402


def _fisher_exact_rational(a: int, b: int, c: int, d: int) -> Fraction:
    """Independent exact two-sided Fisher p-value via rational hypergeometric sum."""
    n1, n0, col1 = a + b, c + d, a + c
    n = n1 + n0
    lo, hi = max(0, col1 - n0), min(n1, col1)
    denom = comb(n, col1)
    p_obs = Fraction(comb(n1, a) * comb(n0, col1 - a), denom)
    total = Fraction(0)
    for k in range(lo, hi + 1):
        pk = Fraction(comb(n1, k) * comb(n0, col1 - k), denom)
        if pk <= p_obs:
            total += pk
    return total


# Hard-coded reference values from SciPy 1.x ``fisher_exact(table,
# alternative="two-sided")`` (equivalently R ``fisher.test``). Only well-known,
# textbook-stable literals are anchored here; every other edge case is validated
# against the exact-rational path (which is itself the mathematical ground truth).
_SCIPY_REFERENCE = {
    (8, 2, 1, 5): 0.03496503496503497,  # classic 2x2 (Agresti); two-sided
    (3, 1, 1, 3): 0.4857142857142857,  # Fisher's tea-tasting table
    (1, 9, 11, 3): 0.0027594561852200832,  # two-sided (one-sided-less is 0.0013)
    (10, 10, 10, 10): 1.0,  # perfectly balanced -> p == 1
}


@pytest.mark.parametrize("table,expected", list(_SCIPY_REFERENCE.items()))
def test_fisher_matches_scipy_reference(table, expected) -> None:
    a, b, c, d = table
    p = _fisher(a, b, c, d)
    assert p == pytest.approx(expected, rel=1e-6, abs=1e-9)
    # And the fully independent exact-rational path agrees with the reference.
    assert float(_fisher_exact_rational(a, b, c, d)) == pytest.approx(expected, rel=1e-6, abs=1e-9)


# Deterministic grid spanning textbook, zero-cell, imbalanced, near-one, and
# very-small-p tables. The production log-space function must match the exact
# rational computation everywhere.
_GRID = [
    (a, b, c, d)
    for a in range(0, 6)
    for b in range(0, 6)
    for c in range(0, 6)
    for d in range(0, 6)
    if (a + b) > 0 and (c + d) > 0 and (a + c) > 0 and (b + d) > 0
]


@pytest.mark.parametrize("table", _GRID)
def test_fisher_matches_exact_rational_on_grid(table) -> None:
    a, b, c, d = table
    p = _fisher(a, b, c, d)
    assert p is not None
    assert 0.0 <= p <= 1.0
    assert p == pytest.approx(float(_fisher_exact_rational(a, b, c, d)), abs=1e-12)


def test_fisher_symmetries_row_col_transpose() -> None:
    a, b, c, d = 8, 2, 1, 5
    base = _fisher(a, b, c, d)
    assert _fisher(c, d, a, b) == pytest.approx(base, abs=1e-12)  # swap rows
    assert _fisher(b, a, d, c) == pytest.approx(base, abs=1e-12)  # swap columns
    assert _fisher(a, c, b, d) == pytest.approx(base, abs=1e-12)  # transpose


def test_fisher_numerical_stability_experiment_scale() -> None:
    # Margins similar to the largest sparse tables in the live experiment:
    # tiny with-theme column against a ~7M non-seed background.
    p = _fisher(25, 40_000, 3, 7_000_000)
    assert p is not None and 0.0 <= p <= 1.0
    # Exact rational path agrees despite the huge d cell / factorials.
    assert p == pytest.approx(float(_fisher_exact_rational(25, 40_000, 3, 7_000_000)), abs=1e-9)


def test_fisher_p_near_one_and_very_small() -> None:
    assert _fisher(10, 10, 10, 10) == pytest.approx(1.0, abs=1e-12)
    assert 0.0 < _fisher(0, 10, 10, 0) < 1e-3  # strong separation -> tiny p


def test_fisher_single_feasible_table_is_one() -> None:
    # col1 == 0 (no with-theme records at all) -> only one feasible table.
    assert _fisher(0, 40, 0, 60) == 1.0


def test_fisher_deterministic_repeatable() -> None:
    t = (2, 8, 20, 5)
    assert _fisher(*t) == _fisher(*t)

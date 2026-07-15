"""Tests for the dependency-free statistics helpers."""

import math

from research_data import stats


def test_lag_zscore_excludes_current_and_needs_dispersion() -> None:
    # Constant prior window has zero dispersion -> undefined, never infinite.
    assert stats.lag_zscore(10.0, [5.0, 5.0, 5.0]) is None
    z = stats.lag_zscore(10.0, [1.0, 2.0, 3.0])
    assert z is not None and z > 0


def test_lag_zscore_short_window_is_none() -> None:
    assert stats.lag_zscore(1.0, [2.0]) is None


def test_trailing_percentile_uses_prior_only() -> None:
    assert stats.trailing_percentile(3.0, [1.0, 2.0, 3.0]) == 1.0
    assert stats.trailing_percentile(0.0, [1.0, 2.0, 3.0]) == 0.0
    assert stats.trailing_percentile(1.0, []) is None


def test_entropy_and_hhi_edge_cases() -> None:
    assert stats.entropy([]) is None
    assert stats.entropy([5.0]) == 0.0  # single source -> zero entropy
    assert stats.herfindahl([1.0]) == 1.0
    assert stats.herfindahl([0.0, 0.0]) is None
    # Even split across four -> HHI = 0.25, effective sources = 4.
    assert math.isclose(stats.herfindahl([1, 1, 1, 1]), 0.25)
    assert math.isclose(stats.effective_count([1, 1, 1, 1]), 4.0, rel_tol=1e-9)


def test_bootstrap_ci_is_deterministic() -> None:
    values = [0.1, -0.2, 0.3, 0.05, -0.1, 0.2]
    a = stats.bootstrap_ci(values, seed=7)
    b = stats.bootstrap_ci(values, seed=7)
    assert a == b
    assert a[0] is not None and a[1] is not None and a[0] <= a[1]


def test_bootstrap_ci_needs_two_points() -> None:
    assert stats.bootstrap_ci([1.0]) == (None, None)


def test_benjamini_hochberg_basic() -> None:
    # One tiny p-value among large ones should be rejected.
    rejected = stats.benjamini_hochberg([0.001, 0.6, 0.7, 0.8], alpha=0.05)
    assert rejected[0] is True
    assert rejected[1:] == [False, False, False]
    assert stats.benjamini_hochberg([]) == []


def test_benjamini_hochberg_all_large_rejects_none() -> None:
    assert stats.benjamini_hochberg([0.4, 0.5, 0.9]) == [False, False, False]


def test_benjamini_hochberg_adjusted_is_aligned_and_monotone() -> None:
    adjusted = stats.benjamini_hochberg_adjusted([0.01, 0.04, 0.03])
    assert len(adjusted) == 3
    assert adjusted[0] <= adjusted[2] <= adjusted[1]


def test_session_date_bootstrap_moves_same_date_rows_together() -> None:
    # Duplicating identical cross-sectional rows within each date does not change
    # the result because the bootstrap unit is the complete session-date cluster.
    clustered = {"2026-06-01": [1.0, 1.0], "2026-06-02": [3.0, 3.0]}
    collapsed = {"2026-06-01": [1.0], "2026-06-02": [3.0]}
    a = stats.session_date_block_bootstrap_ci(clustered, iterations=100, seed=7, block_length=1)
    b = stats.session_date_block_bootstrap_ci(collapsed, iterations=100, seed=7, block_length=1)
    assert a == b


def test_session_date_block_bootstrap_is_deterministic() -> None:
    values = {
        "2026-06-01": [0.01, 0.02],
        "2026-06-02": [0.03],
        "2026-06-03": [-0.01, 0.0],
    }
    a = stats.session_date_block_bootstrap_ci(values, iterations=100, seed=11, block_length=2)
    b = stats.session_date_block_bootstrap_ci(values, iterations=100, seed=11, block_length=2)
    assert a == b

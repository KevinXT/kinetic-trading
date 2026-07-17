"""Tests for pilot sample-size planning (Wilson solver and scenarios)."""

from __future__ import annotations

import pytest
from research_data.relevance.metrics import wilson_interval
from research_data.relevance.pilot_planning import (
    build_pilot_sample_size_plan,
    finite_population_corrected_n,
    minimum_n_for_wilson_half_width,
    round_up_to,
    wilson_half_width_for_p,
    z_for_confidence,
)


def test_wilson_interval_hand_calculation() -> None:
    # Classic check: n=100, x=50, z≈1.96 → half-width near 0.096
    low, high = wilson_interval(50, 100)
    center = (low + high) / 2
    hw = (high - low) / 2
    assert abs(center - 0.5) < 0.02
    assert 0.09 < hw < 0.11


def test_wilson_half_width_table_p50() -> None:
    z = z_for_confidence(0.95)
    expected = {
        100: 0.096,
        150: 0.079,
        200: 0.069,
        250: 0.062,
        300: 0.056,
        385: 0.050,
    }
    for n, approx in expected.items():
        hw = wilson_half_width_for_p(0.50, n, z=z)
        assert abs(hw - approx) < 0.005, (n, hw, approx)


def test_minimum_n_solver_prevalence_half_width() -> None:
    z = z_for_confidence(0.95)
    n = minimum_n_for_wilson_half_width(0.50, 0.07, z=z)
    assert 190 <= n <= 200
    assert wilson_half_width_for_p(0.50, n, z=z) <= 0.07
    assert wilson_half_width_for_p(0.50, n - 1, z=z) > 0.07


def test_positive_negative_denominator_planning() -> None:
    z = z_for_confidence(0.95)
    # ~0.80 rate, half-width 0.10 → ~60
    n = minimum_n_for_wilson_half_width(0.80, 0.10, z=z)
    assert 55 <= n <= 70


def test_finite_population_and_reserve() -> None:
    n_fpc = finite_population_corrected_n(200, 250)
    assert n_fpc < 200
    assert n_fpc >= 1
    assert round_up_to(193, 10) == 200


def test_worst_case_and_underfilled() -> None:
    plan = build_pilot_sample_size_plan(
        eligible_cluster_population_size=50,
        target_prevalence_half_width=0.07,
        expected_unusable_fraction=0.10,
    )
    assert plan.worst_case_representative_n == max(
        row["n_with_unusable_reserve"] for row in plan.required_representative_by_prevalence
    )
    assert plan.underfilled is True
    assert plan.population_can_satisfy_plan is False
    assert any("UNDERFILLED" in w for w in plan.warnings)
    # Operational target is rounded; not quietly reduced below derived worst-case.
    assert plan.operational_target_representative >= plan.worst_case_representative_n


def test_determinism() -> None:
    a = build_pilot_sample_size_plan(eligible_cluster_population_size=500)
    b = build_pilot_sample_size_plan(eligible_cluster_population_size=500)
    assert a.to_dict() == b.to_dict()


def test_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        z_for_confidence(0.97)
    with pytest.raises(ValueError):
        build_pilot_sample_size_plan(
            eligible_cluster_population_size=10, target_prevalence_half_width=0
        )
    with pytest.raises(ValueError):
        build_pilot_sample_size_plan(
            eligible_cluster_population_size=10, expected_unusable_fraction=1.0
        )


def test_operational_round_near_200() -> None:
    plan = build_pilot_sample_size_plan(
        eligible_cluster_population_size=10_000,
        target_prevalence_half_width=0.07,
        apply_finite_population_correction=False,
        expected_unusable_fraction=0.0,
        # Keep denominator requirements from dominating prevalence for this check
        desired_minimum_positive_denominator=1,
        desired_minimum_negative_denominator=1,
        metric_half_width=0.50,
    )
    # At p=0.5, n≈193 → round to 200 when round_up_to=10
    assert plan.operational_target_representative == 200

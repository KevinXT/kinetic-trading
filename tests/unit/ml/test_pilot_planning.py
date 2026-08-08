"""Tests for pilot sample-size planning (Wilson solver and scenarios)."""

from __future__ import annotations

import pytest

from kinetic.ml.relevance.metrics import wilson_interval
from kinetic.ml.relevance.pilot_planning import (
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
    assert plan.underpowered is True
    assert plan.feasible is False
    assert plan.population_can_satisfy_plan is False
    assert any("UNDERFILLED" in w for w in plan.warnings)
    assert any("UNDERPOWERED" in w for w in plan.warnings)
    # Operational draw may be capped at N while inferential requirement stays visible.
    assert plan.operational_target_representative <= plan.eligible_cluster_population_size
    assert plan.required_sample_size > plan.eligible_cluster_population_size


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


def test_fpc_does_not_reduce_impossible_positive_denominator() -> None:
    # N=100, q=0.10, m+=60 → n+ = 600. FPC must not reduce 600.
    plan = build_pilot_sample_size_plan(
        eligible_cluster_population_size=100,
        planning_prevalence_scenarios=(0.10,),
        desired_minimum_positive_denominator=60,
        desired_minimum_negative_denominator=1,
        expected_unusable_fraction=0.0,
        apply_finite_population_correction=True,
        round_up_to_multiple=1,
        target_prevalence_half_width=0.50,
    )
    row = plan.required_representative_by_prevalence[0]
    assert row["n_for_positive_denominator"] == 600
    assert row["required_sample_size"] == 600
    assert row["feasible"] is False
    assert plan.underpowered is True
    assert plan.feasible is False
    # Prevalence FPC may shrink n_prev, but not the positive requirement.
    assert row["n_prev_with_fpc"] <= row["n_for_prevalence_half_width"]
    assert row["n_with_finite_population_correction"] == 600


def test_fpc_does_not_reduce_impossible_negative_denominator() -> None:
    # N=100, q=0.95, m-=60 → n- = 1200.
    plan = build_pilot_sample_size_plan(
        eligible_cluster_population_size=100,
        planning_prevalence_scenarios=(0.95,),
        desired_minimum_positive_denominator=1,
        desired_minimum_negative_denominator=60,
        expected_unusable_fraction=0.0,
        apply_finite_population_correction=True,
        round_up_to_multiple=1,
        target_prevalence_half_width=0.50,
    )
    row = plan.required_representative_by_prevalence[0]
    assert row["n_for_negative_denominator"] == 1200
    assert row["required_sample_size"] == 1200
    assert plan.underpowered is True
    assert plan.feasible is False


def test_fpc_reduces_prevalence_precision_only() -> None:
    n0 = minimum_n_for_wilson_half_width(0.50, 0.07, z=z_for_confidence(0.95))
    n_fpc = finite_population_corrected_n(n0, 250)
    assert n_fpc < n0
    plan = build_pilot_sample_size_plan(
        eligible_cluster_population_size=250,
        planning_prevalence_scenarios=(0.50,),
        desired_minimum_positive_denominator=1,
        desired_minimum_negative_denominator=1,
        expected_unusable_fraction=0.0,
        apply_finite_population_correction=True,
        round_up_to_multiple=1,
        target_prevalence_half_width=0.07,
    )
    row = plan.required_representative_by_prevalence[0]
    assert row["n_prev_with_fpc"] == n_fpc
    assert row["n_for_prevalence_half_width"] == n0
    assert row["fpc_applied_only_to_prevalence_precision"] is True


def test_open_population_skips_fpc() -> None:
    plan = build_pilot_sample_size_plan(
        eligible_cluster_population_size=0,
        planning_prevalence_scenarios=(0.50,),
        desired_minimum_positive_denominator=1,
        desired_minimum_negative_denominator=1,
        expected_unusable_fraction=0.0,
        apply_finite_population_correction=True,
        round_up_to_multiple=1,
        target_prevalence_half_width=0.07,
    )
    row = plan.required_representative_by_prevalence[0]
    assert row["finite_population_correction_applied"] is False
    assert row["n_prev_with_fpc"] == row["n_for_prevalence_half_width"]


def test_small_synthetic_population_labeled_underpowered() -> None:
    # 22 eligible clusters cannot satisfy ~60 positives and ~60 negatives.
    plan = build_pilot_sample_size_plan(
        eligible_cluster_population_size=22,
        planning_prevalence_scenarios=(0.25, 0.50),
        desired_minimum_positive_denominator=60,
        desired_minimum_negative_denominator=60,
        expected_unusable_fraction=0.0,
        apply_finite_population_correction=True,
        round_up_to_multiple=1,
    )
    assert plan.underpowered is True
    assert plan.feasible is False
    assert plan.required_sample_size > 22
    assert any("SMALL_SYNTHETIC" in w or "UNDERPOWERED" in w for w in plan.warnings)
    # Software fixture can exercise workflow; statistical requirement remains underpowered.
    assert plan.operational_target_representative <= 22

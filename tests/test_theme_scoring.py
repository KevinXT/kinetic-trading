"""Tests for association scoring of theme candidates."""

import math

from news_data.bigquery.theme_scoring import (
    ScoreComponents,
    association_component,
    benjamini_hochberg,
    composite_score,
    herfindahl,
    lift,
    lift_confidence_interval,
    odds_ratio,
    prevalence,
    risk_difference,
    share,
    smoothed_log_lift,
    support_component,
    temporal_stability_component,
    top_share,
    two_proportion_z_test,
    weekly_stability,
)


def test_prevalence_and_zero_handling() -> None:
    assert prevalence(50, 200) == 0.25
    assert prevalence(5, 0) is None
    assert prevalence(None, 10) is None


def test_lift_enrichment_and_depletion() -> None:
    # Theme 10x more prevalent in seed than background.
    assert lift(100, 1000, 100, 10000) == 10.0
    # No enrichment.
    assert lift(100, 1000, 1000, 10000) == 1.0
    # Undefined when background prevalence is zero.
    assert lift(100, 1000, 0, 10000) is None


def test_smoothed_log_lift_finite_at_zero_background() -> None:
    # Raw lift would be infinite (b=0); smoothing keeps it finite and positive.
    val = smoothed_log_lift(100, 1000, 0, 10000)
    assert val is not None and val > 0 and math.isfinite(val)
    # No enrichment -> ~0.
    assert abs(smoothed_log_lift(100, 1000, 1000, 10000)) < 0.05


def test_risk_difference_sign() -> None:
    assert risk_difference(100, 1000, 100, 10000) > 0
    assert risk_difference(10, 1000, 1000, 10000) < 0
    assert risk_difference(None, 1000, 1, 10) is None


def test_odds_ratio_smoothed_and_bounds() -> None:
    assert odds_ratio(100, 1000, 100, 10000) > 1.0
    # Guards against impossible counts.
    assert odds_ratio(2000, 1000, 100, 10000) is None


def test_low_support_does_not_explode_via_smoothing() -> None:
    # One seeded record, zero background: raw lift explodes, smoothed stays modest.
    big = lift(1, 1, 0, 1_000_000)
    small = smoothed_log_lift(1, 1, 0, 1_000_000)
    assert big is None  # background prevalence 0 -> undefined raw lift
    assert small is not None and small < 15  # bounded by smoothing


def test_concentration_helpers() -> None:
    assert top_share([90, 5, 5]) == 0.9
    assert top_share([0, 0]) is None
    assert herfindahl([1, 1, 1, 1]) == 0.25  # even across 4 -> 1/4
    assert herfindahl([]) is None


def test_support_component_floor_and_saturation() -> None:
    assert support_component(10, 25, 5000) == 0.0  # below floor
    assert support_component(5000, 25, 5000) == 1.0  # saturated
    mid = support_component(500, 25, 5000)
    assert mid is not None and 0.0 < mid < 1.0
    assert support_component(None, 25, 5000) is None


def test_association_component_logistic() -> None:
    assert association_component(0.0) == 0.5
    assert association_component(None) is None
    assert association_component(5.0) > 0.9
    assert association_component(-5.0) < 0.1


def test_temporal_stability_component() -> None:
    assert temporal_stability_component(30, 30) == 1.0
    assert temporal_stability_component(15, 30) == 0.5
    assert temporal_stability_component(1, 0) is None


def test_composite_none_without_association() -> None:
    comps = ScoreComponents(
        support=1.0,
        association=None,
        temporal_stability=1.0,
        source_diversity=1.0,
        seed_diversity=1.0,
        generic_penalty=0.0,
        duplication_penalty=None,
    )
    # Without a background-based association signal there is no composite.
    assert composite_score(comps) is None


def test_composite_penalizes_generic() -> None:
    base = ScoreComponents(0.8, 0.8, 0.8, 0.8, 0.8, 0.0, None)
    generic = ScoreComponents(0.8, 0.8, 0.8, 0.8, 0.8, 0.8, None)
    s_base = composite_score(base)
    s_generic = composite_score(generic)
    assert s_base is not None and s_generic is not None
    assert s_generic < s_base


def test_two_proportion_z_test_significance_vs_effect() -> None:
    # Strong enrichment, ample support -> tiny p-value.
    p_strong = two_proportion_z_test(400, 5000, 800, 500000)
    assert p_strong is not None and p_strong < 1e-6
    # No prevalence gap -> p ~ 1 regardless of large counts (significance != effect).
    p_null = two_proportion_z_test(3000, 5000, 300000, 500000)
    assert p_null is not None and p_null > 0.5
    # Undefined denominators.
    assert two_proportion_z_test(1, 0, 1, 10) is None


def test_lift_confidence_interval_brackets_point_estimate() -> None:
    point = lift(400, 5000, 800, 500000)
    ci = lift_confidence_interval(400, 5000, 800, 500000)
    assert ci is not None and point is not None
    low, high = ci
    assert 0 < low < point < high
    # Missing inputs -> None (honest null).
    assert lift_confidence_interval(None, 5000, 800, 500000) is None


def test_benjamini_hochberg_orders_and_preserves_nulls() -> None:
    raw = [0.001, 0.5, None, 0.02]
    adj = benjamini_hochberg(raw)
    assert adj[2] is None  # null passes through, excluded from m
    # Adjusted >= raw and within [0, 1].
    for r, a in zip(raw, adj):
        if r is not None:
            assert a is not None and r <= a <= 1.0
    # Empty / all-None input is safe.
    assert benjamini_hochberg([None, None]) == [None, None]


def test_weekly_stability_summary() -> None:
    assert weekly_stability([80, 90, 0, 85, 75]) == (4, 75)
    assert weekly_stability([0, 0, 0]) == (0, 0)
    assert weekly_stability([None, 10, 5]) == (2, 5)


def test_share_bounds() -> None:
    assert share(40, 400) == 0.1
    assert share(500, 400) == 1.0  # clipped
    assert share(1, 0) is None
    assert share(None, 10) is None

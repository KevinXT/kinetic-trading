"""Quantitative sample-size planning for the real-corpus relevance pilot.

Uses Wilson score intervals (Newcombe 1998). Does not hardcode “200 is enough.”
Operational defaults are labeled as engineering choices, not universal laws.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from research_data.relevance.metrics import wilson_interval

PLANNING_VERSION = "pilot-sample-size-plan-v1"

# Approximate z for common confidence levels.
_Z_BY_CONFIDENCE = {
    0.90: 1.6448536269514722,
    0.95: 1.959963984540054,
    0.99: 2.5758293035489004,
}


def z_for_confidence(confidence_level: float) -> float:
    if confidence_level in _Z_BY_CONFIDENCE:
        return _Z_BY_CONFIDENCE[confidence_level]
    if not 0.5 < confidence_level < 1.0:
        raise ValueError("confidence_level must be in (0.5, 1)")
    # Stay dependency-free: require tabulated confidence levels.
    raise ValueError(
        f"unsupported confidence_level {confidence_level}; "
        f"supported: {sorted(_Z_BY_CONFIDENCE)}"
    )


def wilson_half_width(successes: int, n: int, *, z: float) -> Optional[float]:
    interval = wilson_interval(successes, n, z=z)
    if interval is None:
        return None
    low, high = interval
    phat = successes / n if n else 0.0
    # Half-width around Wilson center (symmetric in Wilson formula).
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2 * n)) / denom
    return max(center - low, high - center)


def wilson_half_width_for_p(p: float, n: int, *, z: float) -> float:
    if n < 1:
        raise ValueError("n must be >= 1")
    if not 0.0 <= p <= 1.0:
        raise ValueError("p must be in [0, 1]")
    # Use continuous form of Wilson half-width (not rounded successes).
    z2 = z * z
    denom = 1.0 + z2 / n
    margin = (z / denom) * math.sqrt((p * (1.0 - p) / n) + (z2 / (4 * n * n)))
    return margin


def minimum_n_for_wilson_half_width(
    p: float,
    target_half_width: float,
    *,
    z: float,
    n_max: int = 1_000_000,
) -> int:
    """Smallest integer n such that Wilson half-width at prevalence p <= target."""
    if not 0.0 < target_half_width <= 1.0:
        raise ValueError("target_half_width must be in (0, 1]")
    if not 0.0 <= p <= 1.0:
        raise ValueError("p must be in [0, 1]")
    lo, hi = 1, n_max
    best = n_max
    while lo <= hi:
        mid = (lo + hi) // 2
        hw = wilson_half_width_for_p(p, mid, z=z)
        if hw <= target_half_width:
            best = mid
            hi = mid - 1
        else:
            lo = mid + 1
    if wilson_half_width_for_p(p, best, z=z) > target_half_width:
        raise ValueError(f"cannot achieve half-width {target_half_width} at p={p} with n<={n_max}")
    return best


def finite_population_corrected_n(n_infinite: int, population_size: int) -> int:
    """Approximate FPC-adjusted sample size for proportion planning."""
    if population_size < 1:
        raise ValueError("population_size must be >= 1")
    if n_infinite < 1:
        raise ValueError("n_infinite must be >= 1")
    # n_fpc = n / (1 + (n - 1) / N)
    return int(math.ceil(n_infinite / (1.0 + (n_infinite - 1) / population_size)))


def round_up_to(n: int, multiple: int) -> int:
    if multiple < 1:
        raise ValueError("round_up_to multiple must be >= 1")
    if n % multiple == 0:
        return n
    return n + (multiple - n % multiple)


@dataclass(frozen=True)
class PilotSampleSizePlanV1:
    confidence_level: float
    target_prevalence_half_width: float
    planning_prevalence_scenarios: tuple[float, ...]
    assumed_baseline_precision_scenarios: tuple[float, ...]
    assumed_baseline_recall_scenarios: tuple[float, ...]
    eligible_cluster_population_size: int
    desired_minimum_positive_denominator: int
    desired_minimum_negative_denominator: int
    planned_calibration_round_size: int
    planned_double_annotation_count: int
    expected_unusable_fraction: float
    apply_finite_population_correction: bool
    round_up_to_multiple: int
    challenge_target_count: int
    duplicate_candidate_target_count: int
    duplicate_below_threshold_audit_target_count: int
    planning_version: str = PLANNING_VERSION

    # Outputs filled by build_pilot_sample_size_plan
    scenario_rows: tuple[dict[str, Any], ...] = ()
    required_representative_by_prevalence: tuple[dict[str, Any], ...] = ()
    worst_case_representative_n: int = 0
    required_positive_count: int = 0
    required_negative_count: int = 0
    planned_reserve: int = 0
    operational_target_representative: int = 0
    population_can_satisfy_plan: bool = False
    underfilled: bool = False
    warnings: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    formula_notes: tuple[str, ...] = ()
    derived_vs_operational: tuple[dict[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "planning_version": self.planning_version,
            "confidence_level": self.confidence_level,
            "target_prevalence_half_width": self.target_prevalence_half_width,
            "planning_prevalence_scenarios": list(self.planning_prevalence_scenarios),
            "assumed_baseline_precision_scenarios": list(self.assumed_baseline_precision_scenarios),
            "assumed_baseline_recall_scenarios": list(self.assumed_baseline_recall_scenarios),
            "eligible_cluster_population_size": self.eligible_cluster_population_size,
            "desired_minimum_positive_denominator": self.desired_minimum_positive_denominator,
            "desired_minimum_negative_denominator": self.desired_minimum_negative_denominator,
            "planned_calibration_round_size": self.planned_calibration_round_size,
            "planned_double_annotation_count": self.planned_double_annotation_count,
            "expected_unusable_fraction": self.expected_unusable_fraction,
            "apply_finite_population_correction": self.apply_finite_population_correction,
            "round_up_to_multiple": self.round_up_to_multiple,
            "challenge_target_count": self.challenge_target_count,
            "duplicate_candidate_target_count": self.duplicate_candidate_target_count,
            "duplicate_below_threshold_audit_target_count": (
                self.duplicate_below_threshold_audit_target_count
            ),
            "scenario_rows": list(self.scenario_rows),
            "required_representative_by_prevalence": list(
                self.required_representative_by_prevalence
            ),
            "worst_case_representative_n": self.worst_case_representative_n,
            "required_positive_count": self.required_positive_count,
            "required_negative_count": self.required_negative_count,
            "planned_reserve": self.planned_reserve,
            "operational_target_representative": self.operational_target_representative,
            "population_can_satisfy_plan": self.population_can_satisfy_plan,
            "underfilled": self.underfilled,
            "warnings": list(self.warnings),
            "assumptions": list(self.assumptions),
            "formula_notes": list(self.formula_notes),
            "derived_vs_operational": list(self.derived_vs_operational),
        }


def build_pilot_sample_size_plan(
    *,
    eligible_cluster_population_size: int,
    confidence_level: float = 0.95,
    target_prevalence_half_width: float = 0.07,
    planning_prevalence_scenarios: Sequence[float] = (0.10, 0.25, 0.50, 0.75),
    assumed_baseline_precision_scenarios: Sequence[float] = (0.80,),
    assumed_baseline_recall_scenarios: Sequence[float] = (0.80,),
    metric_half_width: float = 0.10,
    desired_minimum_positive_denominator: Optional[int] = None,
    desired_minimum_negative_denominator: Optional[int] = None,
    planned_calibration_round_size: int = 40,
    planned_double_annotation_count: int = 120,
    expected_unusable_fraction: float = 0.10,
    apply_finite_population_correction: bool = True,
    round_up_to_multiple: int = 10,
    challenge_target_count: int = 100,
    duplicate_candidate_target_count: int = 200,
    duplicate_below_threshold_audit_target_count: int = 100,
) -> PilotSampleSizePlanV1:
    if eligible_cluster_population_size < 0:
        raise ValueError("eligible_cluster_population_size must be >= 0")
    if not 0.0 <= expected_unusable_fraction < 1.0:
        raise ValueError("expected_unusable_fraction must be in [0, 1)")
    if target_prevalence_half_width <= 0 or target_prevalence_half_width > 1:
        raise ValueError("target_prevalence_half_width must be in (0, 1]")

    z = z_for_confidence(confidence_level)
    warnings: list[str] = []

    # Metric denominator planning at ~0.80 success rate by default.
    pos_den = desired_minimum_positive_denominator
    if pos_den is None:
        # Use worst-case among precision/recall scenarios near 0.5 for conservatism
        # when solving for half-width at p=0.80 (Buderer-style denominator).
        pos_den = minimum_n_for_wilson_half_width(0.80, metric_half_width, z=z)
    neg_den = desired_minimum_negative_denominator
    if neg_den is None:
        neg_den = minimum_n_for_wilson_half_width(0.80, metric_half_width, z=z)

    scenario_rows: list[dict[str, Any]] = []
    # Illustration table at p=0.5 for documentation.
    for n in (100, 150, 200, 250, 300, 385):
        scenario_rows.append(
            {
                "sample_size": n,
                "assumed_p": 0.50,
                "wilson_half_width": wilson_half_width_for_p(0.50, n, z=z),
                "role": "planning_illustration_not_guarantee",
            }
        )

    required_by_prev: list[dict[str, Any]] = []
    worst_n = 0
    for q in planning_prevalence_scenarios:
        if not 0.0 < q < 1.0:
            raise ValueError(f"prevalence scenario must be in (0,1), got {q}")
        n_prev = minimum_n_for_wilson_half_width(q, target_prevalence_half_width, z=z)
        n_for_pos = int(math.ceil(pos_den / q))
        n_for_neg = int(math.ceil(neg_den / (1.0 - q)))
        n_need = max(n_prev, n_for_pos, n_for_neg)
        n_fpc = (
            finite_population_corrected_n(n_need, eligible_cluster_population_size)
            if apply_finite_population_correction and eligible_cluster_population_size > 0
            else n_need
        )
        n_with_reserve = int(math.ceil(n_fpc / (1.0 - expected_unusable_fraction)))
        required_by_prev.append(
            {
                "prevalence_scenario": q,
                "n_for_prevalence_half_width": n_prev,
                "n_for_positive_denominator": n_for_pos,
                "n_for_negative_denominator": n_for_neg,
                "n_required_before_reserve": n_need,
                "n_with_finite_population_correction": n_fpc,
                "n_with_unusable_reserve": n_with_reserve,
                "finite_population_correction_applied": (
                    apply_finite_population_correction and eligible_cluster_population_size > 0
                ),
            }
        )
        worst_n = max(worst_n, n_with_reserve)

    operational = round_up_to(worst_n, round_up_to_multiple) if worst_n else 0
    planned_reserve = max(
        0,
        operational
        - max((r["n_with_finite_population_correction"] for r in required_by_prev), default=0),
    )

    can_satisfy = eligible_cluster_population_size >= operational if operational > 0 else False
    underfilled = eligible_cluster_population_size < operational
    if underfilled:
        warnings.append(
            "UNDERFILLED_POPULATION: eligible cluster population cannot satisfy "
            f"operational target {operational} (available={eligible_cluster_population_size}). "
            "Target was not quietly reduced."
        )
    if eligible_cluster_population_size == 0:
        warnings.append("EMPTY_ELIGIBLE_POPULATION")

    assumptions = (
        "Wilson score interval used for unweighted binomial planning (Newcombe 1998).",
        "Prevalence scenarios are planning assumptions, not estimates.",
        "Positive/negative denominator planning follows Buderer (1996) by analogy.",
        "Challenge and calibration counts are operational conveniences unless marked derived.",
        "Finite-population correction applies only to a known finite local corpus.",
        f"z={z} for confidence_level={confidence_level}.",
    )
    formula_notes = (
        "Wilson center = (p̂ + z²/(2n)) / (1 + z²/n)",
        "Wilson half-width = z * sqrt(p̂(1-p̂)/n + z²/(4n²)) / (1 + z²/n)",
        "n solved by binary search on continuous Wilson half-width at assumed p.",
        "n_for_positives ≈ m / q; n_for_negatives ≈ r / (1-q).",
        "n_fpc = ceil(n / (1 + (n-1)/N)) when FPC applied.",
        "n_with_reserve = ceil(n_fpc / (1 - unusable_fraction)).",
    )
    derived_vs_operational = (
        {
            "name": "worst_case_representative_n",
            "kind": "statistically_derived",
            "why": "max over prevalence scenarios of Wilson + denominator + reserve",
        },
        {
            "name": "operational_target_representative",
            "kind": "derived_then_rounded",
            "why": f"round_up_to {round_up_to_multiple} for operational convenience",
        },
        {
            "name": "challenge_target_count",
            "kind": "operational_default",
            "why": "stress-test size; not population inference",
        },
        {
            "name": "planned_calibration_round_size",
            "kind": "operational_default",
            "why": "guideline calibration; excluded from sealed benchmark by default",
        },
        {
            "name": "planned_double_annotation_count",
            "kind": "operational_default_not_universal",
            "why": "Flack/Rotondi motivate precision planning; 120 is not universal",
        },
        {
            "name": "duplicate_candidate_target_count",
            "kind": "operational_default",
            "why": "review workload target for threshold calibration",
        },
    )

    return PilotSampleSizePlanV1(
        confidence_level=confidence_level,
        target_prevalence_half_width=target_prevalence_half_width,
        planning_prevalence_scenarios=tuple(planning_prevalence_scenarios),
        assumed_baseline_precision_scenarios=tuple(assumed_baseline_precision_scenarios),
        assumed_baseline_recall_scenarios=tuple(assumed_baseline_recall_scenarios),
        eligible_cluster_population_size=eligible_cluster_population_size,
        desired_minimum_positive_denominator=pos_den,
        desired_minimum_negative_denominator=neg_den,
        planned_calibration_round_size=planned_calibration_round_size,
        planned_double_annotation_count=planned_double_annotation_count,
        expected_unusable_fraction=expected_unusable_fraction,
        apply_finite_population_correction=apply_finite_population_correction,
        round_up_to_multiple=round_up_to_multiple,
        challenge_target_count=challenge_target_count,
        duplicate_candidate_target_count=duplicate_candidate_target_count,
        duplicate_below_threshold_audit_target_count=(duplicate_below_threshold_audit_target_count),
        scenario_rows=tuple(scenario_rows),
        required_representative_by_prevalence=tuple(required_by_prev),
        worst_case_representative_n=worst_n,
        required_positive_count=pos_den,
        required_negative_count=neg_den,
        planned_reserve=planned_reserve,
        operational_target_representative=operational,
        population_can_satisfy_plan=can_satisfy,
        underfilled=underfilled,
        warnings=tuple(warnings),
        assumptions=assumptions,
        formula_notes=formula_notes,
        derived_vs_operational=derived_vs_operational,
    )


def plan_from_config(
    params: Mapping[str, Any],
    *,
    eligible_cluster_population_size: int,
) -> PilotSampleSizePlanV1:
    design = params.get("pilot_design") or {}
    rep = design.get("representative_sample") or {}
    challenge = design.get("challenge_sample") or {}
    calib = design.get("annotation_calibration") or {}
    formal = design.get("formal_double_annotation") or {}
    dup = design.get("duplicate_pair_review") or {}
    return build_pilot_sample_size_plan(
        eligible_cluster_population_size=eligible_cluster_population_size,
        confidence_level=float(design.get("confidence_level", 0.95)),
        target_prevalence_half_width=float(rep.get("target_prevalence_half_width", 0.07)),
        planning_prevalence_scenarios=tuple(
            float(x) for x in (rep.get("planning_prevalence_scenarios") or (0.10, 0.25, 0.50, 0.75))
        ),
        expected_unusable_fraction=float(rep.get("unusable_record_reserve_fraction", 0.10)),
        round_up_to_multiple=int(rep.get("round_up_to", 10)),
        planned_calibration_round_size=int(calib.get("target_count", 40)),
        planned_double_annotation_count=int(formal.get("target_count", 120)),
        challenge_target_count=int(challenge.get("target_count", 100)),
        duplicate_candidate_target_count=int(dup.get("candidate_target_count", 200)),
        duplicate_below_threshold_audit_target_count=int(
            dup.get("below_threshold_audit_target_count", 100)
        ),
        apply_finite_population_correction=bool(
            design.get("apply_finite_population_correction", True)
        ),
        metric_half_width=float(design.get("metric_denominator_half_width", 0.10)),
    )


def render_design_assumptions_md(plan: PilotSampleSizePlanV1) -> str:
    lines = [
        "# Pilot sample-size design assumptions",
        "",
        f"- Planning version: `{plan.planning_version}`",
        f"- Eligible cluster population N: {plan.eligible_cluster_population_size}",
        f"- Confidence level: {plan.confidence_level}",
        f"- Target prevalence half-width: {plan.target_prevalence_half_width}",
        f"- Worst-case representative n (before rounding): {plan.worst_case_representative_n}",
        f"- Operational target (rounded): {plan.operational_target_representative}",
        f"- Required positive denominator: {plan.required_positive_count}",
        f"- Required negative denominator: {plan.required_negative_count}",
        f"- Planned reserve (approx): {plan.planned_reserve}",
        f"- Population can satisfy plan: {plan.population_can_satisfy_plan}",
        f"- Underfilled: {plan.underfilled}",
        "",
        "## Assumptions",
        "",
    ]
    for a in plan.assumptions:
        lines.append(f"- {a}")
    lines.extend(["", "## Formula notes", ""])
    for f in plan.formula_notes:
        lines.append(f"- {f}")
    lines.extend(["", "## Derived vs operational", ""])
    for row in plan.derived_vs_operational:
        lines.append(f"- `{row['name']}` — **{row['kind']}**: {row['why']}")
    if plan.warnings:
        lines.extend(["", "## Warnings", ""])
        for w in plan.warnings:
            lines.append(f"- {w}")
    lines.append("")
    return "\n".join(lines)

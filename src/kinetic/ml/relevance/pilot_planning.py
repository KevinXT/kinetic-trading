"""Quantitative sample-size planning for the real-corpus relevance pilot.

Uses Wilson score intervals (Newcombe 1998). Does not hardcode “200 is enough.”
Operational defaults are labeled as engineering choices, not universal laws.

Finite-population correction (FPC) applies only to the prevalence-precision
sample-size requirement. Class-denominator requirements (m+/q, m-/(1-q)) are
never FPC-reduced: they are physical counts needed for separate metric
precision and remain infeasible when they exceed the population.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from kinetic.ml.relevance.metrics import wilson_interval

PLANNING_VERSION = "pilot-sample-size-plan-v2"

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
    """FPC-adjusted sample size for proportion planning only.

    n_fpc = ceil(N * n0 / (N + n0 - 1))
    Equivalent to ceil(n0 / (1 + (n0 - 1) / N)).
    """
    if population_size < 1:
        raise ValueError("population_size must be >= 1")
    if n_infinite < 1:
        raise ValueError("n_infinite must be >= 1")
    return int(math.ceil((population_size * n_infinite) / (population_size + n_infinite - 1)))


def round_up_to(n: int, multiple: int) -> int:
    if multiple < 1:
        raise ValueError("round_up_to multiple must be >= 1")
    if n % multiple == 0:
        return n
    return n + (multiple - n % multiple)


def _limiting_requirement(n_prev_fpc: int, n_for_pos: int, n_for_neg: int) -> str:
    m = max(n_prev_fpc, n_for_pos, n_for_neg)
    if n_for_pos == m and n_for_pos >= n_for_neg and n_for_pos >= n_prev_fpc:
        if n_for_pos > n_prev_fpc and n_for_pos > n_for_neg:
            return "positive_denominator"
        if n_for_pos == n_for_neg and n_for_pos > n_prev_fpc:
            return "positive_and_negative_denominator"
    if n_for_neg == m and n_for_neg > n_prev_fpc and n_for_neg > n_for_pos:
        return "negative_denominator"
    if n_prev_fpc == m:
        return "prevalence_precision"
    if n_for_pos == m:
        return "positive_denominator"
    if n_for_neg == m:
        return "negative_denominator"
    return "prevalence_precision"


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
    feasible: bool = False
    underpowered: bool = False
    limiting_requirement: str = "prevalence_precision"
    required_sample_size: int = 0
    available_population_size: int = 0
    fpc_applied_only_to_prevalence_precision: bool = True
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
            "feasible": self.feasible,
            "underpowered": self.underpowered,
            "limiting_requirement": self.limiting_requirement,
            "required_sample_size": self.required_sample_size,
            "available_population_size": self.available_population_size,
            "fpc_applied_only_to_prevalence_precision": (
                self.fpc_applied_only_to_prevalence_precision
            ),
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
    N = eligible_cluster_population_size
    # FPC only when the population is a known finite local corpus.
    use_fpc = bool(apply_finite_population_correction and N > 0)

    # Metric denominator planning at ~0.80 success rate by default.
    pos_den = desired_minimum_positive_denominator
    if pos_den is None:
        pos_den = minimum_n_for_wilson_half_width(0.80, metric_half_width, z=z)
    neg_den = desired_minimum_negative_denominator
    if neg_den is None:
        neg_den = minimum_n_for_wilson_half_width(0.80, metric_half_width, z=z)

    scenario_rows: list[dict[str, Any]] = []
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
    worst_inferential = 0
    worst_operational = 0
    global_limiting = "prevalence_precision"
    any_infeasible = False

    for q in planning_prevalence_scenarios:
        if not 0.0 < q < 1.0:
            raise ValueError(f"prevalence scenario must be in (0,1), got {q}")
        n_prev = minimum_n_for_wilson_half_width(q, target_prevalence_half_width, z=z)
        # FPC may reduce prevalence-precision n only.
        n_prev_fpc = finite_population_corrected_n(n_prev, N) if use_fpc else n_prev
        # Class denominators are physical requirements — never FPC-reduced.
        n_for_pos = int(math.ceil(pos_den / q))
        n_for_neg = int(math.ceil(neg_den / (1.0 - q)))
        n_required = max(n_prev_fpc, n_for_pos, n_for_neg)
        limiting = _limiting_requirement(n_prev_fpc, n_for_pos, n_for_neg)
        expected_pos_census = q * N if N > 0 else None
        expected_neg_census = (1.0 - q) * N if N > 0 else None
        pos_ok_census = expected_pos_census is not None and expected_pos_census + 1e-12 >= pos_den
        neg_ok_census = expected_neg_census is not None and expected_neg_census + 1e-12 >= neg_den
        feasible_scenario = N > 0 and n_required <= N
        if not feasible_scenario and N > 0:
            any_infeasible = True
        # Operational draw allows for unusable records but must not hide
        # inferential infeasibility.
        n_with_reserve = int(math.ceil(n_required / (1.0 - expected_unusable_fraction)))
        operational_draw = min(n_with_reserve, N) if N > 0 else n_with_reserve
        required_by_prev.append(
            {
                "prevalence_scenario": q,
                "prevalence_precision_requirement": n_prev,
                "n_for_prevalence_half_width": n_prev,
                "n_prev_with_fpc": n_prev_fpc,
                "positive_denominator_requirement": n_for_pos,
                "negative_denominator_requirement": n_for_neg,
                "n_for_positive_denominator": n_for_pos,
                "n_for_negative_denominator": n_for_neg,
                "required_sample_size": n_required,
                "n_required_before_reserve": n_required,
                # Backward-compatible key: previously FPC was applied to max(...).
                # Now this equals n_required (FPC already scoped to prevalence only).
                "n_with_finite_population_correction": n_required,
                "n_with_unusable_reserve": n_with_reserve,
                "operational_draw_capped_at_N": operational_draw,
                "finite_population_correction_applied": use_fpc,
                "fpc_applied_only_to_prevalence_precision": True,
                "feasible": feasible_scenario,
                "underpowered": not feasible_scenario,
                "limiting_requirement": limiting,
                "available_population_size": N,
                "expected_positive_count_at_full_census": expected_pos_census,
                "expected_negative_count_at_full_census": expected_neg_census,
                "positive_requirement_satisfied_by_census": pos_ok_census,
                "negative_requirement_satisfied_by_census": neg_ok_census,
            }
        )
        if n_required >= worst_inferential:
            worst_inferential = n_required
            global_limiting = limiting
        worst_operational = max(worst_operational, n_with_reserve)

    # Operational target is rounded for workflow convenience, but may be capped
    # at N while remaining underpowered when inferential n > N.
    operational_uncapped = (
        round_up_to(worst_operational, round_up_to_multiple) if worst_operational else 0
    )
    if N > 0:
        operational = min(operational_uncapped, N)
    else:
        operational = operational_uncapped
    planned_reserve = max(0, worst_operational - worst_inferential)

    feasible = N > 0 and worst_inferential <= N and not any_infeasible
    underpowered = N == 0 or worst_inferential > N or any_infeasible
    underfilled = underpowered
    can_satisfy = feasible

    if underpowered:
        warnings.append(
            "UNDERPOWERED_PLAN: inferential required_sample_size "
            f"{worst_inferential} exceeds available population N={N} "
            f"(limiting_requirement={global_limiting}). "
            "FPC was not applied to positive/negative denominator requirements. "
            "Operational draw may be capped at N while underpowered=true."
        )
        warnings.append(
            "UNDERFILLED_POPULATION: eligible cluster population cannot satisfy "
            f"inferential target {worst_inferential} (available={N}). "
            "Target was not quietly reduced to appear feasible."
        )
    if N == 0:
        warnings.append("EMPTY_ELIGIBLE_POPULATION")
    if N > 0 and N < 50:
        warnings.append(
            "SMALL_SYNTHETIC_OR_LOCAL_POPULATION: software fixtures can exercise "
            "workflow; statistical planning requirements may remain underpowered."
        )

    assumptions = (
        "Wilson score interval used for unweighted binomial planning (Newcombe 1998).",
        "Prevalence scenarios are planning assumptions, not estimates.",
        "Positive/negative denominator planning follows Buderer (1996) by analogy.",
        "Challenge and calibration counts are operational conveniences unless marked derived.",
        "Finite-population correction applies only to prevalence-precision n when N is known.",
        "FPC is never applied to n+ = ceil(m+/q) or n- = ceil(m-/(1-q)).",
        "When population size is unknown or represents future news, FPC is not applied.",
        "Reserve arithmetic cannot make an infeasible inferential requirement appear feasible.",
        f"z={z} for confidence_level={confidence_level}.",
    )
    formula_notes = (
        "Wilson center = (p̂ + z²/(2n)) / (1 + z²/n)",
        "Wilson half-width = z * sqrt(p̂(1-p̂)/n + z²/(4n²)) / (1 + z²/n)",
        "n_prev solved by binary search on continuous Wilson half-width at assumed p.",
        "n_prev_FPC = ceil(N * n_prev / (N + n_prev - 1)) when FPC applied.",
        "n+ = ceil(m+ / q); n- = ceil(m- / (1-q)); FPC not applied to n+ or n-.",
        "n_required = max(n_prev_FPC, n+, n-).",
        "If n_required > N the plan is infeasible/underpowered (not clamped to satisfied).",
        "n_with_reserve = ceil(n_required / (1 - unusable_fraction)).",
        "operational_draw = min(round_up(n_with_reserve), N) while preserving underpowered.",
    )
    derived_vs_operational = (
        {
            "name": "required_sample_size",
            "kind": "statistically_derived",
            "why": "max of FPC prevalence precision and class-denominator requirements",
        },
        {
            "name": "worst_case_representative_n",
            "kind": "statistically_derived",
            "why": "max over prevalence scenarios of n_required after unusable reserve",
        },
        {
            "name": "operational_target_representative",
            "kind": "derived_then_rounded_capped",
            "why": (
                f"round_up_to {round_up_to_multiple}, then cap at N as operational "
                "maximum while underpowered remains true when inferential n > N"
            ),
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
        eligible_cluster_population_size=N,
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
        worst_case_representative_n=worst_operational,
        required_positive_count=pos_den,
        required_negative_count=neg_den,
        planned_reserve=planned_reserve,
        operational_target_representative=operational,
        population_can_satisfy_plan=can_satisfy,
        underfilled=underfilled,
        feasible=feasible,
        underpowered=underpowered,
        limiting_requirement=global_limiting,
        required_sample_size=worst_inferential,
        available_population_size=N,
        fpc_applied_only_to_prevalence_precision=True,
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
        f"- Inferential required sample size: {plan.required_sample_size}",
        f"- Worst-case representative n (with reserve): {plan.worst_case_representative_n}",
        f"- Operational target (rounded, capped at N): {plan.operational_target_representative}",
        f"- Required positive denominator: {plan.required_positive_count}",
        f"- Required negative denominator: {plan.required_negative_count}",
        f"- Limiting requirement: {plan.limiting_requirement}",
        f"- Feasible: {plan.feasible}",
        f"- Underpowered: {plan.underpowered}",
        f"- FPC applied only to prevalence precision: "
        f"{plan.fpc_applied_only_to_prevalence_precision}",
        f"- Planned reserve (approx): {plan.planned_reserve}",
        f"- Population can satisfy plan: {plan.population_can_satisfy_plan}",
        f"- Underfilled (alias of underpowered): {plan.underfilled}",
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

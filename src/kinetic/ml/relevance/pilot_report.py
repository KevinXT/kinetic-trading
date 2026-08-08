"""Pilot readiness and quantitative report rendering."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from kinetic.ml.relevance.pilot_models import READINESS_STATUSES

PILOT_LIMITATIONS = """# Pilot limitations

- The local corpus may not represent future news.
- Rights-cleared sources may introduce publisher bias.
- Language restrictions limit generalization.
- Duplicate recall is bounded to the comparison universe.
- Near-duplicate threshold remains a design choice.
- Annotation disagreement may reflect legitimate ambiguity.
- Pilot sample sizes produce uncertainty.
- Deterministic baseline results do not imply model performance.
- No sentiment or market-impact analysis exists.
- No predictive return exists.
- No causal claim exists.
- No production trading use exists.
- Synthetic fixtures are for software validation only and are not real pilot findings.
"""


def build_readiness_decision(
    *,
    evidence: Mapping[str, Any],
    human_approval: Optional[str],
    human_reason: Optional[str] = None,
) -> dict[str, Any]:
    missing = [k for k, v in evidence.items() if v in (None, False, "missing", "incomplete")]
    available = [k for k, v in evidence.items() if v not in (None, False, "missing", "incomplete")]

    if human_approval == "APPROVE_FINAL_BENCHMARK_CONSTRUCTION":
        status = "READY_FOR_FINAL_BENCHMARK_CONSTRUCTION"
    elif available and not (
        evidence.get("rights_complete")
        and evidence.get("sample_plan_complete")
        and evidence.get("representative_sample_complete")
    ):
        status = "NOT_READY"
    else:
        status = "REVIEW_REQUIRED"

    if status == "READY_FOR_FINAL_BENCHMARK_CONSTRUCTION":
        if human_approval != "APPROVE_FINAL_BENCHMARK_CONSTRUCTION":
            status = "REVIEW_REQUIRED"

    if status not in READINESS_STATUSES:
        raise ValueError(status)

    return {
        "decision_version": "pilot-readiness-decision-v1",
        "status": status,
        "evidence_available": available,
        "evidence_missing": missing,
        "evidence": dict(evidence),
        "human_decision": human_approval,
        "human_reason": human_reason,
        "notes": [
            "Readiness does not mean ready for production trading.",
            "No automatic universal statistical pass threshold is applied.",
            "READY_FOR_FINAL_BENCHMARK_CONSTRUCTION requires exact typed human gate.",
        ],
    }


def render_quantitative_report(
    *,
    state: str,
    plan: Mapping[str, Any],
    eligibility_counts: Mapping[str, Any],
    representative_manifest: Mapping[str, Any],
    challenge_manifest: Mapping[str, Any],
    agreement: Optional[Mapping[str, Any]],
    baseline_rep: Optional[Mapping[str, Any]],
    baseline_chal: Optional[Mapping[str, Any]],
    duplicate_blocking: Optional[Mapping[str, Any]],
    readiness: Mapping[str, Any],
    execution_status: str,
) -> str:
    lines = [
        "# Semiconductor relevance real-corpus pilot — quantitative report",
        "",
        f"- Pilot state: `{state}`",
        f"- Execution status: `{execution_status}`",
        f"- Readiness: `{readiness.get('status')}`",
        "",
        "## Unsupported claims",
        "",
        "This report does **not** demonstrate a usable classifier, market signal,",
        "predictive return, causal relationship, or trading edge.",
        "",
        "## Sample-size plan",
        "",
        f"- Eligible population N: {plan.get('eligible_cluster_population_size')}",
        f"- Operational representative target: {plan.get('operational_target_representative')}",
        f"- Worst-case derived n: {plan.get('worst_case_representative_n')}",
        f"- Underfilled: {plan.get('underfilled')}",
        f"- Underpowered: {plan.get('underpowered')}",
        f"- Feasible: {plan.get('feasible')}",
        f"- Required sample size (inferential): {plan.get('required_sample_size')}",
        f"- Limiting requirement: {plan.get('limiting_requirement')}",
        f"- FPC applied only to prevalence precision: "
        f"{plan.get('fpc_applied_only_to_prevalence_precision')}",
        f"- Population can satisfy plan: {plan.get('population_can_satisfy_plan')}",
        "",
        "Software fixtures can exercise workflow even when the statistical planning",
        "requirement is underpowered for the available eligible population.",
        "",
        "## Corpus rights and eligibility",
        "",
        f"- Schema accepted: {eligibility_counts.get('schema_valid')}",
        f"- Rights eligible: {eligibility_counts.get('rights_eligible')}",
        f"- Corpus eligible: {eligibility_counts.get('corpus_eligible')}",
        f"- Sampling frame units: {eligibility_counts.get('sampling_frame_units')}",
        "",
        "Private article bodies are not reproduced in this report.",
        "",
        "## Sampling",
        "",
        f"- Representative actual n: {representative_manifest.get('actual_n')}",
        f"- Challenge actual n: {challenge_manifest.get('actual_n')}",
        f"- Probability sample: {representative_manifest.get('probability_sample')}",
        "",
    ]
    if agreement:
        kappa = agreement.get("linearly_weighted_cohen_kappa") or {}
        lines.extend(
            [
                "## Annotation agreement",
                "",
                f"- Jointly labeled clusters: {agreement.get('n_jointly_labeled_clusters')}",
                f"- Exact agreement: {agreement.get('raw_exact_agreement')}",
                f"- Adjacent agreement: {agreement.get('adjacent_agreement')}",
                f"- Large-disagreement rate: {agreement.get('large_disagreement_rate')}",
                f"- Weighted kappa: {kappa.get('kappa')}",
                f"- Kappa CI: {kappa.get('kappa_ci')}",
                "",
            ]
        )
    if baseline_rep:
        lines.extend(["## Deterministic baselines (representative)", ""])
        for bid, block in sorted((baseline_rep.get("baselines") or {}).items()):
            lines.append(
                f"- `{bid}`: precision={block.get('precision', block.get('precision_w'))}, "
                f"recall={block.get('recall', block.get('recall_w'))}, "
                f"f1={block.get('f1', block.get('f1_w'))}, "
                f"coverage={block.get('coverage')}, estimator={block.get('estimator')}"
            )
        lines.append("")
    if baseline_chal:
        lines.extend(
            [
                "## Deterministic baselines (challenge; not population-representative)",
                "",
            ]
        )
        for bid, block in sorted((baseline_chal.get("baselines") or {}).items()):
            lines.append(
                f"- `{bid}`: precision={block.get('precision')}, recall={block.get('recall')}, "
                f"f1={block.get('f1')}, coverage={block.get('coverage')}"
            )
        lines.append("")
    if duplicate_blocking:
        lines.extend(
            [
                "## Duplicate calibration",
                "",
                f"- Blocking universe pair count: {duplicate_blocking.get('pair_count')}",
                "- Threshold recommendation: `HUMAN_REVIEW_REQUIRED`",
                "",
            ]
        )
    lines.extend(
        [
            "## Final decision",
            "",
            f"`{readiness.get('status')}`",
            "",
            f"Human gate: `{readiness.get('human_decision')}`",
            "",
        ]
    )
    return "\n".join(lines)

"""Human-readable benchmark report and limitations."""

from __future__ import annotations

from typing import Any, Mapping

LIMITATIONS = """# Semiconductor relevance benchmark — limitations

This phase constructs benchmark infrastructure. It does **not** implement or
validate a language model, sentiment model, trading signal, or market edge.

- No live article-content provider is implemented; offline/local JSONL only.
- The entity registry is a small semiconductor fixture, not a complete industry
  universe. SEC ticker/CIK fields are stubs with provenance, not a live download.
- Exact deduplication uses strong deterministic evidence; near-duplicate
  similarity thresholds are candidate-generation engineering choices, not
  scientifically validated duplicate thresholds.
- No production human-labeled corpus yet; fixture labels are synthetic ground
  truth for offline tests only.
- Title-based run-scoped dedupe in the DOC pipeline remains separate from this
  research-layer exact/near duplicate analysis.
- Different publisher domains are not assumed to be independent journalism.
- GDELT theme co-occurrence is not semantic relevance; themes were rejected as
  a primary semiconductor identity layer in the prior experiment.
- Publication, ingestion, and retrieval timestamps are distinct; none proves the
  exact historical time a live trading system could have consumed the article.
- Wilson confidence intervals do not prove generalization beyond the selected
  time period, publishers, companies, guidelines, and available fields.
- Annotator agreement may be descriptive when the double-labeled sample is small.
- No sentiment model, no predictive-return evidence, no causal claim.
- No trading system consumes these outputs.
"""


def render_benchmark_report(
    *,
    title: str,
    sampling_manifest: Mapping[str, Any],
    split_manifest: Mapping[str, Any],
    contamination: Mapping[str, Any],
    metrics: Mapping[str, Any] | None,
    agreement: Mapping[str, Any] | None,
    generated_at: str,
) -> str:
    lines = [
        f"# {title}",
        "",
        f"Generated at: {generated_at}",
        "",
        "> This phase constructs benchmark infrastructure. It does not implement",
        "> or validate a language model, sentiment model, trading signal, or",
        "> market edge.",
        "",
        "## Sampling",
        "",
        f"- Eligible clusters/articles: {sampling_manifest.get('eligible_article_count')}",
        f"- Exact clusters: {sampling_manifest.get('exact_cluster_count')}",
        f"- Requested sample: {sampling_manifest.get('requested_sample_count')}",
        f"- Actual sample: {sampling_manifest.get('actual_sample_count')}",
        f"- Sampling seed/version: {sampling_manifest.get('sampling_seed')} / "
        f"{sampling_manifest.get('sampling_version')}",
        f"- Underfilled strata: {sampling_manifest.get('underfilled_strata')}",
        "",
        "## Splits",
        "",
        f"- Development end: {split_manifest.get('development_end')}",
        f"- Validation end: {split_manifest.get('validation_end')}",
        f"- Holdout end: {split_manifest.get('holdout_end')}",
        f"- Embargo days: {split_manifest.get('embargo_days')} (no hidden default)",
        f"- Holdout metrics enabled: {split_manifest.get('holdout_metrics_enabled')}",
        f"- Assignments by split: {split_manifest.get('counts_by_split')}",
        "",
        "## Contamination checks",
        "",
        f"- Exact cluster cross-split count: "
        f"{contamination.get('exact_cluster_cross_split_count')}",
        f"- Near-duplicate cross-split warnings: "
        f"{contamination.get('near_duplicate_cross_split_count')}",
        f"- Chronology ok: {contamination.get('chronology_ok')}",
        f"- Report ok: {contamination.get('ok')}",
        "",
    ]
    if agreement is not None:
        kappa = agreement.get("linearly_weighted_cohen_kappa", {})
        lines.extend(
            [
                "## Annotation agreement",
                "",
                f"- Jointly labeled: {agreement.get('n_jointly_labeled_articles')}",
                f"- Raw percent agreement: {agreement.get('raw_percent_agreement')}",
                f"- Linearly weighted Cohen's kappa: {kappa.get('kappa')}",
                f"- Kappa reason/warning: {kappa.get('reason') or agreement.get('warning')}",
                "",
            ]
        )
    if metrics is not None:
        lines.extend(["## Baseline metrics (adjudicated labels)", ""])
        for baseline_id, splits in sorted(metrics.get("baselines", {}).items()):
            lines.append(f"### {baseline_id}")
            lines.append("")
            for split, block in sorted(splits.items()):
                lines.append(
                    f"- **{split}**: TP={block['tp']} FP={block['fp']} "
                    f"TN={block['tn']} FN={block['fn']} abstentions={block['abstentions']}; "
                    f"precision={block['precision']} recall={block['recall']} "
                    f"f1={block['f1']} coverage={block['coverage']}"
                )
            lines.append("")
    lines.extend(
        [
            "## Unsupported claims",
            "",
            "- No text classifier or sentiment model is implemented.",
            "- Fixture metrics are not real-world model performance.",
            "- No predictive return or causal effect is demonstrated.",
            "",
        ]
    )
    return "\n".join(lines)

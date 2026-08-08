"""Human-readable report and summary JSON for seeded theme scoring."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from kinetic.ingestion.news.gdelt.bigquery.queries import SCORING_QUERY_BUILDER_VERSION
from kinetic.processing.news.themes.classification import CATEGORIES

from .constants import (
    ART_CONCENTRATION_CSV,
    ART_DECISION,
    ART_ESTIMATE,
    ART_EVIDENCE_CSV,
    ART_EVIDENCE_JSONL,
    ART_REPORT_MD,
    ART_REVIEW_CSV,
    ART_SCORES_CSV,
    ART_SCORES_JSONL,
    ART_SEED_DIAG_CSV,
    ART_SEED_DIAG_JSONL,
    ART_SQL,
    ART_STABILITY_CSV,
    ART_SUMMARY,
    MATCHING_MODE,
    SCREEN_MAX_TOP_DAY_SHARE,
    SCREEN_MAX_TOP_SEED_SHARE,
    SCREEN_MAX_TOP_SOURCE_SHARE,
    SCREEN_MIN_LIFT,
    SCREEN_MIN_PERIODS,
    SCREEN_MIN_SUPPORT,
)
from .util import _diff_or_none, _test_distribution

JsonDict = Dict[str, Any]


def _write_report(
    ctx,
    *,
    scored: List[JsonDict],
    seed_diag: List[JsonDict],
    denominators: Tuple[Optional[int], Optional[int]],
    meta: JsonDict,
    n_weeks: int,
    topic: str,
    start_str: str,
    end_str: str,
    organization_seeded_only: bool,
    limit: int,
) -> None:
    total_seed, total_bg = denominators
    n = len(scored)

    def count(pred) -> int:
        return sum(1 for r in scored if pred(r))

    # Lift bands use the DISJOINT non-seed inferential lift.
    lift_vals = [
        r["lift_vs_nonseed_background"]
        for r in scored
        if isinstance(r["lift_vs_nonseed_background"], (int, float))
    ]
    lift_buckets = {
        "<1": sum(1 for x in lift_vals if x < 1),
        "1-2": sum(1 for x in lift_vals if 1 <= x < 2),
        "2-3": sum(1 for x in lift_vals if 2 <= x < 3),
        ">=3": sum(1 for x in lift_vals if x >= 3),
    }
    screened = [r for r in scored if r["screening_pass"]]
    non_generic_screened = [r for r in screened if not r["generic_noise_flag"]]

    # Full classification distribution — every category, including explicit zeros.
    class_counts = {cat: 0 for cat in sorted(CATEGORIES)}
    for r in scored:
        class_counts[r["classification"]] = class_counts.get(r["classification"], 0) + 1

    top_stat = sorted(scored, key=lambda r: r["raw_statistical_rank"])[:20]
    top_adj = sorted(scored, key=lambda r: r["research_adjusted_rank"])[:20]

    lines: List[str] = []
    lines.append(f"# Research quality report — {topic} seeded theme scoring")
    lines.append("")
    lines.append(
        f"Window `{start_str}`–`{end_str}` ({n_weeks} weekly buckets). "
        "Human-review candidate screening only — **no candidate is approved and "
        "no theme bundle is modified by this task.** All claims below are bounded "
        "to this window, this organization-seeded corpus, and the queried GKG "
        "fields; they do not generalize to the full GDELT taxonomy or other "
        "periods."
    )
    lines.append("")
    lines.append("## Candidate family, completeness & multiple testing")
    lines.append("")
    complete = bool(meta.get("family_complete"))
    lines.append(
        f"- Total support-qualified candidates (independent count): "
        f"`{meta.get('total_support_qualified_candidates')}`"
    )
    lines.append(
        f"- Candidate rows returned before presentation limit: "
        f"`{meta.get('candidate_rows_returned_before_presentation_limit')}`"
    )
    lines.append(f"- Candidate family safety cap: `{meta.get('candidate_family_safety_cap')}`")
    lines.append(f"- Family cap reached: `{meta.get('family_cap_reached')}`")
    lines.append(f"- Family complete: `{meta.get('family_complete')}`")
    lines.append(
        f"- **Statistical completion status: `{meta.get('statistical_completion_status')}`**"
    )
    lines.append(
        f"- BH skipped due to incomplete family: "
        f"`{meta.get('bh_skipped_due_to_incomplete_family')}`"
    )
    lines.append(
        f"- Valid hypothesis-test count (defined p-values): "
        f"`{meta.get('valid_hypothesis_test_count')}`"
    )
    lines.append(
        f"- Invalid / unavailable test count: `{meta.get('invalid_or_unavailable_test_count')}`"
    )
    lines.append(
        f"- Multiple-testing method: `{meta.get('multiple_testing_method')}`; "
        f"BH family size (= hypotheses corrected): `{meta.get('multiple_testing_family_size')}`"
    )
    lines.append(
        f"- BH applied over the complete family before any presentation limit: "
        f"`{meta.get('multiple_testing_applied_before_presentation_limit')}`"
    )
    lines.append(
        f"- Presentation limit (review CSV / tables below): `{limit}` "
        "(the full family is in `theme_candidate_scores.{csv,jsonl}`)."
    )
    if not complete:
        lines.append("")
        lines.append(
            "> **FAIL-CLOSED:** the support-qualified family is not proven "
            "complete, so Benjamini-Hochberg was skipped, every `adjusted_p_value` "
            "is null, and **no scientific verdict is issued**. Raise "
            "`candidate_limit` above the independent qualified count and re-run."
        )
    lines.append("")
    lines.append("## Corpus & denominators")
    lines.append("")
    lines.append(f"- Total seeded records (N1): `{total_seed}`")
    lines.append(f"- Total whole-corpus records (descriptive denominator): `{total_bg}`")
    nonseed_total = _diff_or_none(total_bg, total_seed)
    lines.append(f"- Total non-seed records (inferential denominator N0): `{nonseed_total}`")
    lines.append(f"- Candidate themes scored (>= min support): `{n}`")
    lines.append(f"- Total record-seed matches: `{meta.get('total_record_seed_matches')}`")
    lines.append(
        f"- Multi-seed records: `{meta.get('multi_seed_record_count')}` "
        f"(share of seed records: `{meta.get('multi_seed_record_share')}`)"
    )
    if organization_seeded_only:
        lines.append(
            "- **Corpus scope:** organization-seeded only (seeds matched against "
            "`V2Organizations`). Industry *phrases* are matched in the same field, "
            "so phrase-only records that never name a company in `V2Organizations` "
            "are under-represented. This is a **primarily organization-seeded "
            "corpus**, not a full semiconductor-topic corpus."
        )
    lines.append("")
    lines.append("## Statistical methodology")
    lines.append("")
    lines.append(
        "- **Descriptive** metrics (`*_vs_all_corpus`) compare the seeded corpus "
        "against *all* same-window records (seeds INCLUDED — nested groups); use "
        "them only for whole-corpus prevalence context."
    )
    lines.append(
        "- **Inferential** metrics (`*_vs_nonseed_background`, CI, p-value, "
        "adjusted p-value) use a DISJOINT 2x2: seeded records vs non-seed records "
        "(`c = all_corpus_theme_count - seed_record_count`, "
        "`N0 = total_corpus - total_seed`). Haldane–Anscombe 0.5 smoothing keeps "
        "odds ratios finite; honest nulls where a cell is undefined."
    )
    lines.append(
        "- **Sparse-aware test selection:** for each candidate the disjoint 2x2 "
        f"table's expected cell counts are computed; a pooled two-proportion "
        f"z-test is used only when every expected cell is >= "
        f"`{meta.get('expected_cell_threshold')}`, otherwise a two-sided **Fisher "
        "exact test** (exact hypergeometric sum; no external dependency). Invalid "
        "tables emit a null p-value and are excluded from BH — never coerced to 1."
    )
    lines.append(
        "- The smoothed log-lift confidence interval is a SEPARATE **approximate "
        "screening interval** (log-ratio delta method, Haldane-Anscombe 0.5, "
        "asymptotic — not exact coverage); it is intentionally unchanged by the "
        "Fisher p-value selection."
    )
    lines.append(
        "- These are **screening approximations**: GKG records are not "
        "independent (syndication/republication, source and event clustering, "
        "theme co-occurrence), so ordinary CIs/p-values and BH-FDR understate "
        "dependence. Statistical significance does **not** establish "
        "semiconductor identity."
    )
    lines.append("")
    # Hypothesis-test selection breakdown.
    test_counts: Dict[str, int] = {}
    for r in scored:
        name = str(r.get("hypothesis_test") or "unknown")
        test_counts[name] = test_counts.get(name, 0) + 1
    sig = count(
        lambda r: isinstance(r.get("adjusted_p_value"), (int, float))
        and r["adjusted_p_value"] < 0.05
    )
    lines.append("## Hypothesis-test selection")
    lines.append("")
    for name in sorted(test_counts):
        lines.append(f"- `{name}`: `{test_counts[name]}`")
    lines.append(f"- Candidates with adjusted p-value < 0.05 (if complete): `{sig}`")
    lines.append("")
    lines.append("## Concentration coverage (exact vs approximate)")
    lines.append("")
    lines.append(
        f"- Exact source concentration: "
        f"`{meta.get('candidates_with_exact_source_concentration')}` / "
        f"`{meta.get('total_candidate_count')}`"
    )
    lines.append(
        f"- Exact day concentration: "
        f"`{meta.get('candidates_with_exact_day_concentration')}` / "
        f"`{meta.get('total_candidate_count')}`"
    )
    lines.append(
        f"- Exact seed concentration: "
        f"`{meta.get('candidates_with_exact_seed_concentration')}` / "
        f"`{meta.get('total_candidate_count')}`"
    )
    lines.append(
        f"- Screened using approximate source fallback: "
        f"`{count(lambda r: r.get('top_source_share_provenance') == 'approximate')}`"
    )
    lines.append(f"- Selection rule: {meta.get('exact_concentration_selection_rule')}")
    lines.append("")
    lines.append("## Lift distribution (non-seed inferential lift)")
    lines.append("")
    for k, v in lift_buckets.items():
        lines.append(f"- lift {k}: `{v}`")
    lines.append("")
    lines.append("## Classification distribution (all categories)")
    lines.append("")
    for cat in sorted(CATEGORIES):
        lines.append(f"- {cat}: `{class_counts[cat]}`")
    lines.append("")
    lines.append(
        f"Classification is produced by a **deterministic rule set**, not an "
        f"ontology. `industry_core = {class_counts.get('industry_core', 0)}` means "
        f"none of the `{n}` support-qualified candidates was classified as "
        "`industry_core` **by the current rules** — it does not establish that no "
        "semiconductor-specific GDELT theme exists outside this candidate family, "
        "date window, support threshold, queried fields, or rule set."
    )
    lines.append("")
    lines.append("## Screening (advisory thresholds, not gates)")
    lines.append("")
    lines.append(
        f"Thresholds: support >= {SCREEN_MIN_SUPPORT}, non-seed lift >= "
        f"{SCREEN_MIN_LIFT}, periods_present >= {SCREEN_MIN_PERIODS}, EXACT top-day "
        f"share < {SCREEN_MAX_TOP_DAY_SHARE}, EXACT top-source share < "
        f"{SCREEN_MAX_TOP_SOURCE_SHARE}, more than one matched seed, top-seed share "
        f"< {SCREEN_MAX_TOP_SEED_SHARE}, not generic."
    )
    lines.append("")
    lines.append(f"- Candidates passing all screening criteria: `{len(screened)}`")
    lines.append(f"- Non-generic candidates passing screening: `{len(non_generic_screened)}`")
    lines.append(
        "- Candidates whose concentration decision changed after exact validation: "
        f"`{count(lambda r: r['concentration_pass_changed_after_exact_check'])}`"
    )
    lines.append("")
    lines.append(f"## Top {min(20, limit)} by raw statistical ranking (non-seed log-lift)")
    lines.append("")
    lines.append("| rank | theme | lift | log-lift | support | periods | adj p | class |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in top_stat[:limit]:
        lines.append(
            f"| {r['raw_statistical_rank']} | {r['theme']} | "
            f"{r['lift_vs_nonseed_background']} | "
            f"{r['smoothed_log_lift_vs_nonseed_background']} | {r['seed_record_count']} | "
            f"{r['periods_present']}/{n_weeks} | {r['adjusted_p_value']} | "
            f"{r['classification']} |"
        )
    lines.append("")
    lines.append(f"## Top {min(20, limit)} by research-adjusted ranking (composite)")
    lines.append("")
    lines.append("| rank | theme | composite | lift | support | class | priority | screen |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in top_adj[:limit]:
        lines.append(
            f"| {r['research_adjusted_rank']} | {r['theme']} | {r['composite_score']} | "
            f"{r['lift_vs_nonseed_background']} | {r['seed_record_count']} | "
            f"{r['classification']} | {r['manual_review_priority']} | "
            f"{'PASS' if r['screening_pass'] else '-'} |"
        )
    lines.append("")
    lines.append("## Seed-composition diagnostics (two explicit denominators)")
    lines.append("")
    lines.append(
        "`share_of_unique_seed_records` uses total seed records as denominator and "
        "**overlaps** (a record can match several entities — it does NOT sum to 1). "
        "`share_of_record_seed_matches` uses total record-seed matches and sums to ~1."
    )
    lines.append("")
    lines.append(
        "| canonical seed | kind | unique records | share of unique seed records | "
        "share of record-seed matches |"
    )
    lines.append("|---|---|---|---|---|")
    for d in seed_diag:
        lines.append(
            f"| {d['canonical_seed']} | {d['seed_kind']} | "
            f"{d['unique_seed_records_matching_entity']} | "
            f"{d['share_of_unique_seed_records_matching_entity']} | "
            f"{d['share_of_all_record_seed_matches']} |"
        )
    lines.append("")
    lines.append("## Representative record-level evidence (deterministic hash sample)")
    lines.append("")
    lines.append(
        "Records sampled by FARM_FINGERPRINT of GKGRECORDID (deterministic). Each "
        "row carries the GKG `DocumentIdentifier` (source URL) so it can be "
        "independently inspected; see `candidate_representative_evidence.{csv,jsonl}` "
        "for the full set. **The sampled record metadata and document identifiers "
        "indicate multi-entity and contextual co-occurrence. Because article "
        "bodies were not retrieved, semantic interpretation remains bounded to the "
        "available GKG metadata.** A human reviewer fills the interpretation columns."
    )
    lines.append("")
    lines.append(
        "Two structural patterns are visible in the sampled metadata. First, "
        "individual GKG records carry multiple themes, demonstrating substantial "
        "theme co-occurrence (GDELT themes are multi-label annotations). Second, "
        "similar document identifiers or paths across publishers are consistent "
        "with probable syndication or mirroring. Because article bodies and "
        "canonical content hashes were not collected, underlying-article "
        "duplication cannot be measured exactly."
    )
    lines.append("")
    lines.append(
        "| theme | record id | date | source | doc identifier avail. | "
        "matched entities | limitations |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    shown = 0
    for r in top_adj:
        if shown >= min(12, max(10, limit)):
            break
        recs = r.get("sample_records") or []
        if not recs:
            continue
        rec = recs[0]
        ents = ";".join(rec.get("matched_seeds") or []) or "-"
        avail = (
            "url"
            if (rec.get("doc_id") or "").startswith("http")
            else ("present" if rec.get("doc_id") else "null")
        )
        lines.append(
            f"| {r['theme']} | {rec.get('rec_id')} | {rec.get('day')} | "
            f"{rec.get('source')} | {avail} | {ents} | metadata-only |"
        )
        shown += 1
    lines.append("")
    lines.append("## Confounding & limitations")
    lines.append("")
    lines.append(
        "- The whole-corpus background is *all* GKG records in the window: "
        "business-news concentration, English-language and Western-publisher skew, "
        "company popularity, and syndication all confound the descriptive lift."
    )
    lines.append(
        "- **Theme co-occurrence, not record/article duplication.** A single "
        "GKGRECORDID carrying several theme codes demonstrates that GDELT themes "
        "are multi-label annotations (often broad or overlapping); it is *not* "
        "evidence of duplicate GKG records or duplicate underlying articles."
    )
    lines.append(
        "- **Probable syndication or mirroring (not proven).** Similar document "
        "identifiers or URL paths appearing under multiple publishers/domains are "
        "*consistent with* probable syndication or mirroring. Because article "
        "bodies and canonical content hashes were not collected, underlying-article "
        "duplication cannot be measured exactly; the counts are GKG records, not "
        "distinct articles, and article-level uniqueness is not claimed."
    )
    lines.append(
        "- Classification is a lexical aid, not ground truth; no predictive-return "
        "validation is performed here, and no causal or predictive claim is made."
    )
    lines.append(
        "- Absence of a semiconductor-specific theme among these support-qualified "
        "candidates does **not** prove none exists below the support threshold or "
        "in other windows/taxonomies."
    )
    lines.append("")
    (ctx.artifacts_dir / ART_REPORT_MD).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _summary(
    *,
    query_name: str,
    topic: str,
    table: str,
    start_str: str,
    end_str: str,
    dry_run: bool,
    estimate,
    maximum_bytes_billed: int,
    decision: str,
    cache_hit: bool,
    scored: List[JsonDict],
    seed_diag: List[JsonDict],
    denominators: Tuple[Optional[int], Optional[int]],
    meta: JsonDict,
    n_weeks: int,
    min_support: int,
    limit: int,
    candidate_limit: int,
    organization_seeded_only: bool,
    note: str,
    warnings: List[str],
) -> JsonDict:
    lift_vals = [r.get("lift_vs_nonseed_background") for r in scored]
    with_lift = sum(1 for x in lift_vals if x is not None)
    generic = sum(1 for r in scored if r.get("generic_noise_flag"))
    screened = sum(1 for r in scored if r.get("screening_pass"))
    total_seed, total_bg = denominators
    lift_ge3 = sum(1 for x in lift_vals if isinstance(x, (int, float)) and x >= 3)
    # Full classification distribution, including explicit zeros.
    class_counts = {cat: 0 for cat in sorted(CATEGORIES)}
    for r in scored:
        cat = r.get("classification")
        if cat:
            class_counts[cat] = class_counts.get(cat, 0) + 1
    return {
        "query_name": query_name,
        "topic": topic,
        "table": table,
        "source_table": table,
        "start_date": start_str,
        "end_date": end_str,
        "window": {"start": start_str, "end": end_str},
        "weekly_buckets": n_weeks,
        "dry_run": dry_run,
        "matching_mode": MATCHING_MODE,
        "scoring_builder_version": SCORING_QUERY_BUILDER_VERSION,
        "organization_seeded_only": organization_seeded_only,
        "min_support": min_support,
        "limit": limit,
        "candidate_limit": candidate_limit,
        # Candidate-family completeness / fail-closed cap (Phase 4).
        "total_support_qualified_candidates": meta.get("total_support_qualified_candidates"),
        "scored_candidate_count": meta.get("scored_candidate_count"),
        "candidate_rows_returned": meta.get("candidate_rows_returned"),
        "candidate_rows_returned_before_presentation_limit": meta.get(
            "candidate_rows_returned_before_presentation_limit"
        ),
        "sentinel_row_count": meta.get("sentinel_row_count"),
        "configured_candidate_limit": meta.get("configured_candidate_limit"),
        "candidate_family_safety_cap": meta.get("candidate_family_safety_cap"),
        "family_cap_reached": meta.get("family_cap_reached"),
        "family_complete": meta.get("family_complete"),
        "statistical_completion_status": meta.get("statistical_completion_status"),
        "bh_skipped_due_to_incomplete_family": meta.get("bh_skipped_due_to_incomplete_family"),
        "result_truncated": meta.get("result_truncated"),
        # Multiple-testing / hypothesis-test selection (Phase 3).
        "valid_hypothesis_test_count": meta.get("valid_hypothesis_test_count"),
        "invalid_or_unavailable_test_count": meta.get("invalid_or_unavailable_test_count"),
        "multiple_testing_method": meta.get("multiple_testing_method"),
        "multiple_testing_family_size": meta.get("multiple_testing_family_size"),
        "multiple_testing_applied_before_limit": meta.get("multiple_testing_applied_before_limit"),
        "multiple_testing_applied_before_presentation_limit": meta.get(
            "multiple_testing_applied_before_presentation_limit"
        ),
        "expected_cell_threshold": meta.get("expected_cell_threshold"),
        "fisher_exact_available": meta.get("fisher_exact_available"),
        "hypothesis_test_distribution": _test_distribution(scored),
        # Concentration coverage (Phase 5).
        "total_candidate_count": meta.get("total_candidate_count"),
        "candidates_with_approx_source_concentration": meta.get(
            "candidates_with_approx_source_concentration"
        ),
        "candidates_with_exact_source_concentration": meta.get(
            "candidates_with_exact_source_concentration"
        ),
        "candidates_with_approx_day_concentration": meta.get(
            "candidates_with_approx_day_concentration"
        ),
        "candidates_with_exact_day_concentration": meta.get(
            "candidates_with_exact_day_concentration"
        ),
        "candidates_with_approx_seed_concentration": meta.get(
            "candidates_with_approx_seed_concentration"
        ),
        "candidates_with_exact_seed_concentration": meta.get(
            "candidates_with_exact_seed_concentration"
        ),
        "source_screen_uses_exact_when_available": meta.get(
            "source_screen_uses_exact_when_available"
        ),
        "day_screen_uses_exact_when_available": meta.get("day_screen_uses_exact_when_available"),
        "seed_screen_uses_exact_when_available": meta.get("seed_screen_uses_exact_when_available"),
        "exact_concentration_selection_rule": meta.get("exact_concentration_selection_rule"),
        # Seed-composition provenance (Phase 5).
        "total_record_seed_matches": meta.get("total_record_seed_matches"),
        "multi_seed_record_count": meta.get("multi_seed_record_count"),
        "multi_seed_record_share": meta.get("multi_seed_record_share"),
        "total_seed_records": total_seed,
        "total_background_records": total_bg,
        "total_nonseed_background_records": _diff_or_none(total_bg, total_seed),
        "estimated_bytes": estimate.total_bytes_processed if estimate else None,
        "estimated_gib": round(estimate.estimated_gib, 4) if estimate else None,
        "estimated_cost_usd": (round(estimate.estimated_cost_usd, 8) if estimate else None),
        "maximum_bytes_billed": maximum_bytes_billed,
        "decision": decision,
        "cache_hit": cache_hit,
        "row_count": len(scored),
        "candidates_with_lift": with_lift,
        "candidates_lift_ge_3": lift_ge3,
        "candidates_passing_screening": screened,
        "generic_noise_flagged": generic,
        "classification_distribution": class_counts,
        "limit_reached": bool(meta.get("result_truncated")),
        # Scoring never auto-selects or modifies the bundle.
        "selected_theme_count": 0,
        "bundle_modified": False,
        "seed_diagnostics_count": len(seed_diag),
        "warnings": list(warnings),
        "output_artifacts": [
            ART_SQL,
            ART_ESTIMATE,
            ART_DECISION,
            ART_SUMMARY,
            ART_SCORES_JSONL,
            ART_SCORES_CSV,
            ART_REVIEW_CSV,
            ART_SEED_DIAG_CSV,
            ART_SEED_DIAG_JSONL,
            ART_STABILITY_CSV,
            ART_CONCENTRATION_CSV,
            ART_EVIDENCE_CSV,
            ART_EVIDENCE_JSONL,
            ART_REPORT_MD,
        ],
        "note": note,
    }

"""
Pipeline task: single-scan seeded-vs-background GDELT theme *scoring*.

Seeded discovery ranks themes by raw co-occurrence frequency, which is dominated
by ubiquitous generic themes. This task adds the missing denominator: in one
BigQuery scan it counts, per theme, both the seeded unique-record count and the
*background* unique-record count over the same window, so association metrics
(lift, smoothed log-lift, risk difference, odds ratio, a lift confidence
interval, and a two-proportion p-value with Benjamini-Hochberg FDR correction)
are computable.

The heavy statistics are computed **offline** from the returned contingency
counts by :mod:`news_data.bigquery.theme_scoring`; each candidate is also
classified for review by :mod:`news_data.bigquery.theme_classification`. Two
independent rankings are emitted — a raw *statistical* ranking (by smoothed
log-lift) and a *research-adjusted* ranking (by a transparent, decomposable
composite score). The output is a **human-review candidate list**: it never
selects codes, never auto-approves, and never edits any theme bundle.

Like every BigQuery path here it is routed through :class:`SafeBigQueryClient`
(dry-run first, ``maximum_bytes_billed`` enforced, cost policy + ledger + SQL
guardrails) and is a *dry run* by default.
"""

from __future__ import annotations

import csv
import json
from typing import Any, Dict, List, Optional, Tuple

from common.cost.ledger import CostLedger
from common.cost.policy import load_cost_policy
from common.date_windows import resolve_date_window, to_yyyymmdd
from common.errors import CostGuardrailError, PipelineError

from news_data.bigquery.cache import bigquery_cache_path, build_cache_key
from news_data.bigquery.client import SafeBigQueryClient
from news_data.bigquery.gdelt_queries import (
    DEFAULT_SCORING_CANDIDATE_LIMIT,
    DEFAULT_SCORING_MIN_SUPPORT,
    DEFAULT_SEED_SAMPLE_COLUMN,
    DEFAULT_SEED_SEARCH_COLUMNS,
    DEFAULT_SEEDED_DISCOVERY_LIMIT,
    DEFAULT_SEEDED_SAMPLE_SIZE,
    DEFAULT_THEME_COLUMN,
    SCORING_QUERY_BUILDER_VERSION,
    SCORING_SEED_SENTINEL_PREFIX,
    SCORING_TOTAL_SENTINEL,
    build_gdelt_seeded_theme_scoring_query,
)
from news_data.bigquery.seed_matching import resolve_seed_terms
from news_data.bigquery.theme_classification import CATEGORIES, classify_theme
from news_data.bigquery.theme_scoring import (
    EXPECTED_CELL_THRESHOLD,
    FISHER_EXACT_AVAILABLE,
    ScoreComponents,
    association_component,
    benjamini_hochberg,
    composite_score,
    diversity_component,
    lift,
    lift_confidence_interval,
    odds_ratio,
    prevalence,
    risk_difference,
    select_hypothesis_test,
    share,
    smoothed_log_lift,
    support_component,
    temporal_stability_component,
    weekly_stability,
)

JsonDict = Dict[str, Any]

PROVIDER = "bigquery_gdelt_seeded_theme_scoring"
_DEFAULT_TABLE = "gdelt-bq.gdeltv2.gkg_partitioned"
_DEFAULT_COST_POLICY_PATH = "configs/cost_policy.yaml"
_DEFAULT_LEDGER_PATH = "data/cost/cost_ledger.jsonl"

MATCHING_MODE = "seed_record_boundary_v6"

# The Benjamini-Hochberg multiple-testing method label, emitted in metadata.
MULTIPLE_TESTING_METHOD = "benjamini_hochberg_fdr"

# Themes explicitly targeted for manual record-level evidence review (Phase 2).
# These are ADDED to the deterministic top-N strata so a reviewer can audit them
# regardless of ranking. Membership is data-independent and reproducible.
_MANUAL_REVIEW_THEMES = (
    "WB_2944_SERVERS",
    "WB_671_STORAGE_MANAGEMENT",
    "WB_1281_MANUFACTURING",
    "WB_667_ICT_INFRASTRUCTURE",
)

# Screening thresholds (decision criteria). These GATE nothing automatically —
# they only populate boolean eligibility / *_pass columns for the reviewer.
# The lift screen is applied to the DISJOINT non-seed inferential lift.
SCREEN_MIN_SUPPORT = 25
SCREEN_MIN_LIFT = 3.0
SCREEN_MIN_PERIODS = 3
SCREEN_MAX_TOP_DAY_SHARE = 0.60
SCREEN_MAX_TOP_SOURCE_SHARE = 0.50
# A candidate "dominated by one seed/company" fails concentration eligibility.
SCREEN_MAX_TOP_SEED_SHARE = 0.90

ART_SQL = "candidate_scoring_sql.sql"
ART_ESTIMATE = "bigquery_dry_run_estimate.json"
ART_DECISION = "bigquery_cost_decision.json"
ART_SUMMARY = "theme_candidate_scoring_summary.json"
ART_SCORES_JSONL = "theme_candidate_scores.jsonl"
ART_SCORES_CSV = "theme_candidate_scores.csv"
ART_REVIEW_CSV = "theme_candidate_review.csv"
ART_SEED_DIAG_CSV = "seed_match_diagnostics.csv"
ART_SEED_DIAG_JSONL = "seed_match_diagnostics.jsonl"
ART_STABILITY_CSV = "candidate_stability.csv"
ART_CONCENTRATION_CSV = "candidate_source_concentration.csv"
ART_EVIDENCE_CSV = "candidate_representative_evidence.csv"
ART_EVIDENCE_JSONL = "candidate_representative_evidence.jsonl"
ART_REPORT_MD = "research_quality_report.md"

_SATURATING_SUPPORT = 5000

# How many strongest / most-discussed candidates get a representative-evidence
# sample written out (Phase 7). Kept small; sampling is deterministic (hash of
# GKGRECORDID) so the same records are always chosen.
_EVIDENCE_TOP_N = 20

# Scalar columns written to theme_candidate_scores.csv (array/nested fields are
# JSON-encoded so the CSV stays flat but lossless). Mirrors the Phase 14 schema:
# descriptive whole-corpus metrics and disjoint non-seed inferential metrics are
# reported SEPARATELY, and approximate vs exact concentration both appear.
_CSV_FIELDS = [
    "theme",
    "raw_frequency_rank",
    "raw_statistical_rank",
    "research_adjusted_rank",
    "seed_record_count",
    "total_seed_records",
    "seed_prevalence",
    # descriptive (whole-corpus, seeds INCLUDED — nested groups)
    "all_corpus_theme_count",
    "all_corpus_record_count",
    "all_corpus_prevalence",
    "descriptive_lift_vs_all_corpus",
    "descriptive_risk_difference_vs_all_corpus",
    # inferential (disjoint non-seed control)
    "nonseed_background_theme_count",
    "nonseed_background_record_count",
    "nonseed_background_prevalence",
    "lift_vs_nonseed_background",
    "smoothed_log_lift_vs_nonseed_background",
    "risk_difference_vs_nonseed_background",
    "smoothed_odds_ratio_vs_nonseed_background",
    "confidence_interval_low",
    "confidence_interval_high",
    # 2x2 contingency + sparse-aware hypothesis-test selection
    "contingency_a",
    "contingency_b",
    "contingency_c",
    "contingency_d",
    "minimum_expected_cell_count",
    "expected_cell_threshold",
    "hypothesis_test",
    "hypothesis_test_reason",
    "p_value",
    "p_value_valid",
    "p_value_unavailable_reason",
    "adjusted_p_value",
    # candidate-family / multiple-testing completeness (fail-closed)
    "total_support_qualified_candidates",
    "candidate_family_safety_cap",
    "family_cap_reached",
    "family_complete",
    "statistical_completion_status",
    "bh_skipped_due_to_incomplete_family",
    "multiple_testing_family_size",
    "multiple_testing_method",
    # stability
    "minimum_period_support",
    "periods_present",
    "periods_total",
    # concentration (approx vs exact, with per-dimension screening provenance)
    "approx_top_day_share",
    "exact_top_day_share",
    "top_day_share_used_for_screening",
    "top_day_share_provenance",
    "approx_top_source_share",
    "exact_top_source_share",
    "top_source_share_used_for_screening",
    "top_source_share_provenance",
    "source_count",
    "approx_top_seed_share",
    "exact_top_seed_share",
    "top_seed_share_used_for_screening",
    "top_seed_share_provenance",
    "seed_count",
    "top_matched_entity",
    "concentration_metric_validated_exactly",
    "concentration_pass_changed_after_exact_check",
    # classification
    "classification",
    "classification_confidence",
    "classification_reasoning",
    "matched_rule",
    "generic_noise_flag",
    "manual_review_required",
    # eligibility tiers + screening
    "statistical_eligibility",
    "stability_eligibility",
    "concentration_eligibility",
    "manual_review_priority",
    "screening_pass",
    # evidence
    "sample_record_ids",
    "sample_sources",
    "matched_seed_examples",
    "score_components",
    "composite_score",
    # backward-compatible aliases (primary = non-seed inferential)
    "lift",
    "smoothed_log_lift",
    "risk_difference",
    "odds_ratio",
    "background_record_count",
    "background_prevalence",
    "total_background_records",
    "top_day_share",
    "top_source_share",
    "top_seed_share",
    "frequency_rank",
]


def bigquery_gdelt_seeded_theme_scoring_task(ctx, params: JsonDict) -> None:
    bq_cfg = (ctx.cfg.get("providers", {}) or {}).get("bigquery", {}) or {}
    project_id = bq_cfg.get("project_id")
    table = bq_cfg.get("table") or _DEFAULT_TABLE

    topic = str(params.get("topic", "")).strip()
    if not topic:
        raise PipelineError("bigquery_gdelt_seeded_theme_scoring requires a non-empty 'topic'.")
    query_name = str(params.get("query_name") or topic).strip()

    seed_terms: List[str] = [
        str(s).strip() for s in (params.get("seed_terms") or []) if str(s).strip()
    ]
    seed_specs = params.get("seed_specs") or None
    if not seed_terms and not seed_specs:
        raise PipelineError(
            "bigquery_gdelt_seeded_theme_scoring requires 'seed_terms' or 'seed_specs'."
        )

    seed_search_columns: List[str] = [
        str(c).strip()
        for c in (params.get("seed_search_columns") or DEFAULT_SEED_SEARCH_COLUMNS)
        if str(c).strip()
    ]
    theme_column = str(params.get("theme_column") or DEFAULT_THEME_COLUMN).strip()
    sample_source_column = str(
        params.get("sample_source_column") or DEFAULT_SEED_SAMPLE_COLUMN
    ).strip()

    partition_filter: JsonDict = params.get("partition_filter", {}) or {}
    window = params.get("window", {}) or {}

    # `limit` is the PRESENTATION limit (top-N in review CSV / report tables).
    # `candidate_limit` is the SQL family safety cap: the FULL support-qualified
    # family is returned (bytes billed are unaffected by row count) so BH-FDR is
    # applied over every tested candidate, not an association-ranked top-N.
    limit = _positive_int(params.get("limit", DEFAULT_SEEDED_DISCOVERY_LIMIT), "limit")
    candidate_limit = _positive_int(
        params.get("candidate_limit", DEFAULT_SCORING_CANDIDATE_LIMIT), "candidate_limit"
    )
    sample_size = _positive_int(
        params.get("sample_size", DEFAULT_SEEDED_SAMPLE_SIZE), "sample_size"
    )
    min_support = _non_negative_int(
        params.get("min_support", DEFAULT_SCORING_MIN_SUPPORT), "min_support"
    )

    # Resolved seeds give a canonical -> kind map for honest diagnostics: the
    # experiment is organization-seeded only (V2Organizations), which the report
    # states explicitly.
    seeds = resolve_seed_terms(seed_specs=seed_specs, seed_terms=seed_terms or None)
    seed_kind = {s.canonical: s.kind for s in seeds}
    organization_seeded_only = [c.strip() for c in seed_search_columns] == ["V2Organizations"]

    cost_controls: JsonDict = params.get("cost_controls", {}) or {}
    cost_policy_path = cost_controls.get("cost_policy_path", _DEFAULT_COST_POLICY_PATH)
    ledger_path = cost_controls.get("ledger_path", _DEFAULT_LEDGER_PATH)
    policy = load_cost_policy(cost_policy_path)
    ledger = CostLedger(ledger_path)

    dry_run = bool(cost_controls.get("dry_run", True))
    execute_query = str(cost_controls.get("execute_query", ""))
    use_cache = bool(cost_controls.get("use_cache", True))
    maximum_bytes_billed = int(
        cost_controls.get("maximum_bytes_billed", policy.default_max_query_bytes)
    )

    start_date, end_date = resolve_date_window(window)
    start_str = to_yyyymmdd(start_date)
    end_str = to_yyyymmdd(end_date)
    n_weeks = ((end_date - start_date).days // 7) + 1

    sql = build_gdelt_seeded_theme_scoring_query(
        table=table,
        start_date=start_str,
        end_date=end_str,
        topic=topic,
        seed_terms=seed_terms or None,
        seed_specs=seed_specs,
        seed_search_columns=seed_search_columns,
        theme_column=theme_column,
        sample_source_column=sample_source_column,
        partition_filter=partition_filter,
        limit=limit,
        sample_size=sample_size,
        min_support=min_support,
        candidate_limit=candidate_limit,
    )
    (ctx.artifacts_dir / ART_SQL).write_text(sql + "\n", encoding="utf-8")

    cache_key = build_cache_key(
        provider=PROVIDER,
        table=table,
        topic=topic,
        query_terms=seed_terms,
        start_date=start_str,
        end_date=end_str,
        builder_version=(f"{SCORING_QUERY_BUILDER_VERSION}-cand{candidate_limit}-min{min_support}"),
        search_columns=seed_search_columns,
        partition_filter=partition_filter,
        query_name=query_name,
        sql=sql,
    )
    cache_path = bigquery_cache_path(cache_key)

    client = SafeBigQueryClient(project_id=project_id, usd_per_tib=policy.bigquery_usd_per_tib)

    try:
        result = client.safe_query(
            sql=sql,
            query_name=query_name,
            topic=topic,
            cost_policy=policy,
            cost_controls=cost_controls,
            project_id=project_id,
            cache_key=cache_key,
            cache_path=cache_path,
            ledger=ledger,
            allowed_tables=[table],
            dry_run=dry_run,
            maximum_bytes_billed=maximum_bytes_billed,
            execute_query=execute_query,
            use_cache=use_cache,
        )
    except CostGuardrailError as err:
        decision = getattr(err, "decision", "BLOCKED")
        estimate = getattr(err, "estimate", None)
        _write_estimate_artifact(ctx, estimate, maximum_bytes_billed)
        _write_decision_artifact(ctx, decision=decision, cache_hit=False, message=str(err))
        ctx.write_json(
            ART_SUMMARY,
            _summary(
                query_name=query_name,
                topic=topic,
                table=table,
                start_str=start_str,
                end_str=end_str,
                dry_run=dry_run,
                estimate=estimate,
                maximum_bytes_billed=maximum_bytes_billed,
                decision=decision,
                cache_hit=False,
                scored=[],
                seed_diag=[],
                denominators=(None, None),
                meta={},
                n_weeks=n_weeks,
                min_support=min_support,
                limit=limit,
                candidate_limit=candidate_limit,
                organization_seeded_only=organization_seeded_only,
                note=str(err),
                warnings=[f"blocked: {decision}"],
            ),
        )
        raise

    _write_estimate_artifact(ctx, result.estimate, maximum_bytes_billed)
    _write_decision_artifact(
        ctx, decision=result.decision, cache_hit=result.cache_hit, message=result.message
    )

    scored: List[JsonDict] = []
    seed_diag: List[JsonDict] = []
    denominators: Tuple[Optional[int], Optional[int]] = (None, None)
    meta: JsonDict = {}
    warnings: List[str] = []
    if result.has_rows:
        scored, seed_diag, denominators, meta = _score_rows(
            result.rows, n_weeks=n_weeks, seed_kind=seed_kind, candidate_limit=candidate_limit
        )
        ctx.state["gdelt_seeded_theme_scores"] = scored
        ctx.write_jsonl(ART_SCORES_JSONL, scored)
        _write_scores_csv(ctx, scored)
        _write_review_csv(ctx, scored, limit=limit)
        _write_seed_diag(ctx, seed_diag)
        _write_stability_csv(ctx, scored, n_weeks=n_weeks)
        _write_concentration_csv(ctx, scored)
        _write_evidence(ctx, scored, n_weeks=n_weeks)
        _write_report(
            ctx,
            scored=scored,
            seed_diag=seed_diag,
            denominators=denominators,
            meta=meta,
            n_weeks=n_weeks,
            topic=topic,
            start_str=start_str,
            end_str=end_str,
            organization_seeded_only=organization_seeded_only,
            limit=limit,
        )
        if not meta.get("family_complete", True):
            warnings.append(
                "STATISTICALLY INCOMPLETE: support-qualified family not proven "
                f"complete (status={meta.get('statistical_completion_status')}); "
                "Benjamini-Hochberg skipped, adjusted p-values are null, and no "
                "scientific verdict may be issued — raise candidate_limit above the "
                "independent qualified count and re-run."
            )
        note = (
            "Loaded from cache." if result.cache_hit else f"Executed BigQuery; {len(scored)} rows."
        )
        if not scored:
            warnings.append("No scored candidates returned; widen seeds/window before proceeding.")
    elif dry_run:
        note = "No data fetched because dry_run=true (dry-run estimate only)."
    else:
        note = "Query executed but returned no scored candidates."
        warnings.append("No scored candidates returned; widen seeds/window before proceeding.")

    ctx.write_json(
        ART_SUMMARY,
        _summary(
            query_name=query_name,
            topic=topic,
            table=table,
            start_str=start_str,
            end_str=end_str,
            dry_run=dry_run,
            estimate=result.estimate,
            maximum_bytes_billed=maximum_bytes_billed,
            decision=result.decision,
            cache_hit=result.cache_hit,
            scored=scored,
            seed_diag=seed_diag,
            denominators=denominators,
            meta=meta,
            n_weeks=n_weeks,
            min_support=min_support,
            limit=limit,
            candidate_limit=candidate_limit,
            organization_seeded_only=organization_seeded_only,
            note=note,
            warnings=warnings,
        ),
    )


def _score_rows(
    rows: List[JsonDict],
    *,
    n_weeks: int,
    seed_kind: Dict[str, str],
    candidate_limit: int,
) -> Tuple[List[JsonDict], List[JsonDict], Tuple[Optional[int], Optional[int]], JsonDict]:
    """Turn raw BigQuery rows into scored candidates + seed diagnostics + meta.

    Returns ``(candidates, seed_diagnostics, (total_seed, total_background),
    meta)``. Synthetic ``__TOTAL__`` / ``__SEED__*`` rows supply the denominators
    and the per-seed corpus counts; they are never emitted as candidates. BH-FDR
    is applied over the COMPLETE returned family (the SQL returns every support-
    qualified candidate up to ``candidate_limit``), so ``meta`` records the
    family size and whether the safety cap truncated it.
    """
    total_row: Optional[JsonDict] = None
    seed_rows: List[JsonDict] = []
    candidate_rows: List[JsonDict] = []
    sentinel_count = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("theme_code") or row.get("theme") or "").strip()
        if code == SCORING_TOTAL_SENTINEL:
            total_row = row
            sentinel_count += 1
        elif code.startswith(SCORING_SEED_SENTINEL_PREFIX):
            seed_rows.append(row)
            sentinel_count += 1
        elif code.startswith("__"):
            sentinel_count += 1
            continue  # unknown sentinel; ignore defensively
        else:
            candidate_rows.append(row)

    # Denominators: prefer the __TOTAL__ row (single-scan design); fall back to
    # per-row columns for backward compatibility with older query shapes/tests.
    if total_row is not None:
        total_seed = _int_or_none(total_row.get("seed_record_count"))
        total_bg = _int_or_none(total_row.get("background_record_count"))
        total_seed_matches = _int_or_none(total_row.get("seed_match_count"))
        multi_seed_records = _int_or_none(total_row.get("multi_seed_record_count"))
    else:
        first = candidate_rows[0] if candidate_rows else {}
        total_seed = _int_or_none(first.get("total_seed_records"))
        total_bg = _int_or_none(first.get("total_background_records"))
        total_seed_matches = None
        multi_seed_records = None

    # INDEPENDENT support-qualified family size (SQL qualified_count CTE). It is
    # computed over per_theme BEFORE the outer safety-cap LIMIT, so it is the
    # ground truth for family completeness even if the returned rows are capped.
    # Read from any row (CROSS JOIN broadcasts it); ``None`` on older query shapes.
    independent_total = None
    for _r in rows or []:
        if isinstance(_r, dict) and _r.get("total_support_qualified_candidates") is not None:
            independent_total = _int_or_none(_r.get("total_support_qualified_candidates"))
            break

    # Seed-match diagnostics (corpus-level, one row per canonical seed). Two
    # explicit denominators (Phase 5): share_of_seed_corpus (over UNIQUE seed
    # records — OVERLAPPING, does NOT sum to 1) and share_of_all_record_seed_
    # matches (over total record-seed matches — sums to ~1).
    seed_diag: List[JsonDict] = []
    for row in seed_rows:
        code = str(row.get("theme_code") or row.get("theme") or "").strip()
        canonical = code[len(SCORING_SEED_SENTINEL_PREFIX) :]
        matched = _int_or_none(row.get("seed_record_count"))
        seed_diag.append(
            {
                "canonical_seed": canonical,
                "seed_kind": seed_kind.get(canonical, "unknown"),
                "unique_seed_records_matching_entity": matched,
                "share_of_unique_seed_records_matching_entity": _round(
                    share(matched, total_seed), 9
                ),
                "record_seed_match_count": matched,
                "share_of_all_record_seed_matches": _round(share(matched, total_seed_matches), 9),
            }
        )
    seed_diag.sort(
        key=lambda d: (-(d["unique_seed_records_matching_entity"] or 0), d["canonical_seed"])
    )

    out: List[JsonDict] = []
    for row in candidate_rows:
        out.append(_score_candidate(row, total_seed=total_seed, total_bg=total_bg, n_weeks=n_weeks))

    # ── FAIL-CLOSED family-completeness gate ────────────────────────────────
    # The candidate safety cap must never let Benjamini-Hochberg run on a
    # truncated hypothesis family. Completeness is proven from the INDEPENDENT
    # qualified_count (unaffected by the row LIMIT); a limit-plus-one row overflow
    # is a secondary detector. If either says the family is incomplete, BH is
    # skipped, every adjusted p-value stays null, and the run is marked
    # statistically incomplete so the scientific verdict is blocked upstream.
    cap = int(candidate_limit)
    returned = len(candidate_rows)
    row_overflow = returned > cap  # SQL returns cap + 1 (+ sentinels)
    if independent_total is None:
        family_complete = not row_overflow
        family_cap_reached = row_overflow
        status = "complete" if family_complete else "incomplete_family_count_unavailable"
        total_qualified: Optional[int] = independent_total if not family_complete else returned
        # Without an independent count we can only *bound* it; if not overflowed
        # the returned rows are the whole family.
        if family_complete:
            total_qualified = returned
    else:
        family_cap_reached = independent_total > cap or row_overflow
        family_complete = independent_total <= cap and not row_overflow
        status = "complete" if family_complete else "incomplete_family_cap_exceeded"
        total_qualified = independent_total

    valid_test_count = sum(1 for r in out if r.get("p_value_valid"))
    invalid_test_count = len(out) - valid_test_count

    if family_complete:
        # Multiple-testing correction across the COMPLETE family (not a top-N).
        # Only candidates with a valid p-value count toward m.
        adjusted = benjamini_hochberg([r["p_value"] for r in out])
        bh_family_size = valid_test_count
        bh_skipped = False
    else:
        adjusted = [None] * len(out)
        bh_family_size = 0
        bh_skipped = True

    for r, adj in zip(out, adjusted):
        r["adjusted_p_value"] = _round(adj, 9)
        r["multiple_testing_family_size"] = bh_family_size
        r["multiple_testing_method"] = MULTIPLE_TESTING_METHOD
        r["total_support_qualified_candidates"] = total_qualified
        r["candidate_family_safety_cap"] = cap
        r["family_cap_reached"] = family_cap_reached
        r["family_complete"] = family_complete
        r["statistical_completion_status"] = status
        r["bh_skipped_due_to_incomplete_family"] = bh_skipped

    _assign_rankings(out)

    meta: JsonDict = {
        "total_support_qualified_candidates": total_qualified,
        "scored_candidate_count": len(out),
        "candidate_rows_returned": returned,
        "candidate_rows_returned_before_presentation_limit": returned,
        "sentinel_row_count": sentinel_count,
        "configured_candidate_limit": cap,
        "candidate_family_safety_cap": cap,
        "family_cap_reached": family_cap_reached,
        "family_complete": family_complete,
        "statistical_completion_status": status,
        "bh_skipped_due_to_incomplete_family": bh_skipped,
        # result_truncated is retained (older summaries/tests read it) and is now
        # an alias of family_cap_reached.
        "result_truncated": family_cap_reached,
        "valid_hypothesis_test_count": valid_test_count,
        "invalid_or_unavailable_test_count": invalid_test_count,
        "multiple_testing_method": MULTIPLE_TESTING_METHOD,
        "multiple_testing_family_size": bh_family_size,
        "multiple_testing_applied_before_limit": True,
        "multiple_testing_applied_before_presentation_limit": True,
        "expected_cell_threshold": EXPECTED_CELL_THRESHOLD,
        "fisher_exact_available": FISHER_EXACT_AVAILABLE,
        "total_record_seed_matches": total_seed_matches,
        "multi_seed_record_count": multi_seed_records,
        "multi_seed_record_share": _round(share(multi_seed_records, total_seed), 9),
        # Concentration coverage counts (Phase 5).
        **_concentration_coverage(out),
    }
    return out, seed_diag, (total_seed, total_bg), meta


def _score_candidate(
    row: JsonDict, *, total_seed: Optional[int], total_bg: Optional[int], n_weeks: int
) -> JsonDict:
    theme = str(row.get("theme_code") or row.get("theme") or "").strip()
    seed_n = _int_or_none(row.get("seed_record_count"))
    # background_record_count is the WHOLE-corpus theme count (seeds included).
    all_corpus_theme_count = _int_or_none(row.get("background_record_count"))
    all_corpus_record_count = total_bg

    # Disjoint NON-SEED inferential control (Phase 3): subtract the seed arm out
    # of the whole-corpus counts so the 2x2 groups do not overlap. Honest nulls
    # (never zeros) if any cell would be invalid.
    nonseed_theme_count = _diff_or_none(all_corpus_theme_count, seed_n)
    nonseed_total = _diff_or_none(total_bg, total_seed)
    cells_valid = _contingency_valid(seed_n, total_seed, nonseed_theme_count, nonseed_total)

    weekly = [_int_or_none(row.get(f"wk_{w}")) for w in range(n_weeks)]
    periods_present, minimum_period_support = weekly_stability(weekly)
    # The SQL also emits periods_present directly; prefer it when present.
    periods_present = _int_or_none(row.get("periods_present")) or periods_present

    # --- Concentration: approximate (APPROX_TOP_COUNT) AND exact (extra grain).
    # APPROX_TOP_COUNT counts the NULL bucket (non-seed rows), which can rank
    # first, so skip null-valued entries and take the first *real* value.
    top_sources = [s for s in _struct_array(row.get("top_sources")) if s.get("value")]
    top_days = [s for s in _struct_array(row.get("top_days")) if s.get("value")]
    sample_sources = [str(s.get("value")) for s in top_sources]
    approx_top_source_count = _struct_n(top_sources[0]) if top_sources else None
    approx_top_day_count = _struct_n(top_days[0]) if top_days else None
    approx_top_source_share = share(approx_top_source_count, seed_n)
    approx_top_day_share = share(approx_top_day_count, seed_n)

    exact_top_source_share = share(_int_or_none(row.get("exact_top_source_count")), seed_n)
    exact_top_day_share = share(_int_or_none(row.get("exact_top_day_count")), seed_n)
    # Prefer the exact value; fall back to approx only if exact is unavailable.
    # Provenance records which value actually fed the screening decision.
    top_source_share, source_provenance = _screen_value(
        exact_top_source_share, approx_top_source_share
    )
    top_day_share, day_provenance = _screen_value(exact_top_day_share, approx_top_day_share)
    source_count = _int_or_none(row.get("source_count"))

    # Seed concentration is already EXACT (from per-seed COUNTIF diagnostics).
    seed_hits = _seed_hits(row.get("seed_hits"))
    seed_count = sum(1 for _, n in seed_hits if n > 0)
    top_seed = max(seed_hits, key=lambda kv: kv[1], default=(None, 0))
    top_seed_share = share(top_seed[1], seed_n) if seed_hits else None
    # Seed/entity concentration is EXACT for every candidate (per-seed COUNTIF in
    # the SQL), so there is no approximate seed share to fall back to.
    seed_provenance = "exact" if top_seed_share is not None else "unavailable"
    top_matched_entity = top_seed[0] if seed_hits and top_seed[1] > 0 else None
    matched = sorted(s for s, n in seed_hits if n > 0)

    # --- Descriptive (whole-corpus) metrics — seeds INCLUDED (nested).
    descriptive_lift = lift(seed_n, total_seed, all_corpus_theme_count, all_corpus_record_count)
    descriptive_rd = risk_difference(
        seed_n, total_seed, all_corpus_theme_count, all_corpus_record_count
    )
    # --- Inferential (disjoint non-seed) metrics.
    if cells_valid:
        log_lift_v = smoothed_log_lift(seed_n, total_seed, nonseed_theme_count, nonseed_total)
        lift_v = lift(seed_n, total_seed, nonseed_theme_count, nonseed_total)
        rd_v = risk_difference(seed_n, total_seed, nonseed_theme_count, nonseed_total)
        or_v = odds_ratio(seed_n, total_seed, nonseed_theme_count, nonseed_total)
        ci = lift_confidence_interval(seed_n, total_seed, nonseed_theme_count, nonseed_total)
    else:
        log_lift_v = lift_v = rd_v = or_v = ci = None

    # Sparse-aware 2x2 hypothesis-test selection (Phase 3). The disjoint table is
    # validated inside; a pooled z-test is used only when every expected cell is
    # >= threshold, otherwise a two-sided Fisher exact test. Invalid tables yield
    # no p-value (never coerced to 1). The smoothed effect-size interval above is
    # a SEPARATE approximate screening estimate and is intentionally unchanged.
    htest = select_hypothesis_test(seed_n, total_seed, nonseed_theme_count, nonseed_total)
    p_value = htest.p_value

    cls = classify_theme(theme)
    components = ScoreComponents(
        support=support_component(seed_n, SCREEN_MIN_SUPPORT, _SATURATING_SUPPORT),
        association=association_component(log_lift_v),
        temporal_stability=temporal_stability_component(periods_present, n_weeks),
        source_diversity=diversity_component(top_source_share),
        seed_diversity=diversity_component(top_seed_share),
        generic_penalty=0.8 if cls.generic_noise_flag else 0.0,
        duplication_penalty=None,
    )
    composite = composite_score(components)

    # --- Eligibility tiers (transparent, non-composite screens). Screening uses
    # the EXACT concentration and the DISJOINT non-seed lift.
    statistical_eligibility = bool(
        (seed_n is not None and seed_n >= SCREEN_MIN_SUPPORT)
        and (lift_v is not None and lift_v >= SCREEN_MIN_LIFT)
    )
    stability_eligibility = bool(periods_present >= SCREEN_MIN_PERIODS)
    concentration_eligibility = bool(
        (top_day_share is not None and top_day_share < SCREEN_MAX_TOP_DAY_SHARE)
        and (top_source_share is not None and top_source_share < SCREEN_MAX_TOP_SOURCE_SHARE)
        and (seed_count > 1)
        and (top_seed_share is not None and top_seed_share < SCREEN_MAX_TOP_SEED_SHARE)
    )
    screening_pass = bool(
        statistical_eligibility
        and stability_eligibility
        and concentration_eligibility
        and (not cls.generic_noise_flag)
    )

    # Did the approximate concentration flip any concentration pass/fail vs exact?
    approx_conc_pass = bool(
        (approx_top_day_share is not None and approx_top_day_share < SCREEN_MAX_TOP_DAY_SHARE)
        and (
            approx_top_source_share is not None
            and approx_top_source_share < SCREEN_MAX_TOP_SOURCE_SHARE
        )
        and (seed_count > 1)
        and (top_seed_share is not None and top_seed_share < SCREEN_MAX_TOP_SEED_SHARE)
    )
    concentration_pass_changed = approx_conc_pass != concentration_eligibility

    manual_review_priority = _review_priority(
        statistical_eligibility=statistical_eligibility,
        stability_eligibility=stability_eligibility,
        concentration_eligibility=concentration_eligibility,
        generic=cls.generic_noise_flag,
        concentration_pass_changed=concentration_pass_changed,
    )

    return {
        "theme": theme,
        # rankings filled by _assign_rankings
        "raw_frequency_rank": None,
        "frequency_rank": None,  # backward-compatible alias
        "raw_statistical_rank": None,
        "research_adjusted_rank": None,
        "seed_record_count": seed_n,
        "total_seed_records": total_seed,
        "seed_prevalence": _round(prevalence(seed_n, total_seed), 9),
        # descriptive whole-corpus
        "all_corpus_theme_count": all_corpus_theme_count,
        "all_corpus_record_count": all_corpus_record_count,
        "all_corpus_prevalence": _round(
            prevalence(all_corpus_theme_count, all_corpus_record_count), 9
        ),
        "descriptive_lift_vs_all_corpus": _round(descriptive_lift),
        "descriptive_risk_difference_vs_all_corpus": _round(descriptive_rd, 9),
        # inferential disjoint non-seed
        "nonseed_background_theme_count": nonseed_theme_count if cells_valid else None,
        "nonseed_background_record_count": nonseed_total if cells_valid else None,
        "nonseed_background_prevalence": (
            _round(prevalence(nonseed_theme_count, nonseed_total), 9) if cells_valid else None
        ),
        "lift_vs_nonseed_background": _round(lift_v),
        "smoothed_log_lift_vs_nonseed_background": _round(log_lift_v),
        "risk_difference_vs_nonseed_background": _round(rd_v, 9),
        "smoothed_odds_ratio_vs_nonseed_background": _round(or_v),
        "confidence_interval_low": _round(ci[0]) if ci else None,
        "confidence_interval_high": _round(ci[1]) if ci else None,
        # 2x2 contingency + sparse-aware test selection (Phase 3)
        "contingency_a": htest.contingency_a,
        "contingency_b": htest.contingency_b,
        "contingency_c": htest.contingency_c,
        "contingency_d": htest.contingency_d,
        "minimum_expected_cell_count": _round(htest.minimum_expected_cell_count, 6),
        "expected_cell_threshold": htest.expected_cell_threshold,
        "hypothesis_test": htest.hypothesis_test,
        "hypothesis_test_reason": htest.hypothesis_test_reason,
        "p_value": _round(p_value, 9),
        "p_value_valid": htest.p_value_valid,
        "p_value_unavailable_reason": htest.p_value_unavailable_reason,
        "adjusted_p_value": None,  # filled after BH across the full family
        # family-completeness fields filled in _score_rows (fail-closed gate)
        "total_support_qualified_candidates": None,
        "candidate_family_safety_cap": None,
        "family_cap_reached": None,
        "family_complete": None,
        "statistical_completion_status": None,
        "bh_skipped_due_to_incomplete_family": None,
        "multiple_testing_method": None,
        "multiple_testing_family_size": None,  # filled after BH
        # backward-compatible aliases (primary = non-seed inferential)
        "lift": _round(lift_v),
        "smoothed_log_lift": _round(log_lift_v),
        "risk_difference": _round(rd_v, 9),
        "odds_ratio": _round(or_v),
        "background_record_count": all_corpus_theme_count,
        "background_prevalence": _round(
            prevalence(all_corpus_theme_count, all_corpus_record_count), 9
        ),
        "total_background_records": total_bg,
        # stability
        "minimum_period_support": minimum_period_support,
        "periods_present": periods_present,
        "periods_total": n_weeks,
        # concentration (approx + exact + per-dimension screening provenance)
        "approx_top_day_share": _round(approx_top_day_share),
        "exact_top_day_share": _round(exact_top_day_share),
        "top_day_share_used_for_screening": _round(top_day_share),
        "top_day_share_provenance": day_provenance,
        "approx_top_source_share": _round(approx_top_source_share),
        "exact_top_source_share": _round(exact_top_source_share),
        "top_source_share_used_for_screening": _round(top_source_share),
        "top_source_share_provenance": source_provenance,
        "source_count": source_count,
        "approx_top_seed_share": _round(top_seed_share),
        "exact_top_seed_share": _round(top_seed_share),
        "top_seed_share_used_for_screening": _round(top_seed_share),
        "top_seed_share_provenance": seed_provenance,
        "seed_count": seed_count,
        "top_matched_entity": top_matched_entity,
        "concentration_metric_validated_exactly": (
            exact_top_source_share is not None and exact_top_day_share is not None
        ),
        "concentration_pass_changed_after_exact_check": concentration_pass_changed,
        # backward-compatible concentration aliases (primary = exact)
        "top_day_share": _round(top_day_share),
        "top_source_share": _round(top_source_share),
        "top_seed_share": _round(top_seed_share),
        "weekly": weekly,
        "seed_hits": [{"seed": s, "n": n} for s, n in seed_hits],
        "first_seen": _int_or_none(row.get("first_seen")),
        "last_seen": _int_or_none(row.get("last_seen")),
        "distinct_day_count": _int_or_none(row.get("distinct_day_count")),
        # classification
        "classification": cls.category,
        "classification_confidence": round(cls.confidence, 4),
        "classification_reasoning": cls.reasoning,
        "matched_rule": cls.matched_rule,
        "generic_noise_flag": cls.generic_noise_flag,
        "manual_review_required": cls.manual_review_required,
        # eligibility + screening
        "statistical_eligibility": statistical_eligibility,
        "stability_eligibility": stability_eligibility,
        "concentration_eligibility": concentration_eligibility,
        "manual_review_priority": manual_review_priority,
        "screening_pass": screening_pass,
        # evidence
        "sample_record_ids": _str_list(row.get("sample_record_ids")),
        "sample_sources": sample_sources,
        "sample_records": _sample_records(row.get("sample_records")),
        "matched_seed_examples": matched,
        "score_components": components.to_dict(),
        "composite_score": _round(composite),
    }


def _review_priority(
    *,
    statistical_eligibility: bool,
    stability_eligibility: bool,
    concentration_eligibility: bool,
    generic: bool,
    concentration_pass_changed: bool,
) -> str:
    """Transparent review-queue label (not a gate, not a composite).

    ``high`` — clears statistics + stability + concentration and is not generic
    (the reviewer's shortlist); ``medium`` — enriched and stable but concentrated
    or flagged, or a candidate whose concentration decision flipped under exact
    validation (worth a look); ``low`` — everything else.
    """
    if (
        statistical_eligibility
        and stability_eligibility
        and concentration_eligibility
        and not generic
    ):
        return "high"
    if statistical_eligibility and stability_eligibility and not generic:
        return "medium"
    if concentration_pass_changed:
        return "medium"
    return "low"


def _assign_rankings(rows: List[JsonDict]) -> None:
    """Assign the deterministic rankings in place.

    - ``raw_frequency_rank`` (alias ``frequency_rank``): raw seed record count
      (the OLD, biased ranking, kept only so the analysis can quantify how far
      the statistical ranking departs from it).
    - ``raw_statistical_rank``: by smoothed log-lift vs the DISJOINT non-seed
      background (effect size), so a strongly enriched theme outranks a frequent
      one.
    - ``research_adjusted_rank``: by the transparent composite (association +
      support + stability + diversity, minus a generic penalty).

    ``None`` metrics always sort last; ties break on theme code for determinism.
    """

    def rank(keys, values_key):
        ordered = sorted(
            range(len(rows)),
            key=lambda i: (
                values_key(rows[i]) is None,
                _neg(values_key(rows[i])),
                rows[i]["theme"],
            ),
        )
        for pos, i in enumerate(ordered, start=1):
            for key in keys:
                rows[i][key] = pos

    rank(("raw_frequency_rank", "frequency_rank"), lambda r: r["seed_record_count"])
    rank(("raw_statistical_rank",), lambda r: r["smoothed_log_lift_vs_nonseed_background"])
    rank(("research_adjusted_rank",), lambda r: r["composite_score"])


def _neg(v):
    return -v if isinstance(v, (int, float)) else 0.0


# ── parsing helpers ──────────────────────────────────────────────────────────


def _struct_array(value: Any) -> List[Dict[str, Any]]:
    """Coerce a BigQuery ARRAY<STRUCT> cell into a list of dicts."""
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(v) for v in value if isinstance(v, dict)]


def _struct_n(struct: Dict[str, Any]) -> Optional[int]:
    """Count field of an APPROX_TOP_COUNT struct (``count``; ``n`` in fixtures)."""
    val = struct.get("count")
    if val is None:
        val = struct.get("n")
    return _int_or_none(val)


def _seed_hits(value: Any) -> List[Tuple[str, int]]:
    """Parse the ``seed_hits`` array of ``{seed, n}`` structs into tuples."""
    out: List[Tuple[str, int]] = []
    if not isinstance(value, (list, tuple)):
        return out
    for item in value:
        if not isinstance(item, dict):
            continue
        seed = str(item.get("seed") or "").strip()
        n = _int_or_none(item.get("n")) or 0
        if seed:
            out.append((seed, n))
    return out


def _screen_value(exact: Optional[float], approx: Optional[float]) -> Tuple[Optional[float], str]:
    """Pick the screening share and record its provenance.

    Exact is always preferred; the approximate sketch is only a fallback. The
    returned provenance is one of ``exact`` / ``approximate`` / ``unavailable``
    and is never claimed to be exact when only the sketch was available.
    """
    if exact is not None:
        return exact, "exact"
    if approx is not None:
        return approx, "approximate"
    return None, "unavailable"


def _test_distribution(rows: List[JsonDict]) -> Dict[str, int]:
    """Count candidates by selected hypothesis test (for the summary artifact)."""
    dist: Dict[str, int] = {}
    for r in rows:
        name = str(r.get("hypothesis_test") or "unknown")
        dist[name] = dist.get(name, 0) + 1
    return dist


def _concentration_coverage(rows: List[JsonDict]) -> JsonDict:
    """Exact/approx concentration coverage counts across the scored family."""

    def has(key: str) -> int:
        return sum(1 for r in rows if r.get(key) is not None)

    n = len(rows)
    return {
        "total_candidate_count": n,
        "candidates_with_approx_source_concentration": has("approx_top_source_share"),
        "candidates_with_exact_source_concentration": has("exact_top_source_share"),
        "candidates_with_approx_day_concentration": has("approx_top_day_share"),
        "candidates_with_exact_day_concentration": has("exact_top_day_share"),
        # seed concentration is exact-only (per-seed COUNTIF), no sketch exists
        "candidates_with_approx_seed_concentration": 0,
        "candidates_with_exact_seed_concentration": has("exact_top_seed_share"),
        "source_screen_uses_exact_when_available": True,
        "day_screen_uses_exact_when_available": True,
        "seed_screen_uses_exact_when_available": True,
        "exact_concentration_selection_rule": (
            "exact metrics are computed for every candidate (source/day via extra "
            "aggregation grains, seed via per-seed COUNTIF, all in the one base "
            "scan); screening prefers exact and only falls back to the "
            "APPROX_TOP_COUNT sketch when an exact value is unavailable."
        ),
    }


def _sample_records(value: Any) -> List[JsonDict]:
    """Parse the hash-sampled ``sample_records`` STRUCT array into plain dicts.

    Carries the GKG ``doc_id`` (DocumentIdentifier / source URL) so record-level
    evidence is independently auditable.
    """
    out: List[JsonDict] = []
    for item in _struct_array(value):
        rec_id = str(item.get("rec_id") or "").strip()
        if not rec_id:
            continue
        seeds = item.get("matched_seeds")
        doc_id = item.get("doc_id")
        out.append(
            {
                "rec_id": rec_id,
                "source": (str(item.get("source")).strip() if item.get("source") else None),
                "day": _int_or_none(item.get("day")),
                "doc_id": (str(doc_id).strip() if doc_id else None),
                "matched_seeds": (
                    [str(s) for s in seeds] if isinstance(seeds, (list, tuple)) else []
                ),
            }
        )
    return out


def _diff_or_none(total: Optional[int], part: Optional[int]) -> Optional[int]:
    """``total - part`` as a non-negative int, else ``None`` (honest null)."""
    if total is None or part is None:
        return None
    d = int(total) - int(part)
    return d if d >= 0 else None


def _contingency_valid(
    a: Optional[int], n1: Optional[int], c: Optional[int], n0: Optional[int]
) -> bool:
    """All four 2x2 cells present and non-negative with valid marginals.

    ``a`` seeded-with-theme, ``n1`` seeded-total, ``c`` nonseed-with-theme,
    ``n0`` nonseed-total. Requires ``0 <= a <= n1`` and ``0 <= c <= n0`` and both
    totals positive, so ``b = n1-a`` and ``d = n0-c`` are also valid.
    """
    if None in (a, n1, c, n0):
        return False
    assert a is not None and n1 is not None and c is not None and n0 is not None
    if n1 <= 0 or n0 <= 0:
        return False
    return 0 <= a <= n1 and 0 <= c <= n0


def _positive_int(value: Any, name: str) -> int:
    try:
        iv = int(value)
    except (TypeError, ValueError) as exc:
        raise PipelineError(
            f"bigquery_gdelt_seeded_theme_scoring '{name}' must be an integer: {exc}."
        )
    if iv <= 0:
        raise PipelineError(f"bigquery_gdelt_seeded_theme_scoring '{name}' must be positive.")
    return iv


def _non_negative_int(value: Any, name: str) -> int:
    try:
        iv = int(value)
    except (TypeError, ValueError) as exc:
        raise PipelineError(
            f"bigquery_gdelt_seeded_theme_scoring '{name}' must be an integer: {exc}."
        )
    if iv < 0:
        raise PipelineError(f"bigquery_gdelt_seeded_theme_scoring '{name}' must be non-negative.")
    return iv


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _round(value: float | None, ndigits: int = 6) -> float | None:
    return None if value is None else round(value, ndigits)


def _str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


# ── artifact writers ─────────────────────────────────────────────────────────


def _write_estimate_artifact(ctx, estimate, maximum_bytes_billed: int) -> None:
    payload = (
        estimate.to_dict()
        if estimate is not None
        else {
            "total_bytes_processed": None,
            "estimated_gib": None,
            "estimated_tib": None,
            "estimated_cost_usd": None,
            "maximum_bytes_billed": maximum_bytes_billed,
            "note": "No dry-run estimate (served from cache).",
        }
    )
    ctx.write_json(ART_ESTIMATE, payload)


def _write_decision_artifact(ctx, *, decision: str, cache_hit: bool, message) -> None:
    ctx.write_json(
        ART_DECISION,
        {"decision": decision, "cache_hit": cache_hit, "message": message},
    )


def _csv_cell(value: Any) -> Any:
    """JSON-encode list/dict cells so the flat CSV stays lossless."""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _write_scores_csv(ctx, rows: List[JsonDict]) -> None:
    path = ctx.artifacts_dir / ART_SCORES_CSV
    ordered = sorted(rows, key=lambda r: r["research_adjusted_rank"])
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in ordered:
            writer.writerow({k: _csv_cell(row.get(k)) for k in _CSV_FIELDS})


def _write_review_csv(ctx, rows: List[JsonDict], *, limit: int) -> None:
    """Reviewer's top-N shortlist (presentation limit); the full family lives in
    ``theme_candidate_scores.{csv,jsonl}``."""
    fields = [
        "research_adjusted_rank",
        "raw_statistical_rank",
        "raw_frequency_rank",
        "theme",
        "classification",
        "classification_reasoning",
        "matched_rule",
        "generic_noise_flag",
        "manual_review_required",
        "manual_review_priority",
        "statistical_eligibility",
        "stability_eligibility",
        "concentration_eligibility",
        "screening_pass",
        "seed_record_count",
        "lift_vs_nonseed_background",
        "smoothed_log_lift_vs_nonseed_background",
        "confidence_interval_low",
        "confidence_interval_high",
        "adjusted_p_value",
        "periods_present",
        "exact_top_day_share",
        "exact_top_source_share",
        "seed_count",
        "exact_top_seed_share",
        "top_matched_entity",
        "sample_sources",
        "sample_record_ids",
    ]
    path = ctx.artifacts_dir / ART_REVIEW_CSV
    ordered = sorted(rows, key=lambda r: r["research_adjusted_rank"])[: max(1, limit)]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in ordered:
            writer.writerow({k: _csv_cell(row.get(k)) for k in fields})


def _write_seed_diag(ctx, seed_diag: List[JsonDict]) -> None:
    ctx.write_jsonl(ART_SEED_DIAG_JSONL, seed_diag)
    fields = [
        "canonical_seed",
        "seed_kind",
        "unique_seed_records_matching_entity",
        "share_of_unique_seed_records_matching_entity",
        "record_seed_match_count",
        "share_of_all_record_seed_matches",
    ]
    path = ctx.artifacts_dir / ART_SEED_DIAG_CSV
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in seed_diag:
            writer.writerow({k: row.get(k) for k in fields})


_EVIDENCE_LIMITATION = (
    "Interpretation bounded to GKG metadata (V2Organizations matches, source "
    "common name, DocumentIdentifier URL). Article body was NOT retrieved, so no "
    "semantic/topical judgment of the article text is asserted."
)


def _evidence_strata(rows: List[JsonDict]) -> List[Tuple[JsonDict, str]]:
    """Select evidence themes across documented, reproducible strata.

    Strata (deterministic, data-order-independent): the top-N research-adjusted
    candidates, the top-N raw-statistical candidates, and an explicit manual-
    review target list. A theme's stratum label records every stratum it belongs
    to, so the sample provenance is auditable and not alphabetical.
    """
    top_adj = sorted(rows, key=lambda r: r["research_adjusted_rank"])[:_EVIDENCE_TOP_N]
    top_stat = sorted(rows, key=lambda r: r["raw_statistical_rank"])[:_EVIDENCE_TOP_N]
    by_adj = {r["theme"] for r in top_adj}
    by_stat = {r["theme"] for r in top_stat}
    manual = set(_MANUAL_REVIEW_THEMES)
    selected: List[Tuple[JsonDict, str]] = []
    for r in rows:
        theme = r["theme"]
        strata: List[str] = []
        if theme in by_adj:
            strata.append("top_research_adjusted")
        if theme in by_stat:
            strata.append("top_raw_statistical")
        if theme in manual:
            strata.append("manual_review_target")
        if strata:
            selected.append((r, "+".join(strata)))
    return selected


def _write_evidence(ctx, rows: List[JsonDict], *, n_weeks: int) -> None:
    """Deterministic, auditable representative-record evidence.

    Selected themes (see :func:`_evidence_strata`) are expanded to one row per
    hash-sampled (FARM_FINGERPRINT of GKGRECORDID) record. The GKG
    ``DocumentIdentifier`` (source URL) is emitted so a reviewer can independently
    inspect each record; when it looks like a URL it also populates
    ``document_url``. The interpretation columns are left null for the human
    reviewer, and ``manual_review_limitations`` states the metadata-only bound.
    No article bodies are fetched.
    """
    selected = _evidence_strata(rows)

    fields = [
        "theme",
        "record_id",
        "record_date",
        "weekly_bucket",
        "source",
        "document_identifier",
        "document_url",
        "matched_seed",
        "canonical_entity",
        "all_matched_seeds",
        "all_matched_entities",
        "sample_selection_method",
        "sample_stratum",
        "evidence_metadata_available",
        "manual_interpretation",
        "identity_context_or_incidental",
        "manual_review_limitations",
    ]
    evidence: List[JsonDict] = []
    for r, stratum in selected:
        for rec in r.get("sample_records") or []:
            day = rec.get("day")
            matched = rec.get("matched_seeds") or []
            doc_id = rec.get("doc_id")
            doc_url = doc_id if (doc_id and str(doc_id).startswith("http")) else None
            evidence.append(
                {
                    "theme": r["theme"],
                    "record_id": rec.get("rec_id"),
                    "record_date": day,
                    "weekly_bucket": _weekly_bucket(day, r.get("first_seen"), n_weeks),
                    "source": rec.get("source"),
                    "document_identifier": doc_id,
                    "document_url": doc_url,
                    "matched_seed": (matched[0] if matched else None),
                    "canonical_entity": (matched[0] if matched else None),
                    "all_matched_seeds": list(matched),
                    "all_matched_entities": list(matched),
                    "sample_selection_method": "deterministic_hash_farmfingerprint_gkgrecordid",
                    "sample_stratum": stratum,
                    "evidence_metadata_available": bool(doc_id) and bool(rec.get("source")),
                    # Filled by a human reviewer from metadata only; never fabricated.
                    "manual_interpretation": None,
                    "identity_context_or_incidental": None,
                    "manual_review_limitations": _EVIDENCE_LIMITATION,
                }
            )
    ctx.write_jsonl(ART_EVIDENCE_JSONL, evidence)
    path = ctx.artifacts_dir / ART_EVIDENCE_CSV
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in evidence:
            writer.writerow({k: _csv_cell(row.get(k)) for k in fields})


def _weekly_bucket(day: Optional[int], first_seen: Optional[int], n_weeks: int) -> Optional[int]:
    """Best-effort weekly bucket index for a YYYYMMDD ``day`` (honest null on gap)."""
    if day is None:
        return None
    try:
        from datetime import date as _date

        d = _date(day // 10000, (day // 100) % 100, day % 100)
        if first_seen is None:
            return None
        f = _date(first_seen // 10000, (first_seen // 100) % 100, first_seen % 100)
        idx = (d - f).days // 7
        return idx if 0 <= idx < n_weeks else None
    except (ValueError, TypeError):
        return None


def _write_stability_csv(ctx, rows: List[JsonDict], *, n_weeks: int) -> None:
    wk_fields = [f"wk_{w}" for w in range(n_weeks)]
    fields = (
        ["theme", "seed_record_count", "periods_present", "periods_total", "minimum_period_support"]
        + wk_fields
        + ["top_day_share", "distinct_day_count", "first_seen", "last_seen"]
    )
    path = ctx.artifacts_dir / ART_STABILITY_CSV
    ordered = sorted(rows, key=lambda r: r["research_adjusted_rank"])
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in ordered:
            out = {k: row.get(k) for k in fields}
            weekly = row.get("weekly") or []
            for w in range(n_weeks):
                out[f"wk_{w}"] = weekly[w] if w < len(weekly) else None
            writer.writerow(out)


def _write_concentration_csv(ctx, rows: List[JsonDict]) -> None:
    fields = [
        "theme",
        "seed_record_count",
        "source_count",
        "approx_top_source_share",
        "exact_top_source_share",
        "top_source_share_used_for_screening",
        "top_source_share_provenance",
        "approx_top_day_share",
        "exact_top_day_share",
        "top_day_share_used_for_screening",
        "top_day_share_provenance",
        "seed_count",
        "exact_top_seed_share",
        "top_seed_share_used_for_screening",
        "top_seed_share_provenance",
        "top_matched_entity",
        "concentration_metric_validated_exactly",
        "concentration_pass_changed_after_exact_check",
        "sample_sources",
    ]
    path = ctx.artifacts_dir / ART_CONCENTRATION_CSV
    ordered = sorted(rows, key=lambda r: r["research_adjusted_rank"])
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in ordered:
            writer.writerow({k: _csv_cell(row.get(k)) for k in fields})


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

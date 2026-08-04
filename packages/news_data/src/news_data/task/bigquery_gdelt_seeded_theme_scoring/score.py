"""Offline scoring of BigQuery seeded-theme contingency rows."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from news_data.bigquery.gdelt_queries import (
    SCORING_SEED_SENTINEL_PREFIX,
    SCORING_TOTAL_SENTINEL,
)
from news_data.bigquery.theme_classification import classify_theme
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

from .constants import (
    _SATURATING_SUPPORT,
    MULTIPLE_TESTING_METHOD,
    SCREEN_MAX_TOP_DAY_SHARE,
    SCREEN_MAX_TOP_SEED_SHARE,
    SCREEN_MAX_TOP_SOURCE_SHARE,
    SCREEN_MIN_LIFT,
    SCREEN_MIN_PERIODS,
    SCREEN_MIN_SUPPORT,
)
from .util import (
    _concentration_coverage,
    _contingency_valid,
    _diff_or_none,
    _int_or_none,
    _neg,
    _round,
    _sample_records,
    _screen_value,
    _seed_hits,
    _str_list,
    _struct_array,
    _struct_n,
)

JsonDict = Dict[str, Any]

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

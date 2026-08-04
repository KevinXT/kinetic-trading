"""Shared constants for seeded theme scoring."""

from __future__ import annotations

PROVIDER = "bigquery_gdelt_seeded_theme_scoring"
_DEFAULT_TABLE = "gdelt-bq.gdeltv2.gkg_partitioned"
_DEFAULT_COST_POLICY_PATH = "configs/cost_policy.yaml"
_DEFAULT_LEDGER_PATH = "data/cost/cost_ledger.jsonl"

MATCHING_MODE = "seed_record_boundary_v6"
MULTIPLE_TESTING_METHOD = "benjamini_hochberg_fdr"

_MANUAL_REVIEW_THEMES = (
    "WB_2944_SERVERS",
    "WB_671_STORAGE_MANAGEMENT",
    "WB_1281_MANUFACTURING",
    "WB_667_ICT_INFRASTRUCTURE",
)

SCREEN_MIN_SUPPORT = 25
SCREEN_MIN_LIFT = 3.0
SCREEN_MIN_PERIODS = 3
SCREEN_MAX_TOP_DAY_SHARE = 0.60
SCREEN_MAX_TOP_SOURCE_SHARE = 0.50
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
_EVIDENCE_TOP_N = 20

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

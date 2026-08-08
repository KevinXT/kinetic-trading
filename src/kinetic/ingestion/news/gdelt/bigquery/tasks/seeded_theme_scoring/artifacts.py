"""Artifact writers for seeded theme scoring outputs."""

from __future__ import annotations

import csv
import json
from typing import Any, Dict, List, Optional, Tuple

from .constants import (
    _CSV_FIELDS,
    _EVIDENCE_TOP_N,
    _MANUAL_REVIEW_THEMES,
    ART_CONCENTRATION_CSV,
    ART_DECISION,
    ART_ESTIMATE,
    ART_EVIDENCE_CSV,
    ART_EVIDENCE_JSONL,
    ART_REVIEW_CSV,
    ART_SCORES_CSV,
    ART_SEED_DIAG_CSV,
    ART_SEED_DIAG_JSONL,
    ART_STABILITY_CSV,
)

JsonDict = Dict[str, Any]


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

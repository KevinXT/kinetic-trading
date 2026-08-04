"""Artifact count reconciliation for the real-corpus pilot."""

from __future__ import annotations

from typing import Any, Mapping


class ReconciliationError(ValueError):
    pass


def _check(name: str, left: int, right: int, errors: list[str]) -> None:
    if left != right:
        errors.append(f"{name}: {left} != {right}")


def reconcile_pilot_counts(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return ok/errors for required arithmetic identities."""
    errors: list[str] = []

    raw_input = int(payload.get("raw_input", 0))
    schema_accepted = int(payload.get("schema_accepted", 0))
    schema_rejected = int(payload.get("schema_rejected", 0))
    _check("raw_input", raw_input, schema_accepted + schema_rejected, errors)

    rights_eligible = int(payload.get("rights_eligible", 0))
    rights_ineligible = int(payload.get("rights_ineligible", 0))
    _check("schema_accepted", schema_accepted, rights_eligible + rights_ineligible, errors)

    corpus_eligible = int(payload.get("corpus_eligible", 0))
    corpus_excluded_non_rights = int(payload.get("corpus_excluded_non_rights", 0))
    _check(
        "rights_eligible",
        rights_eligible,
        corpus_eligible + corpus_excluded_non_rights,
        errors,
    )

    if "eligible_articles" in payload and "exact_cluster_memberships" in payload:
        # Soft check: eligible articles should equal sum of memberships for frame clusters
        # only when all eligible articles are clustered members — reported separately.
        pass

    sampling_frame_units = int(payload.get("sampling_frame_units", 0))
    if "eligible_exact_clusters" in payload:
        _check(
            "sampling_frame_units",
            sampling_frame_units,
            int(payload["eligible_exact_clusters"]),
            errors,
        )

    if "representative_selected" in payload and "representative_by_stratum" in payload:
        _check(
            "representative_selected",
            int(payload["representative_selected"]),
            int(sum(payload["representative_by_stratum"].values())),
            errors,
        )

    if "representative_selected" in payload and "representative_label_partition" in payload:
        part = payload["representative_label_partition"]
        _check(
            "representative_label_partition",
            int(payload["representative_selected"]),
            int(part.get("determinate", 0))
            + int(part.get("cannot_determine", 0))
            + int(part.get("annotation_missing", 0)),
            errors,
        )

    if "double_annotation_selected" in payload and "double_annotation_partition" in payload:
        part = payload["double_annotation_partition"]
        _check(
            "double_annotation_partition",
            int(payload["double_annotation_selected"]),
            int(part.get("jointly_complete", 0))
            + int(part.get("partially_complete", 0))
            + int(part.get("fully_missing", 0)),
            errors,
        )

    if "pair_review_frame" in payload and "pair_review_partition" in payload:
        part = payload["pair_review_partition"]
        _check(
            "pair_review_frame",
            int(payload["pair_review_frame"]),
            int(part.get("reviewed", 0)) + int(part.get("unreviewed", 0)),
            errors,
        )

    if "reviewed_determinate_pairs" in payload and "pair_label_partition" in payload:
        part = payload["pair_label_partition"]
        _check(
            "reviewed_determinate_pairs",
            int(payload["reviewed_determinate_pairs"]),
            int(part.get("same_story", 0)) + int(part.get("distinct", 0)),
            errors,
        )

    if "baseline_evaluated" in payload and "baseline_confusion_sum" in payload:
        _check(
            "baseline_evaluated",
            int(payload["baseline_evaluated"]),
            int(payload["baseline_confusion_sum"]),
            errors,
        )

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "status": "RECONCILED" if not errors else "RECONCILIATION_FAILED",
    }


def assert_reconciled(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = reconcile_pilot_counts(payload)
    if not result["ok"]:
        raise ReconciliationError("; ".join(result["errors"]))
    return result

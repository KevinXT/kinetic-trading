"""Pilot baseline evaluation with representative/challenge separation and weights."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Optional, Sequence

from kinetic.ml.relevance.baselines import BaselinePrediction
from kinetic.ml.relevance.metrics import ConfusionCounts, metrics_from_counts, wilson_interval
from kinetic.ml.relevance.pilot_models import SampledUnitV1
from kinetic.ml.relevance.schemas import AdjudicatedAnnotationV1, binary_relevant

PILOT_METRICS_VERSION = "pilot-baseline-metrics-v1"


def _determinate_labels(
    labels: Sequence[AdjudicatedAnnotationV1],
) -> dict[str, AdjudicatedAnnotationV1]:
    return {lab.article_id: lab for lab in labels}


def weighted_confusion(
    predictions: Sequence[BaselinePrediction],
    labels: Mapping[str, AdjudicatedAnnotationV1],
    weights: Mapping[str, float],
) -> dict[str, Any]:
    tp = fp = tn = fn = abst = 0.0
    n_eval = 0
    for pred in predictions:
        label = labels.get(pred.article_id)
        if label is None:
            continue
        w = float(weights.get(pred.article_id, 1.0))
        n_eval += 1
        actual = binary_relevant(label.relevance_label)
        if pred.abstained or pred.predicted_binary_relevant is None:
            abst += w
            continue
        predicted = bool(pred.predicted_binary_relevant)
        if predicted and actual:
            tp += w
        elif predicted and not actual:
            fp += w
        elif (not predicted) and (not actual):
            tn += w
        else:
            fn += w

    def _ratio(num: float, den: float) -> tuple[Optional[float], Optional[str]]:
        if den <= 0:
            return None, "undefined_zero_denominator"
        return num / den, None

    precision, pr = _ratio(tp, tp + fp)
    recall, rr = _ratio(tp, tp + fn)
    specificity, sr = _ratio(tn, tn + fp)
    if precision is None or recall is None or precision + recall == 0:
        f1, f1r = None, "undefined_zero_denominator"
    else:
        f1, f1r = 2 * precision * recall / (precision + recall), None
    covered = tp + fp + tn + fn
    total = covered + abst
    coverage, _ = _ratio(covered, total) if total else (None, "no_records")
    return {
        "tp_w": tp,
        "fp_w": fp,
        "tn_w": tn,
        "fn_w": fn,
        "abstentions_w": abst,
        "n_articles_evaluated": n_eval,
        "precision_w": precision,
        "precision_reason": pr,
        "recall_w": recall,
        "recall_reason": rr,
        "specificity_w": specificity,
        "specificity_reason": sr,
        "f1_w": f1,
        "f1_reason": f1r,
        "coverage": coverage,
        "abstention_rate": (1.0 - coverage) if coverage is not None else None,
        "uncertainty": {
            "status": "unavailable",
            "method": None,
            "note": (
                "Design-weighted point estimates reported. Wilson intervals are NOT "
                "applied to weighted pseudo-counts. Survey/bootstrap variance not "
                "implemented in this phase."
            ),
        },
        "positive_prevalence_note": "use design-weighted prevalence estimator separately",
    }


def unweighted_confusion_block(
    predictions: Sequence[BaselinePrediction],
    labels: Mapping[str, AdjudicatedAnnotationV1],
) -> dict[str, Any]:
    tp = fp = tn = fn = abst = 0
    for pred in predictions:
        label = labels.get(pred.article_id)
        if label is None:
            continue
        actual = binary_relevant(label.relevance_label)
        if pred.abstained or pred.predicted_binary_relevant is None:
            abst += 1
            continue
        predicted = bool(pred.predicted_binary_relevant)
        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
        elif (not predicted) and (not actual):
            tn += 1
        else:
            fn += 1
    counts = ConfusionCounts(tp=tp, fp=fp, tn=tn, fn=fn, abstentions=abst)
    block = metrics_from_counts(counts)
    # Prevalence
    labeled_preds = [p for p in predictions if p.article_id in labels]
    actual_pos = sum(
        1 for p in labeled_preds if binary_relevant(labels[p.article_id].relevance_label)
    )
    pred_pos = sum(
        1 for p in labeled_preds if (not p.abstained) and p.predicted_binary_relevant is True
    )
    n_lab = len({p.article_id for p in labeled_preds})
    block["positive_prevalence"] = (actual_pos / n_lab) if n_lab else None
    block["predicted_positive_prevalence"] = (pred_pos / n_lab) if n_lab else None
    block["headline_metric_policy"] = (
        "Accuracy is de-emphasized under imbalance; report precision, recall, "
        "coverage, and class prevalence as primary."
    )
    return block


def evaluate_pilot_baselines(
    predictions: Sequence[BaselinePrediction],
    labels: Sequence[AdjudicatedAnnotationV1],
    units: Sequence[SampledUnitV1],
    *,
    role: str,
) -> dict[str, Any]:
    """Evaluate baselines for a sample role (representative|challenge|calibration|combined)."""
    label_by_id = _determinate_labels(labels)
    if role == "combined":
        role_units = list(units)
        population_representative = False
    else:
        role_units = [u for u in units if role in u.sample_roles]
        population_representative = role == "representative"

    role_ids = {u.article_id for u in role_units}
    weights = {
        u.article_id: float(u.design_weight) for u in role_units if u.design_weight is not None
    }
    unequal = population_representative and any(
        u.inclusion_probability is not None
        and u.inclusion_probability != role_units[0].inclusion_probability
        for u in role_units
        if role_units and u.inclusion_probability is not None
    )

    by_baseline: dict[str, list[BaselinePrediction]] = defaultdict(list)
    for pred in predictions:
        if pred.article_id in role_ids and pred.article_id in label_by_id:
            by_baseline[pred.baseline_id].append(pred)

    baselines: dict[str, Any] = {}
    for baseline_id, preds in sorted(by_baseline.items()):
        if population_representative and (unequal or weights):
            # Prefer design-weighted when weights exist.
            if weights:
                block = weighted_confusion(preds, label_by_id, weights)
                block["estimator"] = "design_weighted"
            else:
                block = unweighted_confusion_block(preds, label_by_id)
                block["estimator"] = "unweighted_self_weighting_or_equal_pi"
        else:
            block = unweighted_confusion_block(preds, label_by_id)
            block["estimator"] = "unweighted"
            if role == "challenge":
                block["inferential_use"] = False
                block["note"] = (
                    "Challenge metrics are stress tests, not expected production performance."
                )
        baselines[baseline_id] = block

    return {
        "metrics_version": PILOT_METRICS_VERSION,
        "sample_role": role,
        "population_representative": population_representative,
        "n_units_in_role": len(role_units),
        "n_labeled_in_role": len(role_ids & set(label_by_id)),
        "unequal_inclusion_probabilities": unequal,
        "baselines": baselines,
        "notes": [
            "Primary binary target: relevance_label >= 2.",
            "Do not headline accuracy under class imbalance.",
            "Abstentions reported with coverage; accuracy on covered is separate.",
            "Wilson intervals apply only to unweighted binomial denominators.",
        ],
    }


def design_weighted_prevalence(
    labels: Sequence[AdjudicatedAnnotationV1],
    units: Sequence[SampledUnitV1],
) -> dict[str, Any]:
    """Hájek-style weighted prevalence on representative units with determinate labels."""
    label_by = {lab.article_id: lab for lab in labels}
    num = 0.0
    den = 0.0
    for u in units:
        if "representative" not in u.sample_roles:
            continue
        if u.design_weight is None:
            continue
        lab = label_by.get(u.article_id)
        if lab is None:
            continue
        w = float(u.design_weight)
        y = 1.0 if binary_relevant(lab.relevance_label) else 0.0
        num += w * y
        den += w
    if den <= 0:
        return {
            "estimator": "hajek",
            "prevalence": None,
            "reason": "no_weighted_determinate_labels",
            "population": "eligible_exact_duplicate_clusters_in_local_corpus",
        }
    return {
        "estimator": "hajek",
        "prevalence": num / den,
        "sum_weights": den,
        "population": "eligible_exact_duplicate_clusters_in_local_corpus",
        "uncertainty": {
            "status": "unavailable",
            "note": "No Wilson interval on weighted prevalence in this phase.",
        },
    }


# Keep wilson_interval import used for documentation parity in tests if needed.
_ = wilson_interval

"""Benchmark metrics against adjudicated labels.

Undefined metrics return null plus a reason — never a silent zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from kinetic.ml.relevance.baselines import BaselinePrediction
from kinetic.ml.relevance.schemas import AdjudicatedAnnotationV1, binary_relevant

METRICS_VERSION = "relevance-benchmark-metrics-v1"


def wilson_interval(
    successes: int, n: int, *, z: float = 1.959963984540054
) -> Optional[tuple[float, float]]:
    """95% Wilson score interval for a binomial proportion."""
    if n <= 0:
        return None
    if successes < 0 or successes > n:
        raise ValueError("successes must be in [0, n]")
    phat = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2 * n)) / denom
    margin = (z / denom) * math.sqrt((phat * (1.0 - phat) / n) + (z2 / (4 * n * n)))
    return (max(0.0, center - margin), min(1.0, center + margin))


@dataclass(frozen=True)
class ConfusionCounts:
    tp: int
    fp: int
    tn: int
    fn: int
    abstentions: int

    @property
    def covered(self) -> int:
        return self.tp + self.fp + self.tn + self.fn

    @property
    def total(self) -> int:
        return self.covered + self.abstentions


def _ratio(num: int, den: int) -> tuple[Optional[float], Optional[str]]:
    if den == 0:
        return None, "undefined_zero_denominator"
    return num / den, None


def metrics_from_counts(counts: ConfusionCounts) -> dict[str, Any]:
    tp, fp, tn, fn = counts.tp, counts.fp, counts.tn, counts.fn
    precision, precision_reason = _ratio(tp, tp + fp)
    recall, recall_reason = _ratio(tp, tp + fn)
    specificity, specificity_reason = _ratio(tn, tn + fp)
    accuracy, accuracy_reason = _ratio(tp + tn, counts.covered)

    if precision is None or recall is None or precision + recall == 0:
        f1: Optional[float] = None
        f1_reason: Optional[str] = (
            "undefined_zero_denominator"
            if precision is None or recall is None
            else "undefined_precision_plus_recall_zero"
        )
    else:
        f1 = 2 * precision * recall / (precision + recall)
        f1_reason = None

    bal_acc: Optional[float]
    if recall is None or specificity is None:
        bal_acc = None
        bal_reason = "undefined_requires_recall_and_specificity"
    else:
        bal_acc = 0.5 * (recall + specificity)
        bal_reason = None

    coverage, _ = _ratio(counts.covered, counts.total) if counts.total else (None, "no_records")
    abstention_rate, _ = (
        _ratio(counts.abstentions, counts.total) if counts.total else (None, "no_records")
    )

    def _ci(successes: int, n: int) -> dict[str, Any]:
        interval = wilson_interval(successes, n)
        if interval is None:
            return {"low": None, "high": None, "reason": "undefined_zero_denominator"}
        return {"low": interval[0], "high": interval[1], "reason": None}

    return {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "abstentions": counts.abstentions,
        "total_evaluated_records": counts.total,
        "covered": counts.covered,
        "precision": precision,
        "precision_reason": precision_reason,
        "precision_wilson_95": _ci(tp, tp + fp),
        "recall": recall,
        "recall_reason": recall_reason,
        "recall_wilson_95": _ci(tp, tp + fn),
        "specificity": specificity,
        "specificity_reason": specificity_reason,
        "specificity_wilson_95": _ci(tn, tn + fp),
        "accuracy_on_covered": accuracy,
        "accuracy_reason": accuracy_reason,
        "accuracy_wilson_95": _ci(tp + tn, counts.covered),
        "f1": f1,
        "f1_reason": f1_reason,
        "f1_note": "point_estimate_only_this_phase",
        "balanced_accuracy": bal_acc,
        "balanced_accuracy_reason": bal_reason,
        "coverage": coverage,
        "abstention_rate": abstention_rate,
        "definitions": {
            "precision": "TP / (TP + FP)",
            "recall": "TP / (TP + FN)",
            "f1": "2 * precision * recall / (precision + recall)",
            "specificity": "TN / (TN + FP)",
            "balanced_accuracy": "0.5 * (recall + specificity)",
            "coverage": "covered / total",
            "abstention_rate": "abstentions / total",
            "binary_target": "relevance_label >= 2",
        },
    }


def confusion_from_predictions(
    predictions: Sequence[BaselinePrediction],
    labels: Mapping[str, AdjudicatedAnnotationV1],
) -> ConfusionCounts:
    tp = fp = tn = fn = abstentions = 0
    for pred in predictions:
        label = labels.get(pred.article_id)
        if label is None:
            continue
        actual = binary_relevant(label.relevance_label)
        if pred.abstained or pred.predicted_binary_relevant is None:
            abstentions += 1
            continue
        predicted = bool(pred.predicted_binary_relevant)
        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
        elif not predicted and not actual:
            tn += 1
        else:
            fn += 1
    return ConfusionCounts(tp=tp, fp=fp, tn=tn, fn=fn, abstentions=abstentions)


def evaluate_baselines(
    predictions: Sequence[BaselinePrediction],
    labels: Sequence[AdjudicatedAnnotationV1],
    assignments: Sequence[Mapping[str, Any]],
    *,
    holdout_enabled: bool,
    cluster_count_by_split: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    label_by_id = {lab.article_id: lab for lab in labels}
    split_of = {row["article_id"]: row["split"] for row in assignments}
    # Evaluate on sample representatives / labeled articles only.
    by_baseline: dict[str, list[BaselinePrediction]] = {}
    for pred in predictions:
        if pred.article_id not in label_by_id:
            continue
        by_baseline.setdefault(pred.baseline_id, []).append(pred)

    report: dict[str, Any] = {
        "metrics_version": METRICS_VERSION,
        "holdout_metrics_enabled": holdout_enabled,
        "baselines": {},
        "notes": [
            "Primary binary target: relevance_label >= 2.",
            "Wilson intervals do not prove generalization beyond period/publishers/"
            "companies/guidelines/fields.",
            "No calibration metrics: deterministic baselines lack validated probabilities.",
            "No p-values without a predeclared comparison.",
        ],
    }

    for baseline_id, preds in sorted(by_baseline.items()):
        split_groups: dict[str, list[BaselinePrediction]] = {}
        for pred in preds:
            split = split_of.get(pred.article_id, "unassigned")
            if split == "holdout" and not holdout_enabled:
                continue
            split_groups.setdefault(split, []).append(pred)
        baseline_block: dict[str, Any] = {}
        for split, group in sorted(split_groups.items()):
            counts = confusion_from_predictions(group, label_by_id)
            block = metrics_from_counts(counts)
            block["split"] = split
            block["total_duplicate_clusters"] = (
                cluster_count_by_split.get(split) if cluster_count_by_split else None
            )
            block["baseline_version"] = group[0].baseline_version if group else None
            baseline_block[split] = block
        report["baselines"][baseline_id] = baseline_block
    return report

"""Raw annotation import, agreement analysis, and adjudication helpers."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research_data.relevance.models import (
    AdjudicatedAnnotationV1,
    SemiconductorRelevanceAnnotationV1,
)

# Operational convenience only — not a universal statistical threshold.
DEFAULT_DOUBLE_ANNOTATION_FRACTION = 0.2
MIN_JOINT_LABELS_FOR_KAPPA = 10


def load_raw_annotations(path: str | Path) -> list[SemiconductorRelevanceAnnotationV1]:
    """Load raw annotations; never mutates or overwrites on disk."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    rows: list[Mapping[str, Any]]
    if text.lstrip().startswith("["):
        data = json.loads(text)
        rows = [r for r in data if isinstance(r, dict)]
    else:
        rows = []
        for line in text.splitlines():
            if line.strip():
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
    annotations = [SemiconductorRelevanceAnnotationV1.from_dict(r) for r in rows]
    annotations.sort(key=lambda a: (a.article_id, a.annotator_id, a.annotated_at.isoformat()))
    return annotations


def load_adjudicated_annotations(path: str | Path) -> list[AdjudicatedAnnotationV1]:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    rows: list[Mapping[str, Any]] = []
    if text.lstrip().startswith("["):
        data = json.loads(text)
        rows = [r for r in data if isinstance(r, dict)]
    else:
        for line in text.splitlines():
            if line.strip():
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
    out = [AdjudicatedAnnotationV1.from_dict(r) for r in rows]
    out.sort(key=lambda a: a.article_id)
    return out


def pairwise_joint_labels(
    annotations: Sequence[SemiconductorRelevanceAnnotationV1],
) -> list[tuple[str, int, int, str, str]]:
    """Return joint pairs: article_id, label_a, label_b, annotator_a, annotator_b."""
    by_article: dict[str, list[SemiconductorRelevanceAnnotationV1]] = defaultdict(list)
    for ann in annotations:
        by_article[ann.article_id].append(ann)
    pairs: list[tuple[str, int, int, str, str]] = []
    for article_id, group in sorted(by_article.items()):
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda a: a.annotator_id)
        a, b = ordered[0], ordered[1]
        pairs.append(
            (article_id, a.relevance_label, b.relevance_label, a.annotator_id, b.annotator_id)
        )
    return pairs


def confusion_matrix(
    pairs: Sequence[tuple[str, int, int, str, str]],
    labels: Sequence[int] = (0, 1, 2, 3),
) -> dict[str, Any]:
    matrix = {str(i): {str(j): 0 for j in labels} for i in labels}
    for _, la, lb, _, _ in pairs:
        matrix[str(la)][str(lb)] += 1
    return matrix


def percent_agreement(pairs: Sequence[tuple[str, int, int, str, str]]) -> Optional[float]:
    if not pairs:
        return None
    agree = sum(1 for _, a, b, _, _ in pairs if a == b)
    return agree / len(pairs)


def linearly_weighted_cohen_kappa(
    pairs: Sequence[tuple[str, int, int, str, str]],
    labels: Sequence[int] = (0, 1, 2, 3),
) -> dict[str, Any]:
    """Linearly weighted Cohen's kappa for ordinal labels (two annotators).

    Returns null kappa with a reason when undefined or the joint sample is small.
    """
    n = len(pairs)
    if n == 0:
        return {
            "kappa": None,
            "reason": "no_joint_labels",
            "n_joint": 0,
            "warning": "insufficient_double_labeled_sample",
        }
    if n < MIN_JOINT_LABELS_FOR_KAPPA:
        warning = "double_labeled_sample_small_report_descriptively"
    else:
        warning = None

    label_list = list(labels)
    idx = {lab: i for i, lab in enumerate(label_list)}
    k = len(label_list)
    conf = [[0 for _ in range(k)] for _ in range(k)]
    for _, a, b, _, _ in pairs:
        conf[idx[a]][idx[b]] += 1

    # Weights: 1 - |i-j|/(k-1)
    weights = [[1.0 - abs(i - j) / (k - 1) for j in range(k)] for i in range(k)]
    row_marg = [sum(conf[i][j] for j in range(k)) / n for i in range(k)]
    col_marg = [sum(conf[i][j] for i in range(k)) / n for j in range(k)]

    po = sum(weights[i][j] * conf[i][j] / n for i in range(k) for j in range(k))
    pe = sum(weights[i][j] * row_marg[i] * col_marg[j] for i in range(k) for j in range(k))
    if abs(1.0 - pe) < 1e-12:
        return {
            "kappa": None,
            "reason": "expected_agreement_undefined_or_one",
            "n_joint": n,
            "observed_agreement_weighted": po,
            "expected_agreement_weighted": pe,
            "warning": warning,
        }
    kappa = (po - pe) / (1.0 - pe)
    return {
        "kappa": kappa,
        "reason": None,
        "n_joint": n,
        "observed_agreement_weighted": po,
        "expected_agreement_weighted": pe,
        "warning": warning,
    }


def disagreement_distance_counts(
    pairs: Sequence[tuple[str, int, int, str, str]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for _, a, b, _, _ in pairs:
        d = abs(a - b)
        counts[f"distance_{d}"] += 1
        if a != b:
            lo, hi = (a, b) if a < b else (b, a)
            counts[f"pair_{lo}_vs_{hi}"] += 1
    return dict(sorted(counts.items()))


def label_prevalence_by_annotator(
    annotations: Sequence[SemiconductorRelevanceAnnotationV1],
) -> dict[str, dict[str, int]]:
    out: dict[str, Counter[str]] = defaultdict(Counter)
    for ann in annotations:
        out[ann.annotator_id][str(ann.relevance_label)] += 1
    return {aid: dict(sorted(c.items())) for aid, c in sorted(out.items())}


def analyze_agreement(
    annotations: Sequence[SemiconductorRelevanceAnnotationV1],
) -> dict[str, Any]:
    pairs = pairwise_joint_labels(annotations)
    kappa = linearly_weighted_cohen_kappa(pairs)
    result = {
        "n_raw_annotations": len(annotations),
        "n_jointly_labeled_articles": len(pairs),
        "raw_percent_agreement": percent_agreement(pairs),
        "confusion_matrix": confusion_matrix(pairs),
        "linearly_weighted_cohen_kappa": kappa,
        "label_prevalence_by_annotator": label_prevalence_by_annotator(annotations),
        "disagreement_counts": disagreement_distance_counts(pairs),
        "notes": [
            "Kappa is not the only quality measure.",
            "No universal passing threshold (e.g. kappa >= 0.80) is imposed.",
            "Raw labels remain immutable; adjudication is stored separately.",
        ],
    }
    if kappa.get("warning"):
        result["warning"] = kappa["warning"]
    return result


def disagreement_rows(
    annotations: Sequence[SemiconductorRelevanceAnnotationV1],
) -> list[dict[str, Any]]:
    pairs = pairwise_joint_labels(annotations)
    rows: list[dict[str, Any]] = []
    for article_id, a, b, aid_a, aid_b in pairs:
        if a == b:
            continue
        rows.append(
            {
                "article_id": article_id,
                "annotator_a": aid_a,
                "label_a": a,
                "annotator_b": aid_b,
                "label_b": b,
                "absolute_distance": abs(a - b),
            }
        )
    rows.sort(key=lambda r: (r["article_id"], r["annotator_a"], r["annotator_b"]))
    return rows


def render_annotation_quality_report(agreement: Mapping[str, Any]) -> str:
    lines = [
        "# Annotation quality report",
        "",
        f"- Raw annotations: {agreement.get('n_raw_annotations')}",
        f"- Jointly labeled articles: {agreement.get('n_jointly_labeled_articles')}",
        f"- Raw percent agreement: {agreement.get('raw_percent_agreement')}",
        f"- Linearly weighted Cohen's kappa: "
        f"{agreement.get('linearly_weighted_cohen_kappa', {}).get('kappa')}",
        f"- Kappa status: {agreement.get('linearly_weighted_cohen_kappa', {}).get('reason')}",
    ]
    if agreement.get("warning"):
        lines.append(f"- Warning: {agreement['warning']}")
    lines.extend(
        [
            "",
            "Kappa is reported alongside percent agreement, confusion matrix, and",
            "disagreement-distance counts. No universal pass/fail threshold is applied.",
            "",
        ]
    )
    return "\n".join(lines)

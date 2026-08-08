"""Duplicate-threshold calibration metrics for Sample C pair reviews."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Optional, Sequence

from kinetic.ml.relevance.pilot_models import (
    DuplicatePairReviewV1,
    is_same_underlying_story,
)

DEFAULT_THRESHOLD_GRID = (
    0.50,
    0.55,
    0.60,
    0.65,
    0.68,
    0.70,
    0.75,
    0.80,
    0.85,
    0.90,
)


def pair_max_similarity(review: Mapping[str, Any] | DuplicatePairReviewV1) -> Optional[float]:
    if isinstance(review, DuplicatePairReviewV1):
        scores = [s for s in (review.title_similarity, review.body_similarity) if s is not None]
    else:
        scores = [
            s
            for s in (review.get("title_similarity"), review.get("body_similarity"))
            if s is not None
        ]
    return max(scores) if scores else None


def connected_components_by_article(
    reviews: Sequence[DuplicatePairReviewV1],
) -> list[list[int]]:
    """Article-ID connected components over reviewed pairs (dependence units)."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for rev in reviews:
        union(rev.article_id_a, rev.article_id_b)
    groups: dict[str, list[int]] = defaultdict(list)
    for i, rev in enumerate(reviews):
        groups[find(rev.article_id_a)].append(i)
    return [sorted(v) for _, v in sorted(groups.items())]


def confusion_at_threshold(
    reviews: Sequence[DuplicatePairReviewV1],
    *,
    threshold: float,
    use_body: bool = True,
    use_title: bool = True,
    weighted: bool = True,
) -> dict[str, Any]:
    tp = fp = fn = tn = 0.0
    determinate = 0
    for rev in reviews:
        same = rev.same_underlying_story
        if same is None:
            continue
        determinate += 1
        score_body = rev.body_similarity
        score_title = rev.title_similarity
        candidate = False
        if use_title and score_title is not None and score_title >= threshold:
            candidate = True
        if use_body and score_body is not None and score_body >= threshold:
            candidate = True
        w = rev.sampling_weight if weighted else 1.0
        if candidate and same:
            tp += w
        elif candidate and not same:
            fp += w
        elif (not candidate) and same:
            fn += w
        else:
            tn += w

    def _ratio(num: float, den: float) -> tuple[Optional[float], Optional[str]]:
        if den <= 0:
            return None, "undefined_zero_denominator"
        return num / den, None

    precision, prec_r = _ratio(tp, tp + fp)
    sensitivity, sens_r = _ratio(tp, tp + fn)
    return {
        "threshold": threshold,
        "weighted": weighted,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "determinate_reviews": determinate,
        "candidate_precision": precision,
        "candidate_precision_reason": prec_r,
        "sensitivity_within_blocked_universe": sensitivity,
        "sensitivity_reason": sens_r,
        "uncertainty": {
            "method": None,
            "status": "exploratory_dependence_not_fully_adjusted",
            "note": (
                "Wilson intervals are not applied to weighted pseudo-counts. "
                "Pairs sharing an article are dependent; component bootstrap "
                "recommended. Intervals marked exploratory if not implemented."
            ),
        },
    }


def threshold_grid_metrics(
    reviews: Sequence[DuplicatePairReviewV1],
    pair_frame: Sequence[Mapping[str, Any]],
    *,
    grid: Sequence[float] = DEFAULT_THRESHOLD_GRID,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in grid:
        block = confusion_at_threshold(reviews, threshold=t, weighted=True)
        corpus_candidates = sum(
            1
            for p in pair_frame
            if (
                (p.get("title_similarity") is not None and float(p["title_similarity"]) >= t)
                or (p.get("body_similarity") is not None and float(p["body_similarity"]) >= t)
            )
        )
        block["corpus_candidate_pair_count"] = corpus_candidates
        block["estimated_reviewer_workload_candidates"] = corpus_candidates
        block["recommendation_status"] = "HUMAN_REVIEW_REQUIRED"
        rows.append(block)
    # Monotonic candidate counts should non-increase as threshold rises.
    rows.sort(key=lambda r: r["threshold"])
    return rows


def load_duplicate_pair_reviews(path: str) -> list[DuplicatePairReviewV1]:
    import json
    from pathlib import Path

    text = Path(path).read_text(encoding="utf-8")
    out: list[DuplicatePairReviewV1] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if isinstance(obj, dict):
            out.append(DuplicatePairReviewV1.from_dict(obj))
    out.sort(key=lambda r: r.pair_id)
    return out


def render_duplicate_threshold_report(
    metrics_rows: Sequence[Mapping[str, Any]],
    *,
    blocking_universe: Mapping[str, Any],
) -> str:
    lines = [
        "# Duplicate threshold calibration report",
        "",
        "## Blocking universe",
        "",
    ]
    for k, v in blocking_universe.items():
        lines.append(f"- **{k}**: {v}")
    lines.extend(
        [
            "",
            "Duplicate recall is estimated only within this universe.",
            "",
            "## Threshold comparison",
            "",
            "| Threshold | Precision | Sensitivity | Corpus candidates | Status |",
            "|---:|---:|---:|---:|---|",
        ]
    )
    for row in metrics_rows:
        lines.append(
            f"| {row['threshold']:.2f} | {row.get('candidate_precision')} | "
            f"{row.get('sensitivity_within_blocked_universe')} | "
            f"{row.get('corpus_candidate_pair_count')} | "
            f"{row.get('recommendation_status')} |"
        )
    lines.extend(
        [
            "",
            "## Threshold-selection posture",
            "",
            "- No automatic threshold promotion.",
            "- `duplicate_threshold_recommendation_status: HUMAN_REVIEW_REQUIRED`",
            "- Do not choose a threshold until false-merge vs missed-duplicate vs "
            "review-burden tradeoffs are defined by a human.",
            "- Do not lower thresholds merely to capture a synthetic fixture pair.",
            "",
            "RELATED_SAME_EVENT_DISTINCT_ARTICLE is not automatically a duplicate.",
            "",
        ]
    )
    return "\n".join(lines)


# Re-export helper for tests
__all__ = [
    "DEFAULT_THRESHOLD_GRID",
    "confusion_at_threshold",
    "connected_components_by_article",
    "is_same_underlying_story",
    "load_duplicate_pair_reviews",
    "pair_max_similarity",
    "render_duplicate_threshold_report",
    "threshold_grid_metrics",
]

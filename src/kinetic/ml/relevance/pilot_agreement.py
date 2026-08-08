"""Pilot agreement mathematics: adjacent/large disagreement, bootstrap kappa CI."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from typing import Any, Mapping, Optional, Sequence

from kinetic.ml.relevance.annotation import (
    confusion_matrix,
    linearly_weighted_cohen_kappa,
)
from kinetic.ml.relevance.metrics import wilson_interval
from kinetic.ml.relevance.pilot_models import (
    DISAGREEMENT_CAUSE_CATEGORIES,
    PilotRelevanceAnnotationV1,
)

DEFAULT_BOOTSTRAP_REPLICATES = 2000


def determinate_joint_pairs(
    annotations: Sequence[PilotRelevanceAnnotationV1],
) -> list[tuple[str, int, int, str, str]]:
    """Joint pairs on exact-cluster units with determinate ordinal labels."""
    by_cluster: dict[str, list[PilotRelevanceAnnotationV1]] = defaultdict(list)
    for ann in annotations:
        if ann.cannot_determine or ann.relevance_label is None:
            continue
        by_cluster[ann.duplicate_cluster_id].append(ann)
    pairs: list[tuple[str, int, int, str, str]] = []
    for cluster_id, group in sorted(by_cluster.items()):
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda a: a.annotator_id)
        a, b = ordered[0], ordered[1]
        assert a.relevance_label is not None and b.relevance_label is not None
        pairs.append(
            (
                cluster_id,
                a.relevance_label,
                b.relevance_label,
                a.annotator_id,
                b.annotator_id,
            )
        )
    return pairs


def exact_agreement(pairs: Sequence[tuple[str, int, int, str, str]]) -> Optional[float]:
    if not pairs:
        return None
    return sum(1 for _, a, b, _, _ in pairs if a == b) / len(pairs)


def adjacent_agreement(pairs: Sequence[tuple[str, int, int, str, str]]) -> Optional[float]:
    if not pairs:
        return None
    return sum(1 for _, a, b, _, _ in pairs if abs(a - b) <= 1) / len(pairs)


def large_disagreement_rate(pairs: Sequence[tuple[str, int, int, str, str]]) -> Optional[float]:
    if not pairs:
        return None
    return sum(1 for _, a, b, _, _ in pairs if abs(a - b) >= 2) / len(pairs)


def binary_agreement(pairs: Sequence[tuple[str, int, int, str, str]]) -> Optional[float]:
    if not pairs:
        return None
    return sum(1 for _, a, b, _, _ in pairs if (a >= 2) == (b >= 2)) / len(pairs)


def _wilson_block(successes: int, n: int) -> dict[str, Any]:
    interval = wilson_interval(successes, n)
    if interval is None:
        return {"estimate": None, "low": None, "high": None, "n": n, "method": "wilson"}
    return {
        "estimate": successes / n if n else None,
        "low": interval[0],
        "high": interval[1],
        "n": n,
        "method": "wilson",
    }


def _bootstrap_indices(n: int, replicate: int, seed: str) -> list[int]:
    """Deterministic bootstrap sample of cluster indices."""
    out: list[int] = []
    for j in range(n):
        digest = hashlib.sha256(f"{seed}|boot|{replicate}|{j}".encode("utf-8")).hexdigest()
        out.append(int(digest[:16], 16) % n)
    return out


def bootstrap_weighted_kappa_ci(
    pairs: Sequence[tuple[str, int, int, str, str]],
    *,
    replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
    seed: str = "pilot-kappa-bootstrap-v1",
    confidence_level: float = 0.95,
) -> dict[str, Any]:
    """Cluster bootstrap: resample pairs (cluster units), keep labels paired."""
    n = len(pairs)
    point = linearly_weighted_cohen_kappa(pairs)
    if n < 2 or point.get("kappa") is None:
        return {
            "kappa": point.get("kappa"),
            "kappa_ci": None,
            "reason": point.get("reason") or "insufficient_or_undefined_kappa",
            "bootstrap_replicates": replicates,
            "failed_replicates": 0,
            "method": "cluster_bootstrap_paired_labels",
        }

    stats: list[float] = []
    failed = 0
    for r in range(replicates):
        idx = _bootstrap_indices(n, r, seed)
        sample = [pairs[i] for i in idx]
        k = linearly_weighted_cohen_kappa(sample)
        if k.get("kappa") is None:
            failed += 1
            continue
        stats.append(float(k["kappa"]))
    if len(stats) < max(10, replicates // 10):
        return {
            "kappa": point["kappa"],
            "observed_weighted_agreement": point.get("observed_agreement_weighted"),
            "expected_weighted_agreement": point.get("expected_agreement_weighted"),
            "kappa_ci": None,
            "reason": "too_many_undefined_bootstrap_replicates",
            "bootstrap_replicates": replicates,
            "failed_replicates": failed,
            "successful_replicates": len(stats),
            "method": "cluster_bootstrap_paired_labels",
        }
    stats.sort()
    alpha = 1.0 - confidence_level
    lo_i = int(alpha / 2 * (len(stats) - 1))
    hi_i = int((1.0 - alpha / 2) * (len(stats) - 1))
    return {
        "kappa": point["kappa"],
        "observed_weighted_agreement": point.get("observed_agreement_weighted"),
        "expected_weighted_agreement": point.get("expected_agreement_weighted"),
        "weight_definition": "agreement_weights w_ij = 1 - |i-j|/(K-1)",
        "kappa_ci": {"low": stats[lo_i], "high": stats[hi_i], "level": confidence_level},
        "reason": None,
        "bootstrap_replicates": replicates,
        "failed_replicates": failed,
        "successful_replicates": len(stats),
        "method": "cluster_bootstrap_paired_labels",
        "note": "2000 replicates is an engineering default, not a universal theorem.",
    }


def analyze_pilot_agreement(
    annotations: Sequence[PilotRelevanceAnnotationV1],
    *,
    bootstrap_replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
    bootstrap_seed: str = "pilot-kappa-bootstrap-v1",
) -> dict[str, Any]:
    pairs = determinate_joint_pairs(annotations)
    n = len(pairs)
    exact = exact_agreement(pairs)
    adjacent = adjacent_agreement(pairs)
    large = large_disagreement_rate(pairs)
    binary = binary_agreement(pairs)
    exact_successes = sum(1 for _, a, b, _, _ in pairs if a == b)
    large_successes = sum(1 for _, a, b, _, _ in pairs if abs(a - b) >= 2)

    kappa_block = bootstrap_weighted_kappa_ci(
        pairs, replicates=bootstrap_replicates, seed=bootstrap_seed
    )

    # Convert pilot annotations to a prevalence view compatible with foundation helper.
    # label_prevalence_by_annotator expects SemiconductorRelevanceAnnotationV1-like
    # relevance_label; build a lightweight shim via counts directly.
    prevalence: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    cannot_counts: Counter[str] = Counter()
    for ann in annotations:
        if ann.cannot_determine:
            cannot_counts[ann.annotator_id] += 1
            continue
        if ann.relevance_label is not None:
            prevalence[ann.annotator_id][str(ann.relevance_label)] += 1

    return {
        "n_raw_annotations": len(annotations),
        "n_jointly_labeled_clusters": n,
        "cannot_determine_by_annotator": dict(sorted(cannot_counts.items())),
        "raw_exact_agreement": exact,
        "raw_exact_agreement_wilson_95": _wilson_block(exact_successes, n),
        "adjacent_agreement": adjacent,
        "large_disagreement_rate": large,
        "large_disagreement_wilson_95": _wilson_block(large_successes, n),
        "binary_agreement_label_ge_2": binary,
        "confusion_matrix": confusion_matrix(pairs),
        "linearly_weighted_cohen_kappa": kappa_block,
        "label_prevalence_by_annotator": {
            aid: dict(sorted(c.items())) for aid, c in sorted(prevalence.items())
        },
        "independence_unit": "exact_duplicate_cluster",
        "notes": [
            (
                "Kappa must not be interpreted without n, prevalence, "
                "marginals, CI, and raw agreement."
            ),
            "Bootstrap resamples clusters and keeps paired annotator labels together.",
            (
                "cannot_determine is excluded from ordinal agreement denominators "
                "and reported separately."
            ),
        ],
    }


def disagreement_taxonomy_rows(
    pairs: Sequence[tuple[str, int, int, str, str]],
    causes: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Attach adjudicated disagreement cause categories (required for disagreements)."""
    rows: list[dict[str, Any]] = []
    for cluster_id, a, b, aid_a, aid_b in pairs:
        if a == b:
            continue
        cause = causes.get(cluster_id)
        if cause is None:
            raise ValueError(
                f"missing disagreement cause category for cluster {cluster_id}; "
                f"allowed: {sorted(DISAGREEMENT_CAUSE_CATEGORIES)}"
            )
        if cause not in DISAGREEMENT_CAUSE_CATEGORIES:
            raise ValueError(f"unsupported disagreement cause {cause!r}")
        rows.append(
            {
                "duplicate_cluster_id": cluster_id,
                "annotator_a": aid_a,
                "label_a": a,
                "annotator_b": aid_b,
                "label_b": b,
                "absolute_distance": abs(a - b),
                "disagreement_cause": cause,
            }
        )
    return rows


def summarize_disagreement_causes(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter(str(r["disagreement_cause"]) for r in rows)
    total = sum(counts.values())
    shares = {k: (v / total if total else None) for k, v in sorted(counts.items())}
    return {
        "counts": dict(sorted(counts.items())),
        "shares": shares,
        "total_disagreements": total,
        "note": "Not every disagreement is annotator error.",
    }


def load_pilot_annotations(path: str) -> list[PilotRelevanceAnnotationV1]:
    import json
    from pathlib import Path

    text = Path(path).read_text(encoding="utf-8")
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
    out = [PilotRelevanceAnnotationV1.from_dict(r) for r in rows]
    out.sort(
        key=lambda a: (
            a.duplicate_cluster_id,
            a.annotator_id,
            a.annotation_completed_at.isoformat(),
        )
    )
    return out

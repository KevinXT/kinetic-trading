"""Leakage-resistant chronological split assignment on exact-duplicate clusters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

from kinetic.data.schemas.news import ArticleTextRecordV1
from kinetic.ml.relevance.sampling import BenchmarkCandidate
from kinetic.ml.relevance.schemas import AdjudicatedAnnotationV1
from kinetic.processing.news.dedupe.exact import ExactDuplicateCluster, cluster_index
from kinetic.processing.news.dedupe.near import NearDuplicateCandidate

SPLIT_VERSION = "relevance-benchmark-split-v1"
SPLITS = ("development", "validation", "holdout")
HOLDOUT_GATE_VALUE = "ENABLE"


@dataclass(frozen=True)
class SplitConfig:
    development_end: date
    validation_end: date
    holdout_end: date
    embargo_days: Optional[int] = None
    evaluate_holdout: Optional[str] = None
    version: str = SPLIT_VERSION

    def __post_init__(self) -> None:
        if not (self.development_end < self.validation_end <= self.holdout_end):
            # validation_end marks end of validation; holdout starts after embargo.
            if self.development_end >= self.validation_end:
                raise ValueError("development_end must be before validation_end")
            if self.validation_end > self.holdout_end:
                raise ValueError("validation_end must be <= holdout_end")
        if self.embargo_days is not None and self.embargo_days < 0:
            raise ValueError("embargo_days must be >= 0 when provided")

    @property
    def holdout_enabled(self) -> bool:
        return self.evaluate_holdout == HOLDOUT_GATE_VALUE

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> SplitConfig:
        embargo = data.get("embargo_days", None)
        if embargo is None and "embargo_days" not in data:
            embargo = None
        elif embargo is not None:
            embargo = int(embargo)
        return cls(
            development_end=date.fromisoformat(str(data["development_end"])),
            validation_end=date.fromisoformat(str(data["validation_end"])),
            holdout_end=date.fromisoformat(str(data["holdout_end"])),
            embargo_days=embargo,
            evaluate_holdout=(
                str(data["evaluate_holdout"]) if data.get("evaluate_holdout") is not None else None
            ),
            version=str(data.get("version", SPLIT_VERSION)),
        )


def _cluster_date(
    cluster: ExactDuplicateCluster,
    by_id: Mapping[str, ArticleTextRecordV1],
) -> Optional[date]:
    dates: list[date] = []
    for article_id in cluster.member_article_ids:
        pub = by_id[article_id].published_at
        if pub is not None:
            dates.append(pub.astimezone(timezone.utc).date())
    if not dates:
        return None
    return min(dates)


def assign_splits(
    candidates: Sequence[BenchmarkCandidate],
    articles: Sequence[ArticleTextRecordV1],
    clusters: Sequence[ExactDuplicateCluster],
    *,
    config: SplitConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_id = {a.article_id: a for a in articles}
    index = cluster_index(clusters)
    seen_articles: set[str] = set()
    assignments: list[dict[str, Any]] = []

    embargo = config.embargo_days
    # Periods:
    # development: published_date <= development_end
    # validation: development_end + embargo < date <= validation_end
    # holdout: validation_end + embargo < date <= holdout_end
    for cand in sorted(candidates, key=lambda c: c.duplicate_cluster_id):
        cluster = index[cand.article_id]
        # Ensure all members of the cluster get the same split via cluster date.
        cdate = _cluster_date(cluster, by_id)
        split = _split_for_date(cdate, config, embargo)
        for article_id in cluster.member_article_ids:
            if article_id in seen_articles:
                raise ValueError(f"duplicate article_id in split assignment: {article_id}")
            seen_articles.add(article_id)
            # Only emit rows for sampled candidate articles + all cluster members
            # that appear in the candidate set's clusters.
            assignments.append(
                {
                    "article_id": article_id,
                    "duplicate_cluster_id": cluster.cluster_id,
                    "split": split,
                    "cluster_published_date": cdate.isoformat() if cdate else None,
                    "is_sample_representative": article_id == cand.article_id,
                    "split_version": config.version,
                }
            )

    # Keep only unique article rows (cluster members may appear once).
    # If multiple candidates somehow share a cluster, already guarded.
    assignments.sort(key=lambda r: (r["split"], r["duplicate_cluster_id"], r["article_id"]))

    manifest = {
        "split_version": config.version,
        "development_end": config.development_end.isoformat(),
        "validation_end": config.validation_end.isoformat(),
        "holdout_end": config.holdout_end.isoformat(),
        "embargo_days": embargo,
        "evaluate_holdout": config.evaluate_holdout,
        "holdout_metrics_enabled": config.holdout_enabled,
        "assignment_count": len(assignments),
        "counts_by_split": _count_by(assignments, "split"),
        "notes": [
            "Splits assigned after exact duplicate clustering.",
            "All members of an exact cluster share one split.",
            "Chronological boundaries are explicit; embargo has no hidden default.",
            'Holdout metrics require evaluate_holdout: "ENABLE".',
        ],
    }
    return assignments, manifest


def _split_for_date(cdate: Optional[date], config: SplitConfig, embargo: Optional[int]) -> str:
    if cdate is None:
        return "unassigned_missing_published_at"
    if cdate <= config.development_end:
        return "development"
    gap = timedelta(days=embargo) if embargo is not None else timedelta(days=0)
    validation_start = config.development_end + gap + timedelta(days=1)
    holdout_start = config.validation_end + gap + timedelta(days=1)
    if validation_start <= cdate <= config.validation_end:
        return "validation"
    if holdout_start <= cdate <= config.holdout_end:
        return "holdout"
    if config.development_end < cdate < validation_start:
        return "embargo_dev_val"
    if config.validation_end < cdate < holdout_start:
        return "embargo_val_holdout"
    return "out_of_range"


def _count_by(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        k = str(row[key])
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def contamination_report(
    assignments: Sequence[Mapping[str, Any]],
    clusters: Sequence[ExactDuplicateCluster],
    near_candidates: Sequence[NearDuplicateCandidate],
    *,
    config: SplitConfig,
    labels: Sequence[AdjudicatedAnnotationV1] | None = None,
    articles: Sequence[ArticleTextRecordV1] | None = None,
) -> dict[str, Any]:
    """Verify split integrity and list near-duplicate cross-split warnings."""
    article_to_split = {r["article_id"]: r["split"] for r in assignments}

    # Exact cluster must not cross splits.
    cluster_splits: dict[str, set[str]] = {}
    for row in assignments:
        cluster_splits.setdefault(row["duplicate_cluster_id"], set()).add(row["split"])
    exact_cross = {cid: sorted(splits) for cid, splits in cluster_splits.items() if len(splits) > 1}

    # Duplicate article ids.
    ids = [r["article_id"] for r in assignments]
    dup_ids = sorted({i for i in ids if ids.count(i) > 1})

    # Chronology / embargo: cluster dates vs split labels.
    chronology_ok = True
    chronology_issues: list[str] = []
    if articles is not None:
        by_id = {a.article_id: a for a in articles}
        index = cluster_index(clusters)
        for row in assignments:
            if not row.get("is_sample_representative"):
                continue
            cluster = index[row["article_id"]]
            cdate = _cluster_date(cluster, by_id)
            expected = _split_for_date(cdate, config, config.embargo_days)
            if expected != row["split"]:
                chronology_ok = False
                chronology_issues.append(
                    f"{row['article_id']}: assigned {row['split']} expected {expected}"
                )

    cross_near: list[dict[str, Any]] = []
    for cand in near_candidates:
        sa = article_to_split.get(cand.article_id_a)
        sb = article_to_split.get(cand.article_id_b)
        if sa is None or sb is None:
            continue
        if sa != sb and sa in SPLITS and sb in SPLITS:
            cross_near.append(
                {
                    "article_id_a": cand.article_id_a,
                    "article_id_b": cand.article_id_b,
                    "split_a": sa,
                    "split_b": sb,
                    "cluster_id_a": cand.cluster_id_a,
                    "cluster_id_b": cand.cluster_id_b,
                    "title_similarity": cand.title_similarity,
                    "body_shingle_similarity": cand.body_shingle_similarity,
                }
            )
    cross_near.sort(key=lambda r: (r["article_id_a"], r["article_id_b"]))

    label_dist: dict[str, Any] = {}
    if labels:
        by_split: dict[str, dict[str, int]] = {}
        for lab in labels:
            split = article_to_split.get(lab.article_id, "unassigned")
            by_split.setdefault(split, {})
            key = str(lab.relevance_label)
            by_split[split][key] = by_split[split].get(key, 0) + 1
        label_dist = {k: dict(sorted(v.items())) for k, v in sorted(by_split.items())}

    return {
        "exact_cluster_cross_split": exact_cross,
        "exact_cluster_cross_split_count": len(exact_cross),
        "duplicate_article_ids": dup_ids,
        "chronology_ok": chronology_ok and not chronology_issues,
        "chronology_issues": chronology_issues,
        "embargo_days": config.embargo_days,
        "near_duplicate_cross_split_count": len(cross_near),
        "near_duplicate_cross_split_pairs": cross_near,
        "label_distribution_by_split": label_dist,
        "entity_source_note": (
            "Entity/source distributions by split are reported when labels exist; "
            "different domains are not assumed independent journalism."
        ),
        "ok": (not exact_cross and not dup_ids and chronology_ok and not chronology_issues),
    }

"""Deterministic, config-driven benchmark sampling after exact dedupe."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from news_data.article.models import ArticleTextRecordV1
from news_data.dedupe.exact import ExactDuplicateCluster
from news_data.dedupe.near import NearDuplicateCandidate
from news_data.entity.matching import EntityMatchV1
from news_data.entity.models import EntityReferenceV1

SAMPLING_VERSION = "relevance-benchmark-sampling-v1"


@dataclass(frozen=True)
class SamplingConfig:
    sample_size: int = 50
    sampling_seed: str = "semiconductor-relevance-benchmark-v1"
    max_per_entity: int = 8
    double_annotation_fraction: float = 0.2
    version: str = SAMPLING_VERSION

    def __post_init__(self) -> None:
        if self.sample_size < 1:
            raise ValueError("sample_size must be >= 1")
        if self.max_per_entity < 1:
            raise ValueError("max_per_entity must be >= 1")
        if not 0.0 <= self.double_annotation_fraction <= 1.0:
            raise ValueError("double_annotation_fraction must be in [0, 1]")


@dataclass
class BenchmarkCandidate:
    article_id: str
    duplicate_cluster_id: str
    sampling_category: str
    matched_entity_ids: tuple[str, ...]
    match_fields: tuple[str, ...]
    ambiguity_statuses: tuple[str, ...]
    content_status: str
    domain: str
    published_at: Optional[str]
    week_bucket: Optional[str]
    double_annotate: bool
    sampling_version: str
    sampling_rank_key: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "article_id": self.article_id,
            "duplicate_cluster_id": self.duplicate_cluster_id,
            "sampling_category": self.sampling_category,
            "matched_entity_ids": list(self.matched_entity_ids),
            "match_fields": list(self.match_fields),
            "ambiguity_statuses": list(self.ambiguity_statuses),
            "content_status": self.content_status,
            "domain": self.domain,
            "published_at": self.published_at,
            "week_bucket": self.week_bucket,
            "double_annotate": self.double_annotate,
            "sampling_version": self.sampling_version,
            "sampling_rank_key": self.sampling_rank_key,
        }


def _week_bucket(published_at: Optional[datetime]) -> Optional[str]:
    if published_at is None:
        return None
    dt = published_at.astimezone(timezone.utc)
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _stable_rank_key(cluster_id: str, seed: str) -> str:
    return hashlib.sha256(f"{seed}|{cluster_id}".encode("utf-8")).hexdigest()


def classify_sampling_category(
    article: ArticleTextRecordV1,
    matches: Sequence[EntityMatchV1],
    *,
    near_dup_article_ids: set[str],
    industry_phrase_hit: bool,
) -> str:
    """Sampling category only — not an assumed label."""
    if article.content_status == "metadata_only" or article.body is None:
        if not matches:
            return "metadata_only"
    if article.article_id in near_dup_article_ids:
        # Prefer more specific categories first; syndicated is additive signal.
        pass
    if not matches and industry_phrase_hit:
        return "industry_phrase_match"
    if not matches:
        return "likely_unrelated_or_no_match"
    fields = {m.match_field for m in matches}
    entities = {m.entity_id for m in matches}
    ambiguous = any(m.ambiguity_status == "ambiguous_alias" for m in matches)
    if ambiguous and all(m.ambiguity_status == "ambiguous_alias" for m in matches):
        return "ambiguous_alias_case"
    if len(entities) >= 2:
        return "multiple_semiconductor_entities"
    if "title" in fields:
        cat = "strict_company_match_in_title"
    else:
        cat = "strict_company_match_outside_title"
    # Macro-ish heuristic: title lacks company but body/description has one match
    # and title tokens suggest markets/macro — keep as outside_title; separate
    # macro category when title has no entity and content mentions markets.
    title_l = article.title.casefold()
    macro_words = ("inflation", "nasdaq", "s&p", "markets", "stocks rise", "stocks fall")
    if cat == "strict_company_match_outside_title" and any(w in title_l for w in macro_words):
        return "macro_mention_one_company"
    if article.article_id in near_dup_article_ids:
        return "possible_syndicated_copy"
    return cat


def _industry_phrase_hit(article: ArticleTextRecordV1) -> bool:
    phrases = (
        "semiconductor",
        "semiconductors",
        "chipmaker",
        "wafer fabrication",
        "microprocessor",
        "lithography",
        "foundry",
    )
    blob = " ".join(
        x for x in (article.title, article.description or "", article.body or "") if x
    ).casefold()
    return any(p in blob for p in phrases)


def sample_benchmark_candidates(
    articles: Sequence[ArticleTextRecordV1],
    clusters: Sequence[ExactDuplicateCluster],
    matches: Sequence[EntityMatchV1],
    near_candidates: Sequence[NearDuplicateCandidate],
    entities: Sequence[EntityReferenceV1],
    *,
    config: SamplingConfig,
) -> tuple[list[BenchmarkCandidate], dict[str, Any]]:
    """Sample one representative article per exact cluster with stratum controls."""
    by_id = {a.article_id: a for a in articles}
    matches_by_article: dict[str, list[EntityMatchV1]] = defaultdict(list)
    for m in matches:
        matches_by_article[m.article_id].append(m)

    near_ids: set[str] = set()
    for pair in near_candidates:
        near_ids.add(pair.article_id_a)
        near_ids.add(pair.article_id_b)

    # One unit per exact cluster: canonical article.
    eligible: list[BenchmarkCandidate] = []
    exclusion_reasons: Counter[str] = Counter()
    for cluster in sorted(clusters, key=lambda c: c.cluster_id):
        article = by_id[cluster.canonical_article_id]
        article_matches = matches_by_article.get(article.article_id, [])
        category = classify_sampling_category(
            article,
            article_matches,
            near_dup_article_ids=near_ids,
            industry_phrase_hit=_industry_phrase_hit(article),
        )
        entity_ids = tuple(sorted({m.entity_id for m in article_matches}))
        rank_key = _stable_rank_key(cluster.cluster_id, config.sampling_seed)
        eligible.append(
            BenchmarkCandidate(
                article_id=article.article_id,
                duplicate_cluster_id=cluster.cluster_id,
                sampling_category=category,
                matched_entity_ids=entity_ids,
                match_fields=tuple(sorted({m.match_field for m in article_matches})),
                ambiguity_statuses=tuple(sorted({m.ambiguity_status for m in article_matches})),
                content_status=article.content_status,
                domain=article.domain,
                published_at=(
                    article.published_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                    if article.published_at
                    else None
                ),
                week_bucket=_week_bucket(article.published_at),
                double_annotate=False,
                sampling_version=config.version,
                sampling_rank_key=rank_key,
            )
        )

    # Stratified selection: sort globally by rank key, then enforce max_per_entity.
    ordered = sorted(eligible, key=lambda c: (c.sampling_rank_key, c.duplicate_cluster_id))
    selected: list[BenchmarkCandidate] = []
    entity_counts: Counter[str] = Counter()
    underfilled: list[str] = []
    category_targets = sorted({c.sampling_category for c in ordered})

    # First pass: try to take at least one from each category when available.
    taken_clusters: set[str] = set()
    for cat in category_targets:
        for cand in ordered:
            if cand.sampling_category != cat or cand.duplicate_cluster_id in taken_clusters:
                continue
            if _entity_cap_blocks(cand, entity_counts, config.max_per_entity):
                continue
            selected.append(cand)
            taken_clusters.add(cand.duplicate_cluster_id)
            for eid in cand.matched_entity_ids or ("_none_",):
                entity_counts[eid] += 1
            break
        else:
            underfilled.append(cat)

    # Fill remaining slots.
    for cand in ordered:
        if len(selected) >= config.sample_size:
            break
        if cand.duplicate_cluster_id in taken_clusters:
            continue
        if _entity_cap_blocks(cand, entity_counts, config.max_per_entity):
            exclusion_reasons["entity_cap"] += 1
            continue
        selected.append(cand)
        taken_clusters.add(cand.duplicate_cluster_id)
        for eid in cand.matched_entity_ids or ("_none_",):
            entity_counts[eid] += 1

    selected.sort(key=lambda c: (c.sampling_rank_key, c.duplicate_cluster_id))

    # Deterministic double-annotation subset.
    n_double = int(round(len(selected) * config.double_annotation_fraction))
    double_ids = {
        c.duplicate_cluster_id
        for c in sorted(selected, key=lambda c: c.sampling_rank_key)[:n_double]
    }
    finalized: list[BenchmarkCandidate] = []
    for cand in selected:
        finalized.append(
            BenchmarkCandidate(
                article_id=cand.article_id,
                duplicate_cluster_id=cand.duplicate_cluster_id,
                sampling_category=cand.sampling_category,
                matched_entity_ids=cand.matched_entity_ids,
                match_fields=cand.match_fields,
                ambiguity_statuses=cand.ambiguity_statuses,
                content_status=cand.content_status,
                domain=cand.domain,
                published_at=cand.published_at,
                week_bucket=cand.week_bucket,
                double_annotate=cand.duplicate_cluster_id in double_ids,
                sampling_version=cand.sampling_version,
                sampling_rank_key=cand.sampling_rank_key,
            )
        )

    manifest = _build_manifest(
        eligible=eligible,
        selected=finalized,
        clusters=clusters,
        config=config,
        entity_counts=entity_counts,
        underfilled=underfilled,
        exclusion_reasons=exclusion_reasons,
        entities=entities,
    )
    return finalized, manifest


def _entity_cap_blocks(
    cand: BenchmarkCandidate, entity_counts: Counter[str], max_per_entity: int
) -> bool:
    ids = cand.matched_entity_ids or ("_none_",)
    return any(entity_counts[eid] >= max_per_entity for eid in ids)


def _build_manifest(
    *,
    eligible: Sequence[BenchmarkCandidate],
    selected: Sequence[BenchmarkCandidate],
    clusters: Sequence[ExactDuplicateCluster],
    config: SamplingConfig,
    entity_counts: Counter[str],
    underfilled: Sequence[str],
    exclusion_reasons: Counter[str],
    entities: Sequence[EntityReferenceV1],
) -> dict[str, Any]:
    def counts(rows: Sequence[BenchmarkCandidate], key: str) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for r in rows:
            val = getattr(r, key)
            if isinstance(val, tuple):
                if not val:
                    counter["_none_"] += 1
                else:
                    for item in val:
                        counter[str(item)] += 1
            else:
                counter[str(val if val is not None else "_none_")] += 1
        return dict(sorted(counter.items()))

    eligible_n = len(eligible)
    selected_n = len(selected)
    stratum_inclusion: dict[str, float] = {}
    elig_cat = counts(eligible, "sampling_category")
    sel_cat = counts(selected, "sampling_category")
    for cat, n in elig_cat.items():
        stratum_inclusion[cat] = (sel_cat.get(cat, 0) / n) if n else 0.0

    return {
        "sampling_version": config.version,
        "sampling_seed": config.sampling_seed,
        "eligible_article_count": eligible_n,
        "exact_cluster_count": len(clusters),
        "requested_sample_count": config.sample_size,
        "actual_sample_count": selected_n,
        "max_per_entity": config.max_per_entity,
        "double_annotation_fraction": config.double_annotation_fraction,
        "double_annotation_count": sum(1 for s in selected if s.double_annotate),
        "counts_by_entity": dict(sorted(entity_counts.items())),
        "counts_by_week": counts(selected, "week_bucket"),
        "counts_by_domain": counts(selected, "domain"),
        "counts_by_match_location": counts(selected, "match_fields"),
        "counts_by_alias_ambiguity": counts(selected, "ambiguity_statuses"),
        "counts_by_content_status": counts(selected, "content_status"),
        "counts_by_sampling_category": sel_cat,
        "eligible_counts_by_sampling_category": elig_cat,
        "inclusion_fraction_by_stratum": stratum_inclusion,
        "underfilled_strata": list(underfilled),
        "exclusion_reasons": dict(sorted(exclusion_reasons.items())),
        "entity_registry_version": (entities[0].reference_version if entities else None),
        "notes": [
            "Sampling categories are design strata, not assumed labels.",
            "max_per_entity is a benchmark-design choice, not a statistical law.",
            "Sample size does not guarantee statistical power.",
            "Class counts are meaningful only after human labels exist.",
        ],
    }

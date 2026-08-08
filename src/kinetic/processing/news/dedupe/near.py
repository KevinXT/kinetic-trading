"""Level B: possible near-duplicate candidates for human review.

Uses transparent word-shingle Jaccard resemblance (Broder 1997). The similarity
threshold is a candidate-generation engineering choice, not a scientifically
validated duplicate threshold. Candidates are never auto-merged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timezone
from typing import Any, Optional, Sequence

from kinetic.data.schemas.news import ArticleTextRecordV1
from kinetic.data.serialization import to_json_value
from kinetic.processing.news.dedupe.exact import ExactDuplicateCluster, cluster_index
from kinetic.processing.news.normalize import normalize_text_for_hash

NEAR_DEDUPE_VERSION = "near-duplicate-candidate-v1"
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def title_tokens(text: str) -> frozenset[str]:
    normalized = normalize_text_for_hash(text)
    return frozenset(_TOKEN_RE.findall(normalized))


def word_shingles(text: str, *, n: int = 3) -> frozenset[str]:
    tokens = _TOKEN_RE.findall(normalize_text_for_hash(text))
    if len(tokens) < n:
        return frozenset(tokens)
    return frozenset(" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1))


def jaccard(a: frozenset[str], b: frozenset[str]) -> Optional[float]:
    if not a and not b:
        return None
    union = a | b
    if not union:
        return None
    return len(a & b) / len(union)


@dataclass(frozen=True)
class NearDuplicateCandidate:
    article_id_a: str
    article_id_b: str
    title_similarity: Optional[float]
    body_shingle_similarity: Optional[float]
    publication_time_distance_seconds: Optional[int]
    domain_a: str
    domain_b: str
    candidate_reason: str
    similarity_config_version: str
    human_review_status: str = "pending"
    cluster_id_a: Optional[str] = None
    cluster_id_b: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        payload = to_json_value(self)
        if not isinstance(payload, dict):
            raise TypeError("NearDuplicateCandidate must serialize to a dict")
        return payload


@dataclass(frozen=True)
class NearDuplicateConfig:
    title_similarity_threshold: float = 0.8
    body_similarity_threshold: float = 0.7
    max_publication_distance_hours: Optional[float] = 72.0
    shingle_size: int = 3
    require_compatible_language: bool = True
    version: str = NEAR_DEDUPE_VERSION

    def __post_init__(self) -> None:
        if not 0.0 <= self.title_similarity_threshold <= 1.0:
            raise ValueError("title_similarity_threshold must be in [0, 1]")
        if not 0.0 <= self.body_similarity_threshold <= 1.0:
            raise ValueError("body_similarity_threshold must be in [0, 1]")


def _pub_distance_seconds(a: ArticleTextRecordV1, b: ArticleTextRecordV1) -> Optional[int]:
    if a.published_at is None or b.published_at is None:
        return None
    delta = abs(a.published_at.astimezone(timezone.utc) - b.published_at.astimezone(timezone.utc))
    return int(delta.total_seconds())


def _language_compatible(a: ArticleTextRecordV1, b: ArticleTextRecordV1) -> bool:
    la = (a.language or "").strip().lower()
    lb = (b.language or "").strip().lower()
    if not la or not lb:
        # Missing language is treated as compatible for candidate generation.
        return True
    return la == lb


def generate_near_duplicate_candidates(
    articles: Sequence[ArticleTextRecordV1],
    clusters: Sequence[ExactDuplicateCluster],
    *,
    config: Optional[NearDuplicateConfig] = None,
) -> list[NearDuplicateCandidate]:
    """Emit possible near-duplicate pairs; never merge them.

    Skips pairs already in the same exact-duplicate cluster. Blocks by language
    compatibility and optional publication-time distance.
    """
    cfg = config or NearDuplicateConfig()
    by_id = {a.article_id: a for a in articles}
    index = cluster_index(clusters)
    ids = sorted(by_id)
    max_seconds: Optional[int] = None
    if cfg.max_publication_distance_hours is not None:
        max_seconds = int(cfg.max_publication_distance_hours * 3600)

    # Precompute features.
    title_feats = {i: title_tokens(by_id[i].title) for i in ids}
    body_feats: dict[str, Optional[frozenset[str]]] = {}
    for article_id in ids:
        body = by_id[article_id].body
        body_feats[article_id] = word_shingles(body, n=cfg.shingle_size) if body else None

    candidates: list[NearDuplicateCandidate] = []
    for idx, a_id in enumerate(ids):
        a = by_id[a_id]
        cluster_a = index[a_id].cluster_id
        for b_id in ids[idx + 1 :]:
            b = by_id[b_id]
            cluster_b = index[b_id].cluster_id
            if cluster_a == cluster_b:
                continue
            if cfg.require_compatible_language and not _language_compatible(a, b):
                continue
            distance = _pub_distance_seconds(a, b)
            if max_seconds is not None and distance is not None and distance > max_seconds:
                continue

            title_sim = jaccard(title_feats[a_id], title_feats[b_id])
            body_sim: Optional[float] = None
            if body_feats[a_id] is not None and body_feats[b_id] is not None:
                body_sim = jaccard(body_feats[a_id] or frozenset(), body_feats[b_id] or frozenset())

            reasons: list[str] = []
            if title_sim is not None and title_sim >= cfg.title_similarity_threshold:
                reasons.append("title_jaccard")
            if body_sim is not None and body_sim >= cfg.body_similarity_threshold:
                reasons.append("body_shingle_jaccard")
            # Metadata-only: title threshold alone can surface candidates.
            if not reasons:
                continue

            left, right = (a_id, b_id) if a_id < b_id else (b_id, a_id)
            left_art, right_art = by_id[left], by_id[right]
            candidates.append(
                NearDuplicateCandidate(
                    article_id_a=left,
                    article_id_b=right,
                    title_similarity=title_sim,
                    body_shingle_similarity=body_sim,
                    publication_time_distance_seconds=distance,
                    domain_a=left_art.domain,
                    domain_b=right_art.domain,
                    candidate_reason="+".join(reasons),
                    similarity_config_version=cfg.version,
                    human_review_status="pending",
                    cluster_id_a=index[left].cluster_id,
                    cluster_id_b=index[right].cluster_id,
                )
            )

    candidates.sort(key=lambda c: (c.article_id_a, c.article_id_b))
    return candidates

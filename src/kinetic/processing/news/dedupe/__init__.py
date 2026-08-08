"""Exact duplicate clustering and near-duplicate candidate generation."""

from kinetic.processing.news.dedupe.exact import (
    EXACT_DEDUPE_VERSION,
    ExactDuplicateCluster,
    cluster_exact_duplicates,
)
from kinetic.processing.news.dedupe.near import (
    NEAR_DEDUPE_VERSION,
    NearDuplicateCandidate,
    generate_near_duplicate_candidates,
    jaccard,
    title_tokens,
    word_shingles,
)

__all__ = [
    "EXACT_DEDUPE_VERSION",
    "NEAR_DEDUPE_VERSION",
    "ExactDuplicateCluster",
    "NearDuplicateCandidate",
    "cluster_exact_duplicates",
    "generate_near_duplicate_candidates",
    "jaccard",
    "title_tokens",
    "word_shingles",
]

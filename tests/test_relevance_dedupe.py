"""Exact and near-duplicate analysis tests."""

from __future__ import annotations

from datetime import datetime, timezone

from news_data.article.corpus import LocalArticleCorpusProvider
from news_data.dedupe.exact import cluster_exact_duplicates
from news_data.dedupe.near import (
    NearDuplicateConfig,
    generate_near_duplicate_candidates,
    jaccard,
    title_tokens,
    word_shingles,
)


def test_jaccard_hand_computed() -> None:
    a = frozenset({"a", "b", "c"})
    b = frozenset({"b", "c", "d"})
    assert jaccard(a, b) == 0.5
    assert jaccard(frozenset(), frozenset({"x"})) == 0.0


def test_shingles_and_title_tokens() -> None:
    assert "nvidia unveils next" in word_shingles("NVIDIA unveils next AI GPU", n=3)
    assert "nvidia" in title_tokens("NVIDIA Stock")


def test_exact_url_and_body_clusters_not_domain_alone() -> None:
    clock = datetime(2026, 7, 17, tzinfo=timezone.utc)
    records = (
        LocalArticleCorpusProvider(
            "tests/fixtures/research/relevance_benchmark/articles.jsonl",
            ingested_at=clock,
        )
        .import_articles()
        .records
    )
    clusters = cluster_exact_duplicates(records)
    multi = [c for c in clusters if len(c.member_article_ids) > 1]
    assert multi
    # Same normalized URL / body clusters exist.
    rules = {r for c in multi for r in c.grouping_rules}
    assert "same_normalized_url" in rules or "same_body_sha256" in rules
    # Domain-only pairs (different urls/titles/bodies) must not share a cluster.
    by_domain = {}
    for r in records:
        by_domain.setdefault(r.domain, []).append(r.article_id)
    # example.com domains differ across fixture hosts; ensure two random different
    # articles on distinct stories are not clustered solely by chance domain.
    ids = {r.provider_record_id: r.article_id for r in records}
    index = {a: c.cluster_id for c in clusters for a in c.member_article_ids}
    assert index[ids["q_a"]] != index[ids["q_b"]]
    assert index[ids["nvda_primary"]] != index[ids["nvda_index"]]


def test_near_duplicates_not_auto_merged_and_order_independent() -> None:
    clock = datetime(2026, 7, 17, tzinfo=timezone.utc)
    records = (
        LocalArticleCorpusProvider(
            "tests/fixtures/research/relevance_benchmark/articles.jsonl",
            ingested_at=clock,
        )
        .import_articles()
        .records
    )
    clusters = cluster_exact_duplicates(records)
    cfg = NearDuplicateConfig(
        title_similarity_threshold=0.8,
        body_similarity_threshold=0.7,
        max_publication_distance_hours=168,
    )
    near_a = generate_near_duplicate_candidates(records, clusters, config=cfg)
    near_b = generate_near_duplicate_candidates(list(reversed(records)), clusters, config=cfg)
    assert [c.to_dict() for c in near_a] == [c.to_dict() for c in near_b]
    # Near pairs exist but are not merged into exact clusters.
    assert near_a
    exact_ids = {c.cluster_id for c in clusters}
    for cand in near_a:
        assert cand.cluster_id_a != cand.cluster_id_b
        assert cand.cluster_id_a in exact_ids
        assert cand.human_review_status == "pending"


def test_cluster_ids_deterministic() -> None:
    clock = datetime(2026, 7, 17, tzinfo=timezone.utc)
    records = (
        LocalArticleCorpusProvider(
            "tests/fixtures/research/relevance_benchmark/articles.jsonl",
            ingested_at=clock,
        )
        .import_articles()
        .records
    )
    a = [c.cluster_id for c in cluster_exact_duplicates(records)]
    b = [c.cluster_id for c in cluster_exact_duplicates(list(reversed(records)))]
    assert a == b

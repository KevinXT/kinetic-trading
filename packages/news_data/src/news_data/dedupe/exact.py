"""Level A: exact duplicate grouping from strong deterministic evidence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

from news_data.article.models import ArticleTextRecordV1
from news_data.article.serialize import to_json_value

EXACT_DEDUPE_VERSION = "exact-duplicate-cluster-v1"


@dataclass(frozen=True)
class ExactDuplicateCluster:
    cluster_id: str
    member_article_ids: tuple[str, ...]
    canonical_article_id: str
    grouping_rules: tuple[str, ...]
    normalized_url: Optional[str]
    body_sha256: Optional[str]
    title_sha256: Optional[str]
    provider_record_key: Optional[str]
    domains: tuple[str, ...]
    exact_dedupe_version: str = EXACT_DEDUPE_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload = to_json_value(self)
        if not isinstance(payload, dict):
            raise TypeError("ExactDuplicateCluster must serialize to a dict")
        return payload


def _provider_key(article: ArticleTextRecordV1) -> str:
    return f"{article.provider}|{article.provider_record_id}"


def _union_find_parent(parent: dict[str, str], item: str) -> str:
    while parent[item] != item:
        parent[item] = parent[parent[item]]
        item = parent[item]
    return item


def _union(parent: dict[str, str], a: str, b: str) -> None:
    ra, rb = _union_find_parent(parent, a), _union_find_parent(parent, b)
    if ra == rb:
        return
    # Deterministic: lexicographically smaller root wins.
    if ra < rb:
        parent[rb] = ra
    else:
        parent[ra] = rb


def cluster_exact_duplicates(
    articles: Sequence[ArticleTextRecordV1],
) -> list[ExactDuplicateCluster]:
    """Cluster articles using strong deterministic identity evidence.

    Rules (any shared membership merges via union-find):
    - same_provider_record
    - same_normalized_url
    - same_body_sha256
    - same_title_sha256_and_normalized_url
    - same_title_sha256_and_provider_record

    Domain equality alone never clusters. Singleton clusters are still emitted
    so every article has a cluster_id for sampling/splits.
    """
    by_id = {a.article_id: a for a in articles}
    ids = sorted(by_id)
    parent = {i: i for i in ids}
    edge_rules: dict[tuple[str, str], set[str]] = {}

    def link(a: str, b: str, rule: str) -> None:
        if a == b:
            return
        left, right = (a, b) if a < b else (b, a)
        edge_rules.setdefault((left, right), set()).add(rule)
        _union(parent, a, b)

    # Index buckets.
    by_provider: dict[str, list[str]] = {}
    by_url: dict[str, list[str]] = {}
    by_body: dict[str, list[str]] = {}
    by_title: dict[str, list[str]] = {}

    for article in articles:
        by_provider.setdefault(_provider_key(article), []).append(article.article_id)
        by_url.setdefault(article.normalized_url, []).append(article.article_id)
        if article.body_sha256:
            by_body.setdefault(article.body_sha256, []).append(article.article_id)
        by_title.setdefault(article.title_sha256, []).append(article.article_id)

    def link_all(group: list[str], rule: str) -> None:
        ordered = sorted(set(group))
        if len(ordered) < 2:
            return
        head = ordered[0]
        for other in ordered[1:]:
            link(head, other, rule)

    for group in by_provider.values():
        link_all(group, "same_provider_record")
    for group in by_url.values():
        link_all(group, "same_normalized_url")
    for group in by_body.values():
        link_all(group, "same_body_sha256")

    # Title + corroborating field.
    for title_hash, group in by_title.items():
        if len(group) < 2:
            continue
        ordered_group = sorted(group)
        for i, left_id in enumerate(ordered_group):
            left = by_id[left_id]
            for right_id in ordered_group[i + 1 :]:
                right = by_id[right_id]
                if left.normalized_url == right.normalized_url:
                    link(left_id, right_id, "same_title_sha256_and_normalized_url")
                if _provider_key(left) == _provider_key(right):
                    link(left_id, right_id, "same_title_sha256_and_provider_record")
                # Title + same body hash is already covered by body rule; also
                # allow title + identical body when body present.
                if left.body_sha256 and left.body_sha256 == right.body_sha256:
                    link(left_id, right_id, "same_title_sha256_and_body_sha256")

    roots: dict[str, list[str]] = {}
    for article_id in ids:
        root = _union_find_parent(parent, article_id)
        roots.setdefault(root, []).append(article_id)

    clusters: list[ExactDuplicateCluster] = []
    for members in roots.values():
        members_sorted = tuple(sorted(members))
        canonical = members_sorted[0]
        rules: set[str] = set()
        for i, left_id in enumerate(members_sorted):
            for right_id in members_sorted[i + 1 :]:
                key = (left_id, right_id) if left_id < right_id else (right_id, left_id)
                rules.update(edge_rules.get(key, set()))
        if len(members_sorted) == 1:
            rules = {"singleton"}
        member_articles = [by_id[m] for m in members_sorted]
        urls = {art.normalized_url for art in member_articles}
        bodies = {art.body_sha256 for art in member_articles if art.body_sha256}
        titles = {art.title_sha256 for art in member_articles}
        providers = {_provider_key(art) for art in member_articles}
        domains = tuple(sorted({art.domain for art in member_articles}))
        material = "|".join(members_sorted) + "|" + "|".join(sorted(rules))
        cluster_id = "cluster_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
        clusters.append(
            ExactDuplicateCluster(
                cluster_id=cluster_id,
                member_article_ids=members_sorted,
                canonical_article_id=canonical,
                grouping_rules=tuple(sorted(rules)),
                normalized_url=next(iter(urls)) if len(urls) == 1 else None,
                body_sha256=next(iter(bodies)) if len(bodies) == 1 else None,
                title_sha256=next(iter(titles)) if len(titles) == 1 else None,
                provider_record_key=next(iter(providers)) if len(providers) == 1 else None,
                domains=domains,
            )
        )

    clusters.sort(key=lambda c: c.cluster_id)
    return clusters


def cluster_index(
    clusters: Iterable[ExactDuplicateCluster],
) -> dict[str, ExactDuplicateCluster]:
    """Map article_id -> cluster."""
    out: dict[str, ExactDuplicateCluster] = {}
    for cluster in clusters:
        for article_id in cluster.member_article_ids:
            out[article_id] = cluster
    return out

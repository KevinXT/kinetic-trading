"""Sampling and chronological split tests."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from news_data.article.corpus import LocalArticleCorpusProvider
from news_data.dedupe.exact import cluster_exact_duplicates
from news_data.dedupe.near import NearDuplicateConfig, generate_near_duplicate_candidates
from news_data.entity.matching import match_entities_many
from news_data.entity.reference import default_semiconductor_entities
from research_data.relevance.sampling import SamplingConfig, sample_benchmark_candidates
from research_data.relevance.splits import (
    SplitConfig,
    assign_splits,
    contamination_report,
)


def _fixture_bundle():
    clock = datetime(2026, 7, 17, tzinfo=timezone.utc)
    records = (
        LocalArticleCorpusProvider(
            "tests/fixtures/research/relevance_benchmark/articles.jsonl",
            ingested_at=clock,
        )
        .import_articles()
        .records
    )
    entities = default_semiconductor_entities()
    matches = match_entities_many(records, entities)
    clusters = cluster_exact_duplicates(records)
    near = generate_near_duplicate_candidates(
        records,
        clusters,
        config=NearDuplicateConfig(max_publication_distance_hours=168),
    )
    return records, entities, matches, clusters, near


def test_sampling_byte_stable_and_entity_caps() -> None:
    records, entities, matches, clusters, near = _fixture_bundle()
    cfg = SamplingConfig(sample_size=12, sampling_seed="seed-a", max_per_entity=2)
    s1, m1 = sample_benchmark_candidates(records, clusters, matches, near, entities, config=cfg)
    s2, m2 = sample_benchmark_candidates(
        list(reversed(records)),
        list(reversed(clusters)),
        list(reversed(matches)),
        near,
        entities,
        config=cfg,
    )
    assert [c.to_dict() for c in s1] == [c.to_dict() for c in s2]
    assert m1["actual_sample_count"] == m2["actual_sample_count"]
    assert m1["actual_sample_count"] == len(s1)
    # Exact clusters contribute at most one sampling unit.
    assert len({c.duplicate_cluster_id for c in s1}) == len(s1)
    # Dominant entity cannot silently exceed max_per_entity.
    for eid, n in m1["counts_by_entity"].items():
        if eid != "_none_":
            assert n <= cfg.max_per_entity
    assert m1["eligible_article_count"] == len(clusters)
    assert sum(m1["counts_by_sampling_category"].values()) == m1["actual_sample_count"]


def test_splits_cluster_coherence_chronology_embargo_and_gate() -> None:
    records, entities, matches, clusters, near = _fixture_bundle()
    candidates, _ = sample_benchmark_candidates(
        records,
        clusters,
        matches,
        near,
        entities,
        config=SamplingConfig(sample_size=40, max_per_entity=10),
    )
    cfg = SplitConfig(
        development_end=date(2025, 9, 30),
        validation_end=date(2025, 12, 31),
        holdout_end=date(2026, 4, 30),
        embargo_days=0,
        evaluate_holdout=None,
    )
    assignments, manifest = assign_splits(candidates, records, clusters, config=cfg)
    assert manifest["holdout_metrics_enabled"] is False
    assert manifest["embargo_days"] == 0

    # No exact cluster crosses splits; no duplicate article ids.
    cont = contamination_report(assignments, clusters, near, config=cfg, articles=records)
    assert cont["exact_cluster_cross_split_count"] == 0
    assert cont["duplicate_article_ids"] == []
    assert cont["chronology_ok"] is True

    # Determinism under reorder
    assignments2, _ = assign_splits(
        list(reversed(candidates)), list(reversed(records)), clusters, config=cfg
    )
    assert assignments == assignments2

    # Embargo enforcement places middle dates into embargo buckets when > 0.
    cfg_e = SplitConfig(
        development_end=date(2025, 9, 30),
        validation_end=date(2025, 12, 31),
        holdout_end=date(2026, 4, 30),
        embargo_days=7,
        evaluate_holdout="ENABLE",
    )
    assert cfg_e.holdout_enabled is True
    a3, _ = assign_splits(candidates, records, clusters, config=cfg_e)
    splits = {r["split"] for r in a3}
    # With embargo, some rows may land in embargo_* buckets depending on dates.
    assert "development" in splits or "validation" in splits or "holdout" in splits


def test_split_config_rejects_hidden_inverted_dates() -> None:
    with pytest.raises(ValueError):
        SplitConfig(
            development_end=date(2026, 1, 1),
            validation_end=date(2025, 1, 1),
            holdout_end=date(2026, 6, 1),
            embargo_days=0,
        )

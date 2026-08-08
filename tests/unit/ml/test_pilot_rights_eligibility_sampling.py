"""Rights, eligibility, probability sampling, and content-safety tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from kinetic.data.catalog.entities import load_entity_references
from kinetic.ingestion.news.local_corpus import LocalArticleCorpusProvider
from kinetic.ml.relevance.content_controls import (
    RealContentControlsV1,
    assert_path_safe_for_real_content,
    is_git_tracked_research_path,
    truncate_excerpt,
)
from kinetic.ml.relevance.eligibility import (
    PilotCorpusEligibilityV1,
    evaluate_corpus_eligibility,
)
from kinetic.ml.relevance.pilot_sampling import (
    RepresentativeSampleConfig,
    assign_double_annotation,
    build_sampling_frame,
    merge_annotation_units,
    sample_challenge_purposive,
    sample_representative_probability,
)
from kinetic.ml.relevance.provenance import (
    ArticleContentProvenanceV1,
    load_provenance_jsonl,
)
from kinetic.processing.news.dedupe.exact import cluster_exact_duplicates
from kinetic.processing.news.entity_linking import match_entities_many

FIXED = datetime(2026, 7, 17, 15, 0, 0, tzinfo=timezone.utc)
FIXTURE = Path("tests/fixtures/research/relevance_pilot")


def _load_corpus():
    provider = LocalArticleCorpusProvider(
        path=FIXTURE / "articles.jsonl",
        default_content_source="synthetic_fixture",
        ingested_at=FIXED,
    )
    imported = provider.import_articles()
    provenance = load_provenance_jsonl(str(FIXTURE / "provenance.jsonl"))
    return imported.records, {p.article_id: p for p in provenance}, imported


def test_rights_unclear_rejected() -> None:
    records, prov, imported = _load_corpus()
    clusters = cluster_exact_duplicates(records)
    entities = load_entity_references(None)
    matches = match_entities_many(records, entities)
    decisions, counts, _ = evaluate_corpus_eligibility(
        records,
        prov,
        clusters,
        rules=PilotCorpusEligibilityV1(),
        schema_rejected=len(imported.rejections),
        entity_or_industry_ids={m.article_id for m in matches},
    )
    unclear = [d for d in decisions if "RIGHTS_UNCLEAR" in d.reason_codes]
    assert unclear
    assert counts.rights_ineligible >= 1
    assert counts.sampling_frame_units < len(clusters)


def test_missing_publication_time_excluded() -> None:
    records, prov, imported = _load_corpus()
    clusters = cluster_exact_duplicates(records)
    decisions, _, _ = evaluate_corpus_eligibility(
        records, prov, clusters, rules=PilotCorpusEligibilityV1(), schema_rejected=0
    )
    assert any("MISSING_PUBLICATION_TIME" in d.reason_codes for d in decisions)


def test_body_commit_forbidden_for_unclear_rights() -> None:
    with pytest.raises(ValueError):
        ArticleContentProvenanceV1(
            article_id="x",
            content_origin="web",
            provider_name="p",
            source_url="https://example.test/a",
            acquisition_method="user",
            acquired_at=FIXED,
            rights_status="rights_unclear",
            license_name=None,
            license_reference=None,
            storage_allowed=False,
            research_use_allowed=False,
            redistribution_allowed=False,
            body_commit_allowed=True,
            retention_policy="none",
            provenance_notes=None,
        )


def test_git_tracked_path_fail_closed() -> None:
    controls = RealContentControlsV1()
    assert is_git_tracked_research_path("tests/fixtures/research/relevance_pilot/x.jsonl")
    with pytest.raises(ValueError):
        assert_path_safe_for_real_content(
            "tests/fixtures/research/foo.jsonl",
            controls=controls,
            contains_real_body=True,
        )


def test_excerpt_truncation() -> None:
    text = "a" * 500
    out = truncate_excerpt(text, max_chars=280)
    assert out is not None
    assert len(out) <= 280


def test_probability_sampling_weights_and_determinism() -> None:
    records, prov, _ = _load_corpus()
    clusters = cluster_exact_duplicates(records)
    entities = load_entity_references(None)
    matches = match_entities_many(records, entities)
    decisions, _, _ = evaluate_corpus_eligibility(
        records, prov, clusters, rules=PilotCorpusEligibilityV1(), schema_rejected=0
    )
    frame = build_sampling_frame(records, clusters, matches, decisions, prov)
    cfg = RepresentativeSampleConfig(target_n=12, sampling_seed="seed-a")
    a, man_a = sample_representative_probability(frame, config=cfg)
    b, man_b = sample_representative_probability(list(reversed(frame)), config=cfg)
    assert [u.cluster_id for u in a] == [u.cluster_id for u in b]
    assert man_a["stratum_sample_sizes"] == man_b["stratum_sample_sizes"]
    assert man_a["probability_sample"] is True
    for u in a:
        assert u.inclusion_probability is not None
        assert u.design_weight is not None
        assert abs(u.inclusion_probability * u.design_weight - 1.0) < 1e-9
    assert len({u.cluster_id for u in a}) == len(a)


def test_challenge_and_overlap_single_unit() -> None:
    records, prov, _ = _load_corpus()
    clusters = cluster_exact_duplicates(records)
    entities = load_entity_references(None)
    matches = match_entities_many(records, entities)
    decisions, _, _ = evaluate_corpus_eligibility(
        records, prov, clusters, rules=PilotCorpusEligibilityV1(), schema_rejected=0
    )
    frame = build_sampling_frame(records, clusters, matches, decisions, prov)
    rep, _ = sample_representative_probability(
        frame, config=RepresentativeSampleConfig(target_n=10, sampling_seed="s")
    )
    chal, chal_man = sample_challenge_purposive(frame, target_n=8, sampling_seed="s")
    assert chal_man["inferential_use"] is False
    assert any(u.challenge_reason_codes for u in chal)
    merged, overlap = merge_annotation_units(rep, chal)
    assert overlap["unique_annotation_units"] == len(merged)
    assert overlap["representative_count"] + overlap["challenge_count"] >= len(merged)


def test_double_annotation_prelabel_selection() -> None:
    records, prov, _ = _load_corpus()
    clusters = cluster_exact_duplicates(records)
    entities = load_entity_references(None)
    matches = match_entities_many(records, entities)
    decisions, _, _ = evaluate_corpus_eligibility(
        records, prov, clusters, rules=PilotCorpusEligibilityV1(), schema_rejected=0
    )
    frame = build_sampling_frame(records, clusters, matches, decisions, prov)
    rep, _ = sample_representative_probability(
        frame, config=RepresentativeSampleConfig(target_n=10, sampling_seed="s")
    )
    out = assign_double_annotation(rep, target_count=4, sampling_seed="s")
    assert sum(1 for u in out if u.double_annotate) == 4

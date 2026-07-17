"""Annotation UI service and content-safety tests."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from news_data.article.models import ArticleTextRecordV1
from news_data.article.normalize import (
    NORMALIZER_VERSION,
    normalize_text_for_hash,
    sha256_hex,
)

sys.path.insert(0, str(Path("apps/relevance_annotation_ui").resolve()))

from app_config import UIConfig
from pilot_service import PilotService
from view_models import assert_no_prohibited_fields, find_evidence_offsets

FIXED = datetime(2026, 7, 17, 15, 0, 0, tzinfo=timezone.utc)


def _article(body: str = "NVIDIA Corporation detailed GPU plans.") -> ArticleTextRecordV1:
    title = "NVIDIA unveils roadmap"
    return ArticleTextRecordV1(
        article_id="art_test_1",
        provider="synthetic",
        provider_record_id="1",
        url="https://example-tech.test/a/1",
        normalized_url="https://example-tech.test/a/1",
        canonical_url=None,
        domain="example-tech.test",
        language="en",
        source_country="US",
        title=title,
        description="Short description.",
        body=body,
        published_at=FIXED,
        ingested_at=FIXED,
        retrieved_at=FIXED,
        content_status="available",
        content_source="synthetic_fixture",
        title_sha256=sha256_hex(normalize_text_for_hash(title)),
        body_sha256=sha256_hex(normalize_text_for_hash(body)),
        normalization_version=NORMALIZER_VERSION,
    )


def _service(tmp_path: Path) -> PilotService:
    cfg = UIConfig.from_mapping(
        {
            "ui": {"database_path": str(tmp_path / "ui.sqlite3")},
            "pilot": {
                "config_path": (
                    "configs/research/semiconductor_relevance_real_corpus_pilot_local.yaml"
                ),
                "local_export_root": str(tmp_path / "exports"),
            },
            "content_controls": {"allow_full_body_in_exports": False},
        },
        config_path=tmp_path / "ui.yaml",
    )
    return PilotService(cfg, repo_root=Path.cwd())


def test_valid_submission_and_cannot_determine(tmp_path: Path) -> None:
    service = _service(tmp_path)
    art = _article()
    service.store.upsert_annotation_assignment(
        assignment_id="as1",
        article_id=art.article_id,
        duplicate_cluster_id="c1",
        annotator_id="a1",
        batch_id="b1",
        guideline_version="g1",
        assignment_order=1,
    )
    service.validate_and_save_annotation(
        assignment_id="as1",
        article=art,
        annotator_id="a1",
        client_submission_id="s1",
        relevance_label=3,
        cannot_determine=False,
        cannot_determine_reason=None,
        central_entity_ids=("NVIDIA",),
        secondary_entity_ids=(),
        evidence_excerpt="NVIDIA Corporation detailed GPU plans.",
        content_sufficient=True,
        uncertain=False,
        uncertainty_reason=None,
        decision_reason_code="OK",
        notes=None,
    )
    with pytest.raises(ValueError):
        service.validate_and_save_annotation(
            assignment_id="as1",
            article=art,
            annotator_id="a1",
            client_submission_id="s2",
            relevance_label=0,
            cannot_determine=True,
            cannot_determine_reason="BODY_UNAVAILABLE",
            central_entity_ids=(),
            secondary_entity_ids=(),
            evidence_excerpt=None,
            content_sufficient=False,
            uncertain=False,
            uncertainty_reason=None,
            decision_reason_code="X",
            notes=None,
        )
    with pytest.raises(ValueError):
        service.validate_and_save_annotation(
            assignment_id="as1",
            article=art,
            annotator_id="a1",
            client_submission_id="s3",
            relevance_label=None,
            cannot_determine=True,
            cannot_determine_reason=None,
            central_entity_ids=(),
            secondary_entity_ids=(),
            evidence_excerpt=None,
            content_sufficient=False,
            uncertain=False,
            uncertainty_reason=None,
            decision_reason_code="X",
            notes=None,
        )
    service.validate_and_save_annotation(
        assignment_id="as1",
        article=art,
        annotator_id="a1",
        client_submission_id="s4",
        relevance_label=None,
        cannot_determine=True,
        cannot_determine_reason="BODY_UNAVAILABLE",
        central_entity_ids=(),
        secondary_entity_ids=(),
        evidence_excerpt=None,
        content_sufficient=False,
        uncertain=False,
        uncertainty_reason=None,
        decision_reason_code="X",
        notes=None,
    )
    state = service.store.current_annotation_state("as1")
    assert state is not None
    assert state.cannot_determine is True
    assert state.relevance_label is None


def test_evidence_offsets_and_rejection(tmp_path: Path) -> None:
    start, end, field, cands = find_evidence_offsets(
        excerpt="NVIDIA Corporation",
        title="NVIDIA unveils roadmap",
        description="Short description.",
        body="NVIDIA Corporation detailed GPU plans.",
    )
    assert field == "body"
    assert start is not None and end is not None
    service = _service(tmp_path)
    art = _article()
    service.store.upsert_annotation_assignment(
        assignment_id="as2",
        article_id=art.article_id,
        duplicate_cluster_id="c1",
        annotator_id="a1",
        batch_id="b1",
        guideline_version="g1",
        assignment_order=1,
    )
    with pytest.raises(ValueError, match="not found"):
        service.validate_and_save_annotation(
            assignment_id="as2",
            article=art,
            annotator_id="a1",
            client_submission_id="s1",
            relevance_label=2,
            cannot_determine=False,
            cannot_determine_reason=None,
            central_entity_ids=(),
            secondary_entity_ids=(),
            evidence_excerpt="this excerpt is absent",
            content_sufficient=True,
            uncertain=False,
            uncertainty_reason=None,
            decision_reason_code="X",
            notes=None,
        )


def test_blind_view_excludes_prohibited(tmp_path: Path) -> None:
    service = _service(tmp_path)
    art = _article()
    assignment = {
        "assignment_id": "as3",
        "article_id": art.article_id,
        "duplicate_cluster_id": "c1",
        "annotator_id": "a1",
        "guideline_version": "g1",
    }
    view = service.build_blind_article_view(
        assignment=assignment,
        article=art,
        queue_index=1,
        queue_total=1,
        entity_names=("NVIDIA",),
    )
    payload = view.to_dict()
    assert_no_prohibited_fields(payload)
    for banned in (
        "baseline",
        "challenge_reason",
        "design_weight",
        "inclusion_probability",
        "sample_roles",
    ):
        assert banned not in payload


def test_duplicate_and_adjudication_append_only(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.store.upsert_duplicate_assignment(
        assignment_id="d1",
        pair_id="p1",
        article_id_a="a",
        article_id_b="b",
        reviewer_id="r1",
        batch_id="b",
        assignment_order=1,
    )
    e1 = service.save_duplicate_review(
        assignment_id="d1",
        pair_id="p1",
        reviewer_id="r1",
        client_submission_id="c1",
        pair_label="UNRELATED",
        uncertain=False,
        reason="different events",
    )
    e1b = service.save_duplicate_review(
        assignment_id="d1",
        pair_id="p1",
        reviewer_id="r1",
        client_submission_id="c1",
        pair_label="UNRELATED",
        uncertain=False,
        reason="different events",
    )
    assert e1["event_id"] == e1b["event_id"]
    with pytest.raises(ValueError):
        service.save_duplicate_review(
            assignment_id="d1",
            pair_id="p1",
            reviewer_id="r1",
            client_submission_id="c2",
            pair_label="CANNOT_DETERMINE",
            uncertain=True,
            reason="",
        )

    # Seed two annotation events for adjudication path
    service.store.upsert_annotation_assignment(
        assignment_id="asA",
        article_id="artX",
        duplicate_cluster_id="cX",
        annotator_id="a1",
        batch_id="b",
        guideline_version="g",
        assignment_order=1,
    )
    service.store.upsert_annotation_assignment(
        assignment_id="asB",
        article_id="artX",
        duplicate_cluster_id="cX",
        annotator_id="a2",
        batch_id="b",
        guideline_version="g",
        assignment_order=2,
    )
    base = _article()
    art = ArticleTextRecordV1(
        article_id="artX",
        provider=base.provider,
        provider_record_id=base.provider_record_id,
        url=base.url,
        normalized_url=base.normalized_url,
        canonical_url=None,
        domain=base.domain,
        language="en",
        source_country="US",
        title=base.title,
        description=base.description,
        body=base.body,
        published_at=FIXED,
        ingested_at=FIXED,
        retrieved_at=FIXED,
        content_status="available",
        content_source="synthetic_fixture",
        title_sha256=base.title_sha256,
        body_sha256=base.body_sha256,
        normalization_version=base.normalization_version,
    )
    service.validate_and_save_annotation(
        assignment_id="asA",
        article=art,
        annotator_id="a1",
        client_submission_id="1",
        relevance_label=1,
        cannot_determine=False,
        cannot_determine_reason=None,
        central_entity_ids=(),
        secondary_entity_ids=(),
        evidence_excerpt=None,
        content_sufficient=True,
        uncertain=False,
        uncertainty_reason=None,
        decision_reason_code="X",
        notes=None,
    )
    service.validate_and_save_annotation(
        assignment_id="asB",
        article=art,
        annotator_id="a2",
        client_submission_id="1",
        relevance_label=3,
        cannot_determine=False,
        cannot_determine_reason=None,
        central_entity_ids=(),
        secondary_entity_ids=(),
        evidence_excerpt=None,
        content_sufficient=True,
        uncertain=False,
        uncertainty_reason=None,
        decision_reason_code="X",
        notes=None,
    )
    before = service.store.export_latest_annotation_events()
    before_ids = {r["event_id"] for r in before}
    service.store.upsert_adjudication_assignment(
        assignment_id="adj1",
        article_id="artX",
        duplicate_cluster_id="cX",
        adjudicator_id="adj",
        guideline_version="g",
        raw_event_ids=["x", "y"],
    )
    service.save_adjudication(
        assignment_id="adj1",
        article_id="artX",
        adjudicator_id="adj",
        client_submission_id="z",
        relevance_label=2,
        cannot_determine=False,
        cannot_determine_reason=None,
        central_entity_ids=(),
        secondary_entity_ids=(),
        disagreement_cause="ORDINAL_BOUNDARY_1_2",
        adjudication_reason="boundary case between incidental and secondary",
        raw_labels=[1, 3],
    )
    after = service.store.export_latest_annotation_events()
    assert {r["event_id"] for r in after} == before_ids
    assert {r["relevance_label"] for r in after} == {1, 3}


def test_export_atomic_and_no_tracked_path(tmp_path: Path) -> None:
    service = _service(tmp_path)
    art = _article()
    service.store.upsert_annotation_assignment(
        assignment_id="asE",
        article_id=art.article_id,
        duplicate_cluster_id="c1",
        annotator_id="a1",
        batch_id="b1",
        guideline_version="g1",
        assignment_order=1,
    )
    service.validate_and_save_annotation(
        assignment_id="asE",
        article=art,
        annotator_id="a1",
        client_submission_id="e1",
        relevance_label=2,
        cannot_determine=False,
        cannot_determine_reason=None,
        central_entity_ids=(),
        secondary_entity_ids=(),
        evidence_excerpt=None,
        content_sufficient=True,
        uncertain=False,
        uncertainty_reason=None,
        decision_reason_code="X",
        notes=None,
    )
    dest = tmp_path / "exports" / "raw.jsonl"
    result = service.export_raw_annotations_jsonl(destination=dest, guideline_version="g1")
    assert dest.is_file()
    assert result["record_count"] == 1
    line = dest.read_text(encoding="utf-8").strip()
    obj = json.loads(line)
    assert "body" not in obj
    with pytest.raises(ValueError, match="git-tracked"):
        service.export_raw_annotations_jsonl(
            destination=Path("tests/fixtures/research/relevance_pilot/leak.jsonl"),
            guideline_version="g1",
        )


def test_script_tag_is_plain_text_in_view(tmp_path: Path) -> None:
    service = _service(tmp_path)
    art = _article(body='<script>alert("x")</script> NVIDIA Corporation')
    view = service.build_blind_article_view(
        assignment={
            "assignment_id": "asS",
            "article_id": art.article_id,
            "duplicate_cluster_id": "c",
            "annotator_id": "a",
            "guideline_version": "g",
        },
        article=art,
        queue_index=1,
        queue_total=1,
        entity_names=(),
    )
    assert "<script>" in view.body  # stored as plain text content
    # UI uses st.text which escapes; service never marks HTML safe.

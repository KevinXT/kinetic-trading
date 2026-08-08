"""Annotation UI service and content-safety tests."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kinetic.data.catalog.entities import SEMICONDUCTOR_FIXTURE_VERSION
from kinetic.data.schemas.news import ArticleTextRecordV1
from kinetic.processing.news.normalize import (
    NORMALIZER_VERSION,
    normalize_text_for_hash,
    sha256_hex,
)

sys.path.insert(0, str(Path("tools/annotation").resolve()))

from annotation_store import utc_now  # noqa: E402
from app_config import UIConfig  # noqa: E402
from pilot_context import (  # noqa: E402
    ASSIGNMENT_SCHEMA_VERSION,
    SourceArtifactMismatch,
    form_fingerprint,
    get_or_create_submission_token,
    rotate_submission_token,
)
from pilot_service import PilotService, hash_article_records_artifact  # noqa: E402
from view_models import (  # noqa: E402
    assert_no_prohibited_fields,
    find_evidence_offsets,
    generate_evidence_candidates,
    resolve_evidence_candidate,
)

FIXED = datetime(2026, 7, 17, 15, 0, 0, tzinfo=timezone.utc)


def _article(
    body: str = "NVIDIA Corporation detailed GPU plans.",
    *,
    article_id: str = "art_test_1",
    title: str = "NVIDIA unveils roadmap",
) -> ArticleTextRecordV1:
    return ArticleTextRecordV1(
        article_id=article_id,
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


def _service(tmp_path: Path, *, clock=None) -> PilotService:
    cfg = UIConfig.from_mapping(
        {
            "ui": {"database_path": str(tmp_path / "ui.sqlite3")},
            "pilot": {
                "config_path": (
                    "projects/semiconductor_case_study/configs/"
                    "semiconductor_relevance_real_corpus_pilot_local.yaml"
                ),
                "local_export_root": str(tmp_path / "exports"),
            },
            "content_controls": {"allow_full_body_in_exports": False},
        },
        config_path=tmp_path / "ui.yaml",
    )
    return PilotService(cfg, repo_root=Path.cwd(), clock=clock)


def _bind_assignment(
    service: PilotService,
    art: ArticleTextRecordV1,
    *,
    assignment_id: str = "as1",
    annotator_id: str = "a1",
    batch_id: str = "b1",
    guideline_version: str = "g1",
    sample_roles: tuple[str, ...] = ("calibration",),
    run_id: str = "run1",
) -> None:
    service.store.upsert_annotation_assignment(
        assignment_id=assignment_id,
        article_id=art.article_id,
        duplicate_cluster_id="c1",
        annotator_id=annotator_id,
        batch_id=batch_id,
        guideline_version=guideline_version,
        assignment_order=1,
        source_run_id=run_id,
        source_task_name="test",
        source_artifact_root="/tmp",
        source_assignment_manifest_sha256="m" * 64,
        source_corpus_sha256=hash_article_records_artifact([art]),
        source_article_records_sha256="f" * 64,
        article_title_sha256=art.title_sha256,
        article_body_sha256=art.body_sha256,
        article_normalization_version=art.normalization_version,
        sample_roles=sample_roles,
        entity_reference_version=SEMICONDUCTOR_FIXTURE_VERSION,
        assignment_schema_version=ASSIGNMENT_SCHEMA_VERSION,
    )


def test_default_clock_is_not_hardcoded_july_2026(tmp_path: Path) -> None:
    before = utc_now()
    service = _service(tmp_path)
    now = service.now()
    after = utc_now()
    assert now.tzinfo is not None
    assert before <= now <= after
    assert now != FIXED


def test_valid_submission_and_cannot_determine(tmp_path: Path) -> None:
    service = _service(tmp_path)
    art = _article()
    _bind_assignment(service, art)
    service.validate_and_save_annotation(
        assignment_id="as1",
        article=art,
        annotator_id="a1",
        client_submission_id="s1",
        relevance_label=3,
        cannot_determine=False,
        cannot_determine_reason=None,
        central_entity_ids=("ent_nvidia",),
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


def test_hash_mismatch_blocks_save(tmp_path: Path) -> None:
    service = _service(tmp_path)
    art = _article()
    _bind_assignment(service, art)
    changed = _article(body="CHANGED BODY TEXT that must block save.")
    with pytest.raises(SourceArtifactMismatch):
        service.validate_and_save_annotation(
            assignment_id="as1",
            article=changed,
            annotator_id="a1",
            client_submission_id="s1",
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


def test_entity_ids_not_display_names(tmp_path: Path) -> None:
    service = _service(tmp_path)
    art = _article()
    _bind_assignment(service, art)
    # Display name is accepted only via unique mapping to canonical ID.
    service.validate_and_save_annotation(
        assignment_id="as1",
        article=art,
        annotator_id="a1",
        client_submission_id="s1",
        relevance_label=2,
        cannot_determine=False,
        cannot_determine_reason=None,
        central_entity_ids=("NVIDIA",),
        secondary_entity_ids=(),
        evidence_excerpt=None,
        content_sufficient=True,
        uncertain=False,
        uncertainty_reason=None,
        decision_reason_code="X",
        notes=None,
    )
    state = service.store.current_annotation_state("as1")
    assert state is not None
    assert state.central_entity_ids == ("ent_nvidia",)
    with pytest.raises(ValueError, match="unknown entity_id"):
        service.validate_and_save_annotation(
            assignment_id="as1",
            article=art,
            annotator_id="a1",
            client_submission_id="s2",
            relevance_label=2,
            cannot_determine=False,
            cannot_determine_reason=None,
            central_entity_ids=("NotARealCompany",),
            secondary_entity_ids=(),
            evidence_excerpt=None,
            content_sufficient=True,
            uncertain=False,
            uncertainty_reason=None,
            decision_reason_code="X",
            notes=None,
        )
    with pytest.raises(ValueError, match="overlap"):
        service.validate_and_save_annotation(
            assignment_id="as1",
            article=art,
            annotator_id="a1",
            client_submission_id="s3",
            relevance_label=2,
            cannot_determine=False,
            cannot_determine_reason=None,
            central_entity_ids=("ent_nvidia",),
            secondary_entity_ids=("ent_nvidia",),
            evidence_excerpt=None,
            content_sufficient=True,
            uncertain=False,
            uncertainty_reason=None,
            decision_reason_code="X",
            notes=None,
        )


def test_evidence_candidates_and_fabricated_offset_rejected(tmp_path: Path) -> None:
    start, end, field, cands = find_evidence_offsets(
        excerpt="NVIDIA Corporation",
        title="NVIDIA unveils roadmap",
        description="Short description.",
        body="NVIDIA Corporation detailed GPU plans.",
        article_hash="h",
    )
    assert field == "body"
    assert start is not None and end is not None
    assert len(cands) == 1

    body = "repeat token repeat token"
    art_hash = "ah"
    cands2 = generate_evidence_candidates(
        excerpt="repeat token",
        title="",
        description="",
        body=body,
        article_hash=art_hash,
    )
    assert len(cands2) == 2
    with pytest.raises(ValueError, match="fabricated|unknown"):
        resolve_evidence_candidate(
            excerpt="repeat token",
            title="",
            description="",
            body=body,
            article_hash=art_hash,
            chosen_candidate_id="not-a-real-candidate",
        )
    text, s, e, f, cid = resolve_evidence_candidate(
        excerpt="repeat token",
        title="",
        description="",
        body=body,
        article_hash=art_hash,
        chosen_candidate_id=cands2[1].candidate_id,
    )
    assert text == "repeat token"
    assert cid == cands2[1].candidate_id

    service = _service(tmp_path)
    art = _article()
    _bind_assignment(service, art, assignment_id="as2")
    with pytest.raises(ValueError, match="not found|typed"):
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
    with pytest.raises(ValueError, match="field@offset"):
        service.validate_and_save_annotation(
            assignment_id="as2",
            article=art,
            annotator_id="a1",
            client_submission_id="s2",
            relevance_label=2,
            cannot_determine=False,
            cannot_determine_reason=None,
            central_entity_ids=(),
            secondary_entity_ids=(),
            evidence_excerpt="NVIDIA Corporation",
            content_sufficient=True,
            uncertain=False,
            uncertainty_reason=None,
            decision_reason_code="X",
            notes=None,
            chosen_evidence_occurrence="body@0",
        )


def test_submission_token_idempotent(tmp_path: Path) -> None:
    session: dict = {}
    fp = form_fingerprint(["as1", "a1", 2])
    t1 = get_or_create_submission_token(session, scope="annotation|as1|a1", form_fingerprint=fp)
    t2 = get_or_create_submission_token(session, scope="annotation|as1|a1", form_fingerprint=fp)
    assert t1 == t2
    rotate_submission_token(session, scope="annotation|as1|a1")
    t3 = get_or_create_submission_token(session, scope="annotation|as1|a1", form_fingerprint=fp)
    assert t3 != t1


def test_blind_view_excludes_prohibited(tmp_path: Path) -> None:
    service = _service(tmp_path)
    art = _article()
    assignment = {
        "assignment_id": "as3",
        "article_id": art.article_id,
        "duplicate_cluster_id": "c1",
        "annotator_id": "a1",
        "guideline_version": "g1",
        "source_run_id": "run1",
        "source_corpus_sha256": "c" * 64,
    }
    view = service.build_blind_article_view(
        assignment=assignment,
        article=art,
        queue_index=1,
        queue_total=1,
        entity_options=service.entity_options(),
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
        source_run_id="run1",
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

    art = _article(article_id="artX")
    _bind_assignment(service, art, assignment_id="asA", annotator_id="a1", batch_id="b")
    _bind_assignment(service, art, assignment_id="asB", annotator_id="a2", batch_id="b")
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
    before = service.store.export_latest_annotation_events(source_run_id="run1", batch_id="b")
    before_ids = {r["event_id"] for r in before}
    raw_ids = [r["event_id"] for r in before]
    service.store.upsert_adjudication_assignment(
        assignment_id="adj1",
        article_id="artX",
        duplicate_cluster_id="c1",
        adjudicator_id="adj",
        guideline_version="g1",
        raw_event_ids=raw_ids,
        batch_id="b",
        source_run_id="run1",
        validate_raw=True,
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
        raw_event_ids=raw_ids,
    )
    after = service.store.export_latest_annotation_events(source_run_id="run1", batch_id="b")
    assert {r["event_id"] for r in after} == before_ids
    assert {r["relevance_label"] for r in after} == {1, 3}


def test_export_preserves_assignment_metadata(tmp_path: Path) -> None:
    service = _service(tmp_path)
    art = _article()
    _bind_assignment(
        service,
        art,
        assignment_id="asE",
        sample_roles=("calibration",),
        guideline_version="g-calib",
        batch_id="b1",
    )
    service.validate_and_save_annotation(
        assignment_id="asE",
        article=art,
        annotator_id="a1",
        client_submission_id="e1",
        relevance_label=2,
        cannot_determine=False,
        cannot_determine_reason=None,
        central_entity_ids=("ent_nvidia",),
        secondary_entity_ids=(),
        evidence_excerpt=None,
        content_sufficient=True,
        uncertain=False,
        uncertainty_reason=None,
        decision_reason_code="X",
        notes=None,
    )
    dest = tmp_path / "exports" / "raw.jsonl"
    result = service.export_raw_annotations_jsonl(
        destination=dest,
        source_run_id="run1",
        batch_id="b1",
    )
    assert dest.is_file()
    assert result["record_count"] == 1
    obj = json.loads(dest.read_text(encoding="utf-8").strip())
    assert "body" not in obj
    assert obj["guideline_version"] == "g-calib"
    assert obj["sample_roles"] == ["calibration"]
    assert obj["batch_id"] == "b1"
    assert obj["central_entity_ids"] == ["ent_nvidia"]
    with pytest.raises(ValueError, match="git-tracked"):
        service.export_raw_annotations_jsonl(
            destination=Path("tests/fixtures/research/relevance_pilot/leak.jsonl"),
            source_run_id="run1",
            batch_id="b1",
        )


def test_mixed_guideline_export_blocked(tmp_path: Path) -> None:
    service = _service(tmp_path)
    art = _article()
    _bind_assignment(service, art, assignment_id="as1", guideline_version="g1", annotator_id="a1")
    art2 = _article(article_id="art_test_2", title="Other title")
    _bind_assignment(
        service,
        art2,
        assignment_id="as2",
        guideline_version="g2",
        annotator_id="a2",
        batch_id="b1",
    )
    for aid, a, ann in (("as1", art, "a1"), ("as2", art2, "a2")):
        service.validate_and_save_annotation(
            assignment_id=aid,
            article=a,
            annotator_id=ann,
            client_submission_id="x",
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
    with pytest.raises(ValueError, match="mixed guideline"):
        service.export_raw_annotations_jsonl(
            destination=tmp_path / "exports" / "mixed.jsonl",
            source_run_id="run1",
            batch_id="b1",
        )


def test_audit_readiness_separate_export_types(tmp_path: Path) -> None:
    service = _service(tmp_path)
    snap = service.audit_snapshot()
    assert "raw_annotation_export" in snap["readiness"]
    assert "duplicate_review_export" in snap["readiness"]
    assert "adjudication_export" in snap["readiness"]
    assert "formal_pilot_analysis" in snap["readiness"]
    assert snap["export_ready"] is False


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
            "source_run_id": "run",
            "source_corpus_sha256": "c" * 64,
        },
        article=art,
        queue_index=1,
        queue_total=1,
        entity_options=(),
    )
    assert "<script>" in view.body

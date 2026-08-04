"""SQLite annotation store tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path("apps/relevance_annotation_ui").resolve()))

from annotation_store import SCHEMA_VERSION, AnnotationStore, make_idempotency_key  # noqa: E402


def test_schema_and_migration(tmp_path: Path) -> None:
    db = tmp_path / "a.sqlite3"
    store = AnnotationStore(db)
    assert store.schema_version() == SCHEMA_VERSION
    store2 = AnnotationStore(db)
    assert store2.schema_version() == SCHEMA_VERSION


def test_append_only_idempotent_and_revision(tmp_path: Path) -> None:
    store = AnnotationStore(tmp_path / "b.sqlite3")
    store.upsert_annotation_assignment(
        assignment_id="as1",
        article_id="art1",
        duplicate_cluster_id="c1",
        annotator_id="a1",
        batch_id="b1",
        guideline_version="g1",
        assignment_order=1,
        source_run_id="run1",
        article_title_sha256="t",
        article_body_sha256="b",
        sample_roles=("calibration",),
    )
    e1 = store.append_annotation_event(
        assignment_id="as1",
        article_id="art1",
        annotator_id="a1",
        client_submission_id="sub1",
        event_type="submit",
        relevance_label=2,
        cannot_determine=False,
        cannot_determine_reason=None,
        central_entity_ids=("ent_nvidia",),
        secondary_entity_ids=(),
        evidence_text=None,
        evidence_start=None,
        evidence_end=None,
        content_sufficient=True,
        uncertain=False,
        uncertainty_reason=None,
        decision_reason_code="X",
        notes=None,
    )
    e1b = store.append_annotation_event(
        assignment_id="as1",
        article_id="art1",
        annotator_id="a1",
        client_submission_id="sub1",
        event_type="submit",
        relevance_label=2,
        cannot_determine=False,
        cannot_determine_reason=None,
        central_entity_ids=("ent_nvidia",),
        secondary_entity_ids=(),
        evidence_text=None,
        evidence_start=None,
        evidence_end=None,
        content_sufficient=True,
        uncertain=False,
        uncertainty_reason=None,
        decision_reason_code="X",
        notes=None,
    )
    assert e1["event_id"] == e1b["event_id"]
    e2 = store.append_annotation_event(
        assignment_id="as1",
        article_id="art1",
        annotator_id="a1",
        client_submission_id="sub2",
        event_type="submit",
        relevance_label=3,
        cannot_determine=False,
        cannot_determine_reason=None,
        central_entity_ids=("ent_nvidia",),
        secondary_entity_ids=(),
        evidence_text=None,
        evidence_start=None,
        evidence_end=None,
        content_sufficient=True,
        uncertain=False,
        uncertainty_reason=None,
        decision_reason_code="Y",
        notes=None,
    )
    assert e2["revision"] == 2
    assert e2["previous_revision"] == 1
    state = store.current_annotation_state("as1")
    assert state is not None
    assert state.relevance_label == 3
    assert store.annotation_revision_count("as1") == 2


def test_idempotency_key_differs_by_actor_and_assignment() -> None:
    a = make_idempotency_key(
        assignment_id="as1", actor_id="a1", client_submission_id="s", event_type="submit"
    )
    b = make_idempotency_key(
        assignment_id="as1", actor_id="a2", client_submission_id="s", event_type="submit"
    )
    c = make_idempotency_key(
        assignment_id="as2", actor_id="a1", client_submission_id="s", event_type="submit"
    )
    assert a != b != c


def test_adjudication_raw_event_validation(tmp_path: Path) -> None:
    store = AnnotationStore(tmp_path / "adj.sqlite3")
    for aid, annotator in (("asA", "a1"), ("asB", "a2")):
        store.upsert_annotation_assignment(
            assignment_id=aid,
            article_id="artX",
            duplicate_cluster_id="cX",
            annotator_id=annotator,
            batch_id="b1",
            guideline_version="g1",
            assignment_order=1,
            source_run_id="run1",
            sample_roles=("formal",),
        )
    e1 = store.append_annotation_event(
        assignment_id="asA",
        article_id="artX",
        annotator_id="a1",
        client_submission_id="1",
        event_type="submit",
        relevance_label=1,
        cannot_determine=False,
        cannot_determine_reason=None,
        central_entity_ids=(),
        secondary_entity_ids=(),
        evidence_text=None,
        evidence_start=None,
        evidence_end=None,
        content_sufficient=True,
        uncertain=False,
        uncertainty_reason=None,
        decision_reason_code="X",
        notes=None,
    )
    e2 = store.append_annotation_event(
        assignment_id="asB",
        article_id="artX",
        annotator_id="a2",
        client_submission_id="1",
        event_type="submit",
        relevance_label=3,
        cannot_determine=False,
        cannot_determine_reason=None,
        central_entity_ids=(),
        secondary_entity_ids=(),
        evidence_text=None,
        evidence_start=None,
        evidence_end=None,
        content_sufficient=True,
        uncertain=False,
        uncertainty_reason=None,
        decision_reason_code="X",
        notes=None,
    )
    store.upsert_adjudication_assignment(
        assignment_id="adj1",
        article_id="artX",
        duplicate_cluster_id="cX",
        adjudicator_id="adj",
        guideline_version="g1",
        raw_event_ids=[e1["event_id"], e2["event_id"]],
        batch_id="b1",
        source_run_id="run1",
        validate_raw=True,
    )
    with pytest.raises(ValueError, match="missing raw"):
        store.upsert_adjudication_assignment(
            assignment_id="adj2",
            article_id="artX",
            duplicate_cluster_id="cX",
            adjudicator_id="adj2",
            guideline_version="g1",
            raw_event_ids=["missing", e2["event_id"]],
            batch_id="b1",
            validate_raw=True,
        )
    # Two revisions from the same annotator cannot form inter-rater adjudication.
    e1_rev2 = store.append_annotation_event(
        assignment_id="asA",
        article_id="artX",
        annotator_id="a1",
        client_submission_id="rev2",
        event_type="submit",
        relevance_label=2,
        cannot_determine=False,
        cannot_determine_reason=None,
        central_entity_ids=(),
        secondary_entity_ids=(),
        evidence_text=None,
        evidence_start=None,
        evidence_end=None,
        content_sufficient=True,
        uncertain=False,
        uncertainty_reason=None,
        decision_reason_code="X",
        notes=None,
    )
    with pytest.raises(ValueError, match="different annotator"):
        store.validate_adjudication_raw_events(
            raw_event_ids=[e1["event_id"], e1_rev2["event_id"]],
            article_id="artX",
            duplicate_cluster_id="cX",
            batch_id="b1",
            guideline_version="g1",
            source_run_id="run1",
            require_latest=False,
        )

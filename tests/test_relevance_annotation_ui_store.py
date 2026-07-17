"""SQLite annotation store tests."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path("apps/relevance_annotation_ui").resolve()))

from annotation_store import AnnotationStore, make_idempotency_key  # noqa: E402


def test_schema_and_migration(tmp_path: Path) -> None:
    db = tmp_path / "a.sqlite3"
    store = AnnotationStore(db)
    assert store.schema_version() == 1
    store2 = AnnotationStore(db)
    assert store2.schema_version() == 1


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
        central_entity_ids=("nvidia",),
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
        central_entity_ids=("nvidia",),
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
        central_entity_ids=("nvidia",),
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


def test_foreign_keys_and_busy_timeout(tmp_path: Path) -> None:
    store = AnnotationStore(tmp_path / "c.sqlite3")
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as conn:
            conn.execute("""
                INSERT INTO annotation_events(
                  event_id, idempotency_key, assignment_id, article_id, annotator_id,
                  event_type, previous_revision, revision, relevance_label, cannot_determine,
                  cannot_determine_reason, central_entity_ids, secondary_entity_ids,
                  evidence_text, evidence_start, evidence_end, content_sufficient,
                  uncertain, uncertainty_reason, decision_reason_code, notes, created_at
                ) VALUES ('e','k','missing','a','a1','submit',NULL,1,0,0,NULL,'[]','[]',
                          NULL,NULL,NULL,1,0,NULL,'X',NULL,'2026-07-17T00:00:00Z')
                """)
    conn = store.connect()
    row = conn.execute("PRAGMA busy_timeout").fetchone()
    assert int(row[0]) >= 1000
    conn.close()


def test_idempotency_key_stable() -> None:
    a = make_idempotency_key(
        assignment_id="x", actor_id="a", client_submission_id="c", event_type="submit"
    )
    b = make_idempotency_key(
        assignment_id="x", actor_id="a", client_submission_id="c", event_type="submit"
    )
    assert a == b

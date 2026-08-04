"""Synthetic end-to-end UI store → export workflow (no network, no models)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from news_data.article.models import ArticleTextRecordV1
from news_data.article.normalize import (
    NORMALIZER_VERSION,
    normalize_text_for_hash,
    sha256_hex,
)
from news_data.entity.reference import SEMICONDUCTOR_FIXTURE_VERSION

sys.path.insert(0, str(Path("apps/relevance_annotation_ui").resolve()))

from annotation_store import SCHEMA_VERSION
from app_config import UIConfig
from pilot_context import ASSIGNMENT_SCHEMA_VERSION
from pilot_service import PilotService, hash_article_records_artifact

FIXED = datetime(2026, 7, 17, 15, 0, 0, tzinfo=timezone.utc)
RUN_ID = "integration_run"


def _article(article_id: str, body: str) -> ArticleTextRecordV1:
    title = f"Title for {article_id}"
    return ArticleTextRecordV1(
        article_id=article_id,
        provider="synthetic",
        provider_record_id=article_id,
        url=f"https://example-tech.test/a/{article_id}",
        normalized_url=f"https://example-tech.test/a/{article_id}",
        canonical_url=None,
        domain="example-tech.test",
        language="en",
        source_country="US",
        title=title,
        description="Description.",
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


def test_synthetic_annotation_export_workflow(tmp_path: Path) -> None:
    cfg = UIConfig.from_mapping(
        {
            "ui": {"database_path": str(tmp_path / "ui.sqlite3")},
            "pilot": {"local_export_root": str(tmp_path / "exports")},
            "content_controls": {"allow_full_body_in_exports": False},
        },
        config_path=tmp_path / "ui.yaml",
    )
    service = PilotService(cfg, repo_root=Path.cwd())
    articles = {
        "art_a": _article("art_a", "NVIDIA Corporation reported fab progress."),
        "art_b": _article("art_b", "AMD discussed CPU roadmap without chips."),
    }
    corpus_hash = hash_article_records_artifact(list(articles.values()))
    # Annotator A + B on art_a (double), A only on art_b
    for order, (aid, annotator) in enumerate(
        [("as_a1", "annotator_01"), ("as_a2", "annotator_02"), ("as_b1", "annotator_01")],
        start=1,
    ):
        article_id = "art_a" if aid.startswith("as_a") else "art_b"
        art = articles[article_id]
        service.store.upsert_annotation_assignment(
            assignment_id=aid,
            article_id=article_id,
            duplicate_cluster_id=f"cluster_{article_id}",
            annotator_id=annotator,
            batch_id="calibration_smoke",
            guideline_version="pilot-guidelines-v2",
            assignment_order=order,
            source_run_id=RUN_ID,
            source_task_name="test",
            source_artifact_root=str(tmp_path),
            source_assignment_manifest_sha256="m" * 64,
            source_corpus_sha256=corpus_hash,
            source_article_records_sha256="f" * 64,
            article_title_sha256=art.title_sha256,
            article_body_sha256=art.body_sha256,
            article_normalization_version=art.normalization_version,
            sample_roles=("calibration",),
            entity_reference_version=SEMICONDUCTOR_FIXTURE_VERSION,
            assignment_schema_version=ASSIGNMENT_SCHEMA_VERSION,
        )

    labels = {
        ("as_a1", "annotator_01"): 3,
        ("as_a2", "annotator_02"): 2,
        ("as_b1", "annotator_01"): 1,
    }
    for (assignment_id, annotator_id), label in labels.items():
        article = articles["art_a" if assignment_id.startswith("as_a") else "art_b"]
        service.validate_and_save_annotation(
            assignment_id=assignment_id,
            article=article,
            annotator_id=annotator_id,
            client_submission_id=f"sub_{assignment_id}",
            relevance_label=label,
            cannot_determine=False,
            cannot_determine_reason=None,
            central_entity_ids=(),
            secondary_entity_ids=(),
            evidence_excerpt=None,
            content_sufficient=True,
            uncertain=False,
            uncertainty_reason=None,
            decision_reason_code="SMOKE",
            notes=None,
        )

    # Duplicate review
    service.store.upsert_duplicate_assignment(
        assignment_id="dup1",
        pair_id="pair_ab",
        article_id_a="art_a",
        article_id_b="art_b",
        reviewer_id="reviewer_01",
        batch_id="calibration_smoke",
        assignment_order=1,
        source_run_id=RUN_ID,
    )
    service.save_duplicate_review(
        assignment_id="dup1",
        pair_id="pair_ab",
        reviewer_id="reviewer_01",
        client_submission_id="dup_sub1",
        pair_label="UNRELATED",
        uncertain=False,
        reason="distinct events and entities",
    )

    # Adjudication for disagreement on art_a using explicit raw event IDs
    raw_events = service.store.export_latest_annotation_events(
        source_run_id=RUN_ID, batch_id="calibration_smoke"
    )
    art_a_events = [e for e in raw_events if e["article_id"] == "art_a"]
    raw_ids = [art_a_events[0]["event_id"], art_a_events[1]["event_id"]]
    service.store.upsert_adjudication_assignment(
        assignment_id="adj_a",
        article_id="art_a",
        duplicate_cluster_id="cluster_art_a",
        adjudicator_id="adjudicator_01",
        guideline_version="pilot-guidelines-v2",
        raw_event_ids=raw_ids,
        batch_id="calibration_smoke",
        source_run_id=RUN_ID,
        validate_raw=True,
    )
    service.save_adjudication(
        assignment_id="adj_a",
        article_id="art_a",
        adjudicator_id="adjudicator_01",
        client_submission_id="adj_sub1",
        relevance_label=3,
        cannot_determine=False,
        cannot_determine_reason=None,
        central_entity_ids=(),
        secondary_entity_ids=(),
        disagreement_cause="ORDINAL_BOUNDARY_2_3",
        adjudication_reason="Primary topic with secondary framing disagreement",
        raw_labels=[3, 2],
        raw_event_ids=raw_ids,
    )

    dest = tmp_path / "exports" / "raw_annotations.jsonl"
    result = service.export_raw_annotations_jsonl(
        destination=dest,
        source_run_id=RUN_ID,
        batch_id="calibration_smoke",
    )
    assert result["record_count"] == 3
    lines = [json.loads(line) for line in dest.read_text(encoding="utf-8").splitlines() if line]
    assert all("body" not in row for row in lines)
    assert all(row["sample_roles"] == ["calibration"] for row in lines)
    assert [row["article_id"] for row in lines] == sorted(row["article_id"] for row in lines)

    dup_dest = tmp_path / "exports" / "dups.jsonl"
    dup_result = service.export_duplicate_reviews_jsonl(
        destination=dup_dest, source_run_id=RUN_ID, batch_id="calibration_smoke"
    )
    assert dup_result["record_count"] == 1

    adj_dest = tmp_path / "exports" / "adj.jsonl"
    adj_result = service.export_adjudications_jsonl(
        destination=adj_dest, source_run_id=RUN_ID, batch_id="calibration_smoke"
    )
    assert adj_result["record_count"] == 1

    audit = service.audit_snapshot(source_run_id=RUN_ID)
    assert audit["counts"]["annotation_completed"] == 3
    assert audit["counts"]["annotation_incomplete"] == 0
    assert audit["counts"]["duplicate_completed"] == 1
    assert audit["counts"]["adjudications_remaining"] == 0
    assert audit["schema_version"] == SCHEMA_VERSION
    assert audit["readiness"]["raw_annotation_export"] is True
    assert audit["readiness"]["duplicate_review_export"] is True
    assert audit["readiness"]["adjudication_export"] is True

    # Reopen persistence
    service2 = PilotService(cfg, repo_root=Path.cwd())
    assert service2.store.current_annotation_state("as_a1") is not None
    assert service2.store.current_annotation_state("as_a1").relevance_label == 3

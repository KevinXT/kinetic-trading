"""Service layer bridging research engine artifacts, blind views, and SQLite."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import yaml
from annotation_store import AnnotationStore
from app_config import UIConfig
from news_data.article.corpus import LocalArticleCorpusProvider
from news_data.article.models import ArticleTextRecordV1
from news_data.entity.reference import load_entity_references
from research_data.real_corpus_pilot_task import (
    run_semiconductor_relevance_real_corpus_pilot_task,
)
from research_data.relevance.content_controls import is_git_tracked_research_path
from research_data.relevance.models import RELEVANCE_LABELS
from research_data.relevance.pilot_models import (
    CANNOT_DETERMINE_REASONS,
    DISAGREEMENT_CAUSE_CATEGORIES,
    PAIR_LABELS,
    PilotRelevanceAnnotationV1,
)
from view_models import (
    BlindArticleView,
    BlindDuplicatePairView,
    assert_no_prohibited_fields,
    find_evidence_offsets,
)

try:
    from pipeline_core.engine.context import RunContext
except ImportError:  # pragma: no cover
    RunContext = None  # type: ignore[misc, assignment]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _git_commit(repo_root: Path) -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _file_sha256(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


@dataclass(frozen=True)
class ReproducibilityRecord:
    task_name: str
    config_path: str
    run_id: str
    git_commit: Optional[str]
    input_hashes: dict[str, Optional[str]]
    output_path: str
    timestamp: str
    resulting_state: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_name": self.task_name,
            "config_path": self.config_path,
            "run_id": self.run_id,
            "git_commit": self.git_commit,
            "input_hashes": self.input_hashes,
            "output_path": self.output_path,
            "timestamp": self.timestamp,
            "resulting_state": self.resulting_state,
            "cli_equivalent": (
                f"python3 -m trading_platform {self.config_path} --run-id {self.run_id}"
            ),
        }


class PilotService:
    def __init__(self, config: UIConfig, *, repo_root: Path | None = None) -> None:
        self.config = config
        self.repo_root = repo_root or Path.cwd()
        self.store = AnnotationStore(config.database_path)

    def load_pilot_params(self) -> dict[str, Any]:
        path = self.config.pilot_config_path
        if not path.is_file():
            raise FileNotFoundError(f"pilot config not found: {path}")
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return dict(cfg.get("pipeline", {}).get("strategy") or {})

    def run_preflight(
        self,
        *,
        run_id: str,
        articles_path: Optional[str] = None,
        provenance_path: Optional[str] = None,
        clock: Optional[Any] = None,
    ) -> dict[str, Any]:
        if RunContext is None:
            raise RuntimeError("pipeline_core is required to run preflight")
        params = self.load_pilot_params()
        # Non-destructive: strip gates that would advance to formal labeling.
        params = dict(params)
        params.pop("calibration_approval", None)
        params.pop("formal_raw_annotations_path", None)
        params.pop("formal_adjudicated_annotations_path", None)
        params.pop("duplicate_pair_reviews_path", None)
        if articles_path:
            params["articles_path"] = articles_path
        if provenance_path:
            params["provenance_path"] = provenance_path
        run_dir = self.config.artifact_root / "relevance_annotation_ui" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        ctx = RunContext(
            cfg={"name": "relevance_annotation_ui_preflight"},
            run_name="relevance_annotation_ui_preflight",
            run_id=run_id,
            run_dir=run_dir,
        )
        fixed_clock = clock or (lambda: datetime(2026, 7, 17, 15, 0, 0, tzinfo=timezone.utc))
        run_semiconductor_relevance_real_corpus_pilot_task(ctx, params, clock=fixed_clock)
        state_path = ctx.artifacts_dir / "pilot_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
        repro = ReproducibilityRecord(
            task_name="run_semiconductor_relevance_real_corpus_pilot",
            config_path=str(self.config.pilot_config_path),
            run_id=run_id,
            git_commit=_git_commit(self.repo_root),
            input_hashes={
                "articles": _file_sha256(Path(str(params.get("articles_path", "")))),
                "provenance": _file_sha256(Path(str(params.get("provenance_path", "")))),
                "config": _file_sha256(self.config.pilot_config_path),
            },
            output_path=str(ctx.artifacts_dir),
            timestamp=_utc_now().isoformat().replace("+00:00", "Z"),
            resulting_state=str(state.get("pilot_state", "UNKNOWN")),
        )
        (ctx.artifacts_dir / "ui_reproducibility.json").write_text(
            json.dumps(repro.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {
            "artifacts_dir": str(ctx.artifacts_dir),
            "state": state,
            "reproducibility": repro.to_dict(),
            "preflight_summary": _read_json(
                ctx.artifacts_dir / "real_corpus_preflight_summary.json"
            ),
            "exclusion_summary": _read_json(ctx.artifacts_dir / "corpus_exclusion_summary.json"),
            "sample_plan": _read_json(ctx.artifacts_dir / "pilot_sample_size_plan.json"),
        }

    def import_calibration_assignments_from_batch(
        self,
        *,
        batch_csv_path: Path,
        annotator_ids: Sequence[str],
        batch_id: str,
        guideline_version: str,
    ) -> int:
        import csv

        rows = list(csv.DictReader(batch_csv_path.open(encoding="utf-8")))
        count = 0
        order = 0
        for row in rows:
            article_id = row["article_id"]
            cluster_id = row["duplicate_cluster_id"]
            double = str(row.get("double_annotate", "")).lower() in {"yes", "true", "1"}
            targets = list(annotator_ids) if double else [annotator_ids[0]]
            for annotator_id in targets:
                order += 1
                assignment_id = hashlib.sha256(
                    f"{batch_id}|{article_id}|{annotator_id}".encode("utf-8")
                ).hexdigest()[:24]
                self.store.upsert_annotation_assignment(
                    assignment_id=assignment_id,
                    article_id=article_id,
                    duplicate_cluster_id=cluster_id,
                    annotator_id=annotator_id,
                    batch_id=batch_id,
                    guideline_version=guideline_version,
                    assignment_order=order,
                )
                count += 1
        return count

    def load_articles_index(self, articles_path: Path) -> dict[str, ArticleTextRecordV1]:
        provider = LocalArticleCorpusProvider(
            path=articles_path,
            default_content_source="synthetic_fixture",
            ingested_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
        )
        imported = provider.import_articles()
        return {a.article_id: a for a in imported.records}

    def build_blind_article_view(
        self,
        *,
        assignment: Mapping[str, Any],
        article: ArticleTextRecordV1,
        queue_index: int,
        queue_total: int,
        entity_names: Sequence[str],
    ) -> BlindArticleView:
        body = article.body or ""
        truncated = False
        max_chars = self.config.maximum_body_display_characters
        if (
            self.config.show_full_body
            and self.config.allow_full_body_in_local_ui
            and max_chars is not None
            and len(body) > max_chars
        ):
            body = body[:max_chars]
            truncated = True
        if not self.config.allow_full_body_in_local_ui:
            body = ""
            truncated = True
        state = self.store.current_annotation_state(str(assignment["assignment_id"]))
        view = BlindArticleView(
            assignment_id=str(assignment["assignment_id"]),
            article_id=article.article_id,
            duplicate_cluster_id=str(assignment["duplicate_cluster_id"]),
            title=article.title,
            domain=article.domain,
            published_at=(
                article.published_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                if article.published_at
                else None
            ),
            published_at_precision=(article.published_at_source or "unknown"),
            language=article.language,
            description=article.description or "",
            body=body,
            content_status=article.content_status,
            body_truncated=truncated,
            entity_reference_names=tuple(entity_names),
            guideline_version=str(assignment["guideline_version"]),
            annotator_id=str(assignment["annotator_id"]),
            queue_index=queue_index,
            queue_total=queue_total,
            saved_revision=(state.revision if state else 0),
            last_saved_at=(state.last_saved_at if state else None),
        )
        assert_no_prohibited_fields(view.to_dict())
        return view

    def validate_and_save_annotation(
        self,
        *,
        assignment_id: str,
        article: ArticleTextRecordV1,
        annotator_id: str,
        client_submission_id: str,
        relevance_label: Optional[int],
        cannot_determine: bool,
        cannot_determine_reason: Optional[str],
        central_entity_ids: Sequence[str],
        secondary_entity_ids: Sequence[str],
        evidence_excerpt: Optional[str],
        content_sufficient: bool,
        uncertain: bool,
        uncertainty_reason: Optional[str],
        decision_reason_code: str,
        notes: Optional[str],
        chosen_evidence_occurrence: Optional[str] = None,
        allow_no_exact_span: bool = False,
    ) -> dict[str, Any]:
        if cannot_determine:
            if relevance_label is not None:
                raise ValueError("cannot_determine requires relevance_label to be null")
            if not cannot_determine_reason:
                raise ValueError("cannot_determine_reason is required")
            if cannot_determine_reason not in CANNOT_DETERMINE_REASONS:
                raise ValueError(f"unsupported cannot_determine_reason {cannot_determine_reason}")
        else:
            if relevance_label not in RELEVANCE_LABELS:
                raise ValueError("relevance_label must be in 0..3")
            cannot_determine_reason = None
        if uncertain and not (uncertainty_reason and uncertainty_reason.strip()):
            raise ValueError("uncertainty_reason required when uncertain")
        evidence_text = None
        evidence_start = None
        evidence_end = None
        if evidence_excerpt and evidence_excerpt.strip():
            if len(evidence_excerpt) > self.config.maximum_evidence_characters:
                raise ValueError("evidence excerpt exceeds configured maximum")
            start, end, field, candidates = find_evidence_offsets(
                excerpt=evidence_excerpt,
                title=article.title,
                description=article.description or "",
                body=article.body or "",
            )
            if start is None and not candidates:
                if not allow_no_exact_span:
                    raise ValueError("evidence excerpt not found in article text")
            elif start is None and candidates:
                if not chosen_evidence_occurrence:
                    raise ValueError(
                        "evidence excerpt occurs multiple times; choose occurrence: "
                        + ", ".join(candidates)
                    )
                # chosen format field@offset
                if "@" not in chosen_evidence_occurrence:
                    raise ValueError("invalid occurrence selector")
                field_name, offset_s = chosen_evidence_occurrence.split("@", 1)
                start = int(offset_s)
                end = start + len(evidence_excerpt)
                evidence_text = evidence_excerpt
                evidence_start = start
                evidence_end = end
            else:
                evidence_text = evidence_excerpt
                evidence_start = start
                evidence_end = end
        event = self.store.append_annotation_event(
            assignment_id=assignment_id,
            article_id=article.article_id,
            annotator_id=annotator_id,
            client_submission_id=client_submission_id,
            event_type="submit",
            relevance_label=relevance_label,
            cannot_determine=cannot_determine,
            cannot_determine_reason=cannot_determine_reason,
            central_entity_ids=central_entity_ids,
            secondary_entity_ids=secondary_entity_ids,
            evidence_text=evidence_text,
            evidence_start=evidence_start,
            evidence_end=evidence_end,
            content_sufficient=content_sufficient,
            uncertain=uncertain,
            uncertainty_reason=(uncertainty_reason if uncertain else None),
            decision_reason_code=decision_reason_code or "UNSPECIFIED",
            notes=notes,
        )
        return event

    def build_blind_duplicate_view(
        self,
        *,
        assignment: Mapping[str, Any],
        article_a: ArticleTextRecordV1,
        article_b: ArticleTextRecordV1,
        queue_index: int,
        queue_total: int,
    ) -> BlindDuplicatePairView:
        def side(a: ArticleTextRecordV1) -> dict[str, str]:
            body = a.body or ""
            if not self.config.allow_full_body_in_local_ui:
                body = ""
            return {
                "article_id": a.article_id,
                "title": a.title,
                "domain": a.domain,
                "published_at": (
                    a.published_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                    if a.published_at
                    else ""
                ),
                "description": a.description or "",
                "body": body,
                "content_status": a.content_status,
            }

        view = BlindDuplicatePairView(
            assignment_id=str(assignment["assignment_id"]),
            pair_id=str(assignment["pair_id"]),
            article_a=side(article_a),
            article_b=side(article_b),
            reviewer_id=str(assignment["reviewer_id"]),
            queue_index=queue_index,
            queue_total=queue_total,
        )
        payload = view.to_dict()
        # Ensure similarity/threshold fields never appear.
        for banned in (
            "similarity",
            "threshold",
            "candidate_at_current_threshold",
            "title_similarity",
            "body_similarity",
        ):
            if banned in payload:
                raise ValueError(f"duplicate blind view leaked {banned}")
        return view

    def save_duplicate_review(
        self,
        *,
        assignment_id: str,
        pair_id: str,
        reviewer_id: str,
        client_submission_id: str,
        pair_label: str,
        uncertain: bool,
        reason: str,
    ) -> dict[str, Any]:
        if pair_label not in PAIR_LABELS:
            raise ValueError(f"unsupported pair_label {pair_label}")
        if pair_label == "CANNOT_DETERMINE" and not reason.strip():
            raise ValueError("reason required for CANNOT_DETERMINE")
        if not reason.strip():
            raise ValueError("reason is required")
        return self.store.append_duplicate_review_event(
            assignment_id=assignment_id,
            pair_id=pair_id,
            reviewer_id=reviewer_id,
            client_submission_id=client_submission_id,
            pair_label=pair_label,
            uncertain=uncertain,
            reason=reason,
        )

    def save_adjudication(
        self,
        *,
        assignment_id: str,
        article_id: str,
        adjudicator_id: str,
        client_submission_id: str,
        relevance_label: Optional[int],
        cannot_determine: bool,
        cannot_determine_reason: Optional[str],
        central_entity_ids: Sequence[str],
        secondary_entity_ids: Sequence[str],
        disagreement_cause: str,
        adjudication_reason: str,
        raw_labels: Sequence[int],
    ) -> dict[str, Any]:
        if disagreement_cause not in DISAGREEMENT_CAUSE_CATEGORIES:
            raise ValueError(f"unsupported disagreement_cause {disagreement_cause}")
        if len(adjudication_reason.strip()) < 8:
            raise ValueError("adjudication_reason must be substantive")
        if len(raw_labels) >= 2 and abs(raw_labels[0] - raw_labels[1]) >= 2:
            if len(adjudication_reason.strip()) < 8:
                raise ValueError("large disagreement requires substantive reason")
        if cannot_determine:
            relevance_label = None
            if not cannot_determine_reason:
                raise ValueError("cannot_determine_reason required")
        elif relevance_label not in RELEVANCE_LABELS:
            raise ValueError("relevance_label must be in 0..3")
        return self.store.append_adjudication_event(
            assignment_id=assignment_id,
            article_id=article_id,
            adjudicator_id=adjudicator_id,
            client_submission_id=client_submission_id,
            relevance_label=relevance_label,
            cannot_determine=cannot_determine,
            cannot_determine_reason=cannot_determine_reason,
            central_entity_ids=central_entity_ids,
            secondary_entity_ids=secondary_entity_ids,
            disagreement_cause=disagreement_cause,
            adjudication_reason=adjudication_reason,
        )

    def export_raw_annotations_jsonl(
        self,
        *,
        destination: Path,
        sample_roles: Sequence[str] = ("calibration",),
        guideline_version: str,
    ) -> dict[str, Any]:
        if is_git_tracked_research_path(destination) and not (
            self.config.allow_real_content_in_git_tracked_paths
        ):
            raise ValueError(f"refusing export into git-tracked path: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        rows = self.store.export_latest_annotation_events()
        out_models: list[dict[str, Any]] = []
        for row in rows:
            cannot = bool(row["cannot_determine"])
            model = PilotRelevanceAnnotationV1(
                article_id=row["article_id"],
                duplicate_cluster_id=self._cluster_for_article(
                    row["article_id"], row["assignment_id"]
                ),
                sample_roles=tuple(sample_roles),
                annotator_id=row["annotator_id"],
                guideline_version=guideline_version,
                relevance_label=(None if cannot else row["relevance_label"]),
                cannot_determine=cannot,
                cannot_determine_reason=row["cannot_determine_reason"],
                central_entity_ids=tuple(json.loads(row["central_entity_ids"])),
                secondary_entity_ids=tuple(json.loads(row["secondary_entity_ids"])),
                evidence_text=row["evidence_text"],
                evidence_start=row["evidence_start"],
                evidence_end=row["evidence_end"],
                content_sufficient=bool(row["content_sufficient"]),
                uncertain=bool(row["uncertain"]),
                uncertainty_reason=row["uncertainty_reason"],
                decision_reason_code=row["decision_reason_code"],
                annotator_notes=row["notes"],
                annotation_started_at=None,
                annotation_completed_at=datetime.fromisoformat(
                    str(row["created_at"]).replace("Z", "+00:00")
                ),
            )
            payload = model.to_dict()
            if not self.config.allow_full_body_in_exports:
                # Annotation schema does not require article body.
                pass
            out_models.append(payload)
        out_models.sort(key=lambda r: (r["article_id"], r["annotator_id"]))
        text = "\n".join(json.dumps(r, sort_keys=True) for r in out_models)
        if text:
            text += "\n"
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        tmp = destination.with_suffix(destination.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, destination)
        export_id = content_hash[:24]
        self.store.record_export(
            export_id=export_id,
            export_type="raw_annotations_jsonl",
            destination=str(destination),
            record_count=len(out_models),
            content_hash=content_hash,
        )
        return {
            "destination": str(destination),
            "record_count": len(out_models),
            "content_hash": content_hash,
            "export_id": export_id,
        }

    def _cluster_for_article(self, article_id: str, assignment_id: str) -> str:
        for row in self.store.list_annotation_assignments():
            if row["assignment_id"] == assignment_id:
                return str(row["duplicate_cluster_id"])
        return article_id

    def audit_snapshot(self) -> dict[str, Any]:
        counts = self.store.completion_counts()
        return {
            "schema_version": self.store.schema_version(),
            "database_path": str(self.config.database_path),
            "counts": counts,
            "export_ready": counts["annotation_incomplete"] == 0
            and counts["annotation_assigned"] > 0,
            "mode_note": (
                "Trusted local workstation — mode selection is workflow separation, "
                "not secure multi-tenant authorization."
            ),
        }


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def entity_display_names() -> list[str]:
    entities = load_entity_references(None)
    return sorted({e.display_name for e in entities})

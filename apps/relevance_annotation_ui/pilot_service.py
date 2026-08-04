"""Service layer bridging research engine artifacts, blind views, and SQLite."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

import yaml
from annotation_store import AnnotationStore, utc_now
from app_config import UIConfig
from news_data.article.corpus import LocalArticleCorpusProvider
from news_data.article.models import ArticleTextRecordV1
from news_data.entity.reference import SEMICONDUCTOR_FIXTURE_VERSION, load_entity_references
from pilot_context import (
    ASSIGNMENT_SCHEMA_VERSION,
    PilotRunContext,
    SourceArtifactMismatch,
)
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
    EntityOption,
    article_content_hash,
    assert_no_prohibited_fields,
    resolve_evidence_candidate,
    validate_entity_id_sets,
)

try:
    from pipeline_core.engine.context import RunContext
except ImportError:  # pragma: no cover
    RunContext = None  # type: ignore[misc, assignment]


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


def hash_article_records_artifact(records: Sequence[ArticleTextRecordV1]) -> str:
    """Corpus/article-records hash over deterministically sorted normalized IDs.

    Order-independent: records are sorted by article_id before hashing identity
    fields (article_id, title_sha256, body_sha256, normalization_version).
    """
    rows = sorted(
        (
            {
                "article_id": r.article_id,
                "title_sha256": r.title_sha256,
                "body_sha256": r.body_sha256,
                "normalization_version": r.normalization_version,
            }
            for r in records
        ),
        key=lambda d: str(d["article_id"]),
    )
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    def __init__(
        self,
        config: UIConfig,
        *,
        repo_root: Path | None = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.config = config
        self.repo_root = repo_root or Path.cwd()
        self.store = AnnotationStore(config.database_path)
        self._clock = clock or utc_now

    def now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("clock must return timezone-aware UTC datetime")
        return value.astimezone(timezone.utc)

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
        clock: Optional[Callable[[], datetime]] = None,
    ) -> dict[str, Any]:
        if RunContext is None:
            raise RuntimeError("pipeline_core is required to run preflight")
        params = self.load_pilot_params()
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
        run_clock = clock or self._clock
        run_semiconductor_relevance_real_corpus_pilot_task(ctx, params, clock=run_clock)
        state_path = ctx.artifacts_dir / "pilot_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
        timestamp = self.now().isoformat().replace("+00:00", "Z")
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
            timestamp=timestamp,
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

    def select_pilot_context_from_artifacts(
        self,
        *,
        artifacts_dir: Path,
        run_id: str,
        guideline_version: str,
        batch_ids: Sequence[str],
        selected_batch_id: Optional[str] = None,
    ) -> PilotRunContext:
        articles_path = artifacts_dir / "article_text_records.jsonl"
        if not articles_path.is_file():
            raise FileNotFoundError(f"missing bound article artifact: {articles_path}")
        articles = self.load_articles_index(articles_path)
        if not articles:
            raise ValueError("bound article artifact is empty")
        records_hash = hash_article_records_artifact(list(articles.values()))
        file_hash = _file_sha256(articles_path) or records_hash
        batch_path = artifacts_dir / "calibration_annotation_batch.csv"
        manifest_hash = _file_sha256(batch_path) or hashlib.sha256(b"no-batch").hexdigest()
        ctx = PilotRunContext(
            source_run_id=run_id,
            source_task_name="run_semiconductor_relevance_real_corpus_pilot",
            source_artifact_root=str(artifacts_dir),
            config_path=str(self.config.pilot_config_path),
            git_commit=_git_commit(self.repo_root),
            source_corpus_sha256=records_hash,
            source_article_records_sha256=file_hash,
            source_assignment_manifest_sha256=manifest_hash,
            guideline_version=guideline_version,
            batch_ids=tuple(batch_ids),
            database_path=str(self.config.database_path),
            articles_artifact_path=str(articles_path),
            entity_reference_version=SEMICONDUCTOR_FIXTURE_VERSION,
            selected_batch_id=selected_batch_id or (batch_ids[0] if batch_ids else None),
        )
        self.store.save_pilot_context(source_run_id=run_id, payload=ctx.to_dict())
        return ctx

    def import_bound_assignments_from_batch(
        self,
        *,
        context: PilotRunContext,
        batch_csv_path: Path,
        annotator_ids: Sequence[str],
        batch_id: str,
        sample_roles: Sequence[str] = ("calibration",),
    ) -> int:
        """Import assignments bound to the selected run's article artifact."""
        articles = self.load_articles_index(Path(context.articles_artifact_path))
        records_hash = hash_article_records_artifact(list(articles.values()))
        if records_hash != context.source_corpus_sha256:
            raise SourceArtifactMismatch(
                detail="corpus hash mismatch between context and article artifact"
            )
        file_hash = _file_sha256(Path(context.articles_artifact_path))
        if file_hash and file_hash != context.source_article_records_sha256:
            raise SourceArtifactMismatch(detail="article_text_records.jsonl hash mismatch")
        manifest_hash = _file_sha256(batch_csv_path)
        if manifest_hash is None:
            raise SourceArtifactMismatch(detail="assignment manifest missing")
        if manifest_hash != context.source_assignment_manifest_sha256:
            raise SourceArtifactMismatch(detail="assignment manifest hash mismatch")
        rows = list(csv.DictReader(batch_csv_path.open(encoding="utf-8")))
        seen_assignment_ids: set[str] = set()
        seen_articles: set[str] = set()
        count = 0
        order = 0
        for row in rows:
            article_id = row["article_id"]
            if article_id in seen_articles:
                # Multi-annotator double annotation repeats article_id; OK.
                pass
            seen_articles.add(article_id)
            article = articles.get(article_id)
            if article is None:
                raise SourceArtifactMismatch(
                    detail=f"assignment article_id missing from bound artifact: {article_id}"
                )
            cluster_id = row["duplicate_cluster_id"]
            double = str(row.get("double_annotate", "")).lower() in {"yes", "true", "1"}
            targets = list(annotator_ids) if double else [annotator_ids[0]]
            for annotator_id in targets:
                order += 1
                assignment_id = hashlib.sha256(
                    f"{batch_id}|{article_id}|{annotator_id}|{context.source_run_id}".encode(
                        "utf-8"
                    )
                ).hexdigest()[:24]
                if assignment_id in seen_assignment_ids:
                    raise ValueError(f"duplicate assignment_id {assignment_id}")
                seen_assignment_ids.add(assignment_id)
                self.store.upsert_annotation_assignment(
                    assignment_id=assignment_id,
                    article_id=article_id,
                    duplicate_cluster_id=cluster_id,
                    annotator_id=annotator_id,
                    batch_id=batch_id,
                    guideline_version=context.guideline_version,
                    assignment_order=order,
                    source_run_id=context.source_run_id,
                    source_task_name=context.source_task_name,
                    source_artifact_root=context.source_artifact_root,
                    source_assignment_manifest_sha256=context.source_assignment_manifest_sha256,
                    source_corpus_sha256=context.source_corpus_sha256,
                    source_article_records_sha256=context.source_article_records_sha256,
                    article_title_sha256=article.title_sha256,
                    article_body_sha256=article.body_sha256,
                    article_normalization_version=article.normalization_version,
                    sample_roles=sample_roles,
                    entity_reference_version=context.entity_reference_version,
                    assignment_schema_version=ASSIGNMENT_SCHEMA_VERSION,
                )
                count += 1
        return count

    # Backward-compatible name used by existing pages/tests.
    def import_calibration_assignments_from_batch(
        self,
        *,
        batch_csv_path: Path,
        annotator_ids: Sequence[str],
        batch_id: str,
        guideline_version: str,
        context: Optional[PilotRunContext] = None,
    ) -> int:
        if context is None:
            raise ValueError(
                "bound PilotRunContext is required; free-path assignment import is disabled"
            )
        if context.guideline_version != guideline_version:
            raise ValueError("guideline_version must match selected pilot context")
        return self.import_bound_assignments_from_batch(
            context=context,
            batch_csv_path=batch_csv_path,
            annotator_ids=annotator_ids,
            batch_id=batch_id,
            sample_roles=("calibration",),
        )

    def load_articles_index(self, articles_path: Path) -> dict[str, ArticleTextRecordV1]:
        provider = LocalArticleCorpusProvider(
            path=articles_path,
            default_content_source="synthetic_fixture",
            ingested_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
        )
        imported = provider.import_articles()
        by_id: dict[str, ArticleTextRecordV1] = {}
        for a in imported.records:
            if a.article_id in by_id:
                raise ValueError(f"duplicate article_id in source artifact: {a.article_id}")
            by_id[a.article_id] = a
        return by_id

    def load_bound_article(
        self, *, assignment: Mapping[str, Any], context: PilotRunContext
    ) -> ArticleTextRecordV1:
        if assignment.get("source_run_id") != context.source_run_id:
            raise SourceArtifactMismatch(detail="assignment source_run_id != selected context")
        articles = self.load_articles_index(Path(context.articles_artifact_path))
        article = articles.get(str(assignment["article_id"]))
        if article is None:
            raise SourceArtifactMismatch(detail="article missing from bound artifact")
        if article.title_sha256 != assignment.get("article_title_sha256"):
            raise SourceArtifactMismatch(detail="article title hash mismatch")
        if article.body_sha256 != assignment.get("article_body_sha256"):
            raise SourceArtifactMismatch(detail="article body hash mismatch")
        if article.normalization_version != assignment.get("article_normalization_version"):
            raise SourceArtifactMismatch(detail="normalization version mismatch")
        records_hash = hash_article_records_artifact(list(articles.values()))
        if records_hash != context.source_corpus_sha256:
            raise SourceArtifactMismatch(detail="corpus hash mismatch at load time")
        return article

    def entity_options(self) -> list[EntityOption]:
        entities = load_entity_references(None)
        return [
            EntityOption(
                entity_id=e.entity_id,
                display_name=e.display_name,
                ticker=e.ticker,
                entity_type=e.entity_type,
            )
            for e in sorted(entities, key=lambda x: (x.display_name, x.entity_id))
        ]

    def build_blind_article_view(
        self,
        *,
        assignment: Mapping[str, Any],
        article: ArticleTextRecordV1,
        queue_index: int,
        queue_total: int,
        entity_names: Sequence[str] | None = None,
        entity_options: Sequence[EntityOption] | None = None,
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
        options = (
            tuple(entity_options) if entity_options is not None else tuple(self.entity_options())
        )
        if entity_names and not entity_options:
            # Legacy display-name list: map to options where possible.
            by_name = {o.display_name: o for o in self.entity_options()}
            options = tuple(by_name[n] for n in entity_names if n in by_name)
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
            entity_options=options,
            guideline_version=str(assignment["guideline_version"]),
            annotator_id=str(assignment["annotator_id"]),
            queue_index=queue_index,
            queue_total=queue_total,
            saved_revision=(state.revision if state else 0),
            last_saved_at=(state.last_saved_at if state else None),
            article_title_sha256=article.title_sha256,
            article_body_sha256=article.body_sha256,
            source_run_id=str(assignment.get("source_run_id") or ""),
            source_corpus_sha256=str(assignment.get("source_corpus_sha256") or ""),
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
        chosen_evidence_candidate_id: Optional[str] = None,
        allow_no_exact_span: bool = False,
        no_exact_span_reason: Optional[str] = None,
        verify_assignment_hashes: bool = True,
    ) -> dict[str, Any]:
        assignment = self.store.get_annotation_assignment(assignment_id)
        if assignment is None:
            raise ValueError(f"unknown assignment_id {assignment_id}")
        if verify_assignment_hashes:
            if article.article_id != assignment["article_id"]:
                raise SourceArtifactMismatch(detail="article_id mismatch at save")
            if article.title_sha256 != assignment.get("article_title_sha256"):
                raise SourceArtifactMismatch(detail="title hash mismatch at save")
            if article.body_sha256 != assignment.get("article_body_sha256"):
                raise SourceArtifactMismatch(detail="body hash mismatch at save")
            if article.normalization_version != assignment.get("article_normalization_version"):
                raise SourceArtifactMismatch(detail="normalization version mismatch at save")
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

        allowed_ids = [e.entity_id for e in self.entity_options()]
        # Accept legacy display names only when they uniquely map; otherwise require IDs.
        by_name = {e.display_name: e.entity_id for e in self.entity_options()}
        resolved_central = [
            by_name.get(x, x) if x not in allowed_ids else x for x in central_entity_ids
        ]
        resolved_secondary = [
            by_name.get(x, x) if x not in allowed_ids else x for x in secondary_entity_ids
        ]
        # After remediation, display names that are not also IDs must resolve.
        for x in list(resolved_central) + list(resolved_secondary):
            if x not in allowed_ids and x != "UNRESOLVED_ENTITY":
                raise ValueError(f"unknown entity_id {x!r}; store canonical IDs, not display names")
        central_ids, secondary_ids = validate_entity_id_sets(
            central_entity_ids=resolved_central,
            secondary_entity_ids=resolved_secondary,
            allowed_entity_ids=allowed_ids,
        )

        evidence_text = None
        evidence_start = None
        evidence_end = None
        if evidence_excerpt and evidence_excerpt.strip():
            if len(evidence_excerpt) > self.config.maximum_evidence_characters:
                raise ValueError("evidence excerpt exceeds configured maximum")
            # Reject legacy fabricated field@offset selectors.
            if chosen_evidence_occurrence and "@" in chosen_evidence_occurrence:
                if not chosen_evidence_candidate_id:
                    raise ValueError(
                        "fabricated field@offset selectors are rejected; "
                        "select a generated evidence candidate_id"
                    )
            article_hash = article_content_hash(
                title=article.title,
                description=article.description or "",
                body=article.body or "",
                title_sha256=article.title_sha256,
                body_sha256=article.body_sha256,
            )
            reason = no_exact_span_reason
            if allow_no_exact_span and not reason:
                reason = "NO_SINGLE_EXACT_SPAN"
            evidence_text, evidence_start, evidence_end, _field, _cid = resolve_evidence_candidate(
                excerpt=evidence_excerpt,
                title=article.title,
                description=article.description or "",
                body=article.body or "",
                article_hash=article_hash,
                chosen_candidate_id=chosen_evidence_candidate_id,
                no_exact_span_reason=reason,
            )
        event = self.store.append_annotation_event(
            assignment_id=assignment_id,
            article_id=article.article_id,
            annotator_id=annotator_id,
            client_submission_id=client_submission_id,
            event_type="submit",
            relevance_label=relevance_label,
            cannot_determine=cannot_determine,
            cannot_determine_reason=cannot_determine_reason,
            central_entity_ids=central_ids,
            secondary_entity_ids=secondary_ids,
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
            source_run_id=str(assignment.get("source_run_id") or ""),
        )
        payload = view.to_dict()
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
        raw_event_ids: Sequence[str] = (),
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
        allowed_ids = [e.entity_id for e in self.entity_options()]
        by_name = {e.display_name: e.entity_id for e in self.entity_options()}
        resolved_central = [
            by_name.get(x, x) if x not in allowed_ids else x for x in central_entity_ids
        ]
        resolved_secondary = [
            by_name.get(x, x) if x not in allowed_ids else x for x in secondary_entity_ids
        ]
        central_ids, secondary_ids = validate_entity_id_sets(
            central_entity_ids=resolved_central,
            secondary_entity_ids=resolved_secondary,
            allowed_entity_ids=allowed_ids,
        )
        return self.store.append_adjudication_event(
            assignment_id=assignment_id,
            article_id=article_id,
            adjudicator_id=adjudicator_id,
            client_submission_id=client_submission_id,
            relevance_label=relevance_label,
            cannot_determine=cannot_determine,
            cannot_determine_reason=cannot_determine_reason,
            central_entity_ids=central_ids,
            secondary_entity_ids=secondary_ids,
            disagreement_cause=disagreement_cause,
            adjudication_reason=adjudication_reason,
            raw_event_ids=raw_event_ids,
        )

    def export_raw_annotations_jsonl(
        self,
        *,
        destination: Path,
        source_run_id: str,
        batch_id: str,
        annotator_id: Optional[str] = None,
        guideline_version: Optional[str] = None,
        allow_mixed_guideline: bool = False,
        # Deprecated: caller cannot overwrite stored guideline metadata.
        sample_roles: Sequence[str] | None = None,
        guideline_version_legacy: str | None = None,
    ) -> dict[str, Any]:
        _ = sample_roles  # rejected as overwrite source
        if guideline_version_legacy is not None and guideline_version is None:
            # Old keyword used as overwrite — reject silent overwrite path.
            raise ValueError(
                "caller-supplied guideline_version cannot overwrite stored assignment metadata; "
                "pass guideline_version as an optional verification filter only"
            )
        if is_git_tracked_research_path(destination) and not (
            self.config.allow_real_content_in_git_tracked_paths
        ):
            raise ValueError(f"refusing export into git-tracked path: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        rows = self.store.export_latest_annotation_events(
            source_run_id=source_run_id,
            batch_id=batch_id,
            annotator_id=annotator_id,
            guideline_version=guideline_version,
        )
        if not rows:
            raise ValueError("no annotation events match export scope")
        guidelines = {str(r["assignment_guideline_version"]) for r in rows}
        if len(guidelines) > 1 and not allow_mixed_guideline:
            raise ValueError(
                f"mixed guideline versions in export scope: {sorted(guidelines)}; "
                "request allow_mixed_guideline=True for an explicitly typed mixed export"
            )
        batches = {str(r["assignment_batch_id"]) for r in rows}
        if len(batches) > 1:
            raise ValueError(f"mixed batches in export scope: {sorted(batches)}")
        out_models: list[dict[str, Any]] = []
        for row in rows:
            roles_raw = row.get("assignment_sample_roles")
            if not roles_raw:
                raise ValueError(
                    f"missing assignment sample_roles for assignment {row['assignment_id']}"
                )
            roles = tuple(json.loads(roles_raw)) if isinstance(roles_raw, str) else tuple(roles_raw)
            if not roles:
                raise ValueError("sample_roles must not be empty or invented during export")
            cannot = bool(row["cannot_determine"])
            model = PilotRelevanceAnnotationV1(
                article_id=row["article_id"],
                duplicate_cluster_id=str(row["assignment_cluster_id"]),
                sample_roles=roles,
                annotator_id=row["annotator_id"],
                guideline_version=str(row["assignment_guideline_version"]),
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
            payload["source_run_id"] = row["assignment_source_run_id"]
            payload["source_assignment_manifest_sha256"] = row["assignment_manifest_sha256"]
            payload["assignment_schema_version"] = row["assignment_schema_version"]
            payload["batch_id"] = row["assignment_batch_id"]
            payload["event_id"] = row["event_id"]
            payload["revision"] = row["revision"]
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
        scope = {
            "source_run_id": source_run_id,
            "batch_id": batch_id,
            "annotator_id": annotator_id,
            "guideline_version_filter": guideline_version,
            "export_type": "raw_annotations_jsonl",
        }
        self.store.record_export(
            export_id=export_id,
            export_type="raw_annotations_jsonl",
            destination=str(destination),
            record_count=len(out_models),
            content_hash=content_hash,
            export_scope=scope,
        )
        return {
            "destination": str(destination),
            "record_count": len(out_models),
            "content_hash": content_hash,
            "export_id": export_id,
            "scope": scope,
        }

    def export_duplicate_reviews_jsonl(
        self,
        *,
        destination: Path,
        source_run_id: str,
        batch_id: str,
    ) -> dict[str, Any]:
        if is_git_tracked_research_path(destination) and not (
            self.config.allow_real_content_in_git_tracked_paths
        ):
            raise ValueError(f"refusing export into git-tracked path: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        rows = self.store.export_latest_duplicate_events(
            source_run_id=source_run_id, batch_id=batch_id
        )
        out = []
        for row in rows:
            out.append(
                {
                    "pair_id": row["pair_id"],
                    "article_id_a": row["article_id_a"],
                    "article_id_b": row["article_id_b"],
                    "reviewer_id": row["reviewer_id"],
                    "pair_label": row["pair_label"],
                    "uncertain": bool(row["uncertain"]),
                    "reason": row["reason"],
                    "batch_id": row["assignment_batch_id"],
                    "source_run_id": row["assignment_source_run_id"],
                    "guideline_version": row.get("assignment_guideline_version"),
                    "event_id": row["event_id"],
                    "revision": row["revision"],
                    "reviewed_at": row["created_at"],
                }
            )
        out.sort(key=lambda r: (r["pair_id"], r["reviewer_id"]))
        text = "\n".join(json.dumps(r, sort_keys=True) for r in out)
        if text:
            text += "\n"
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        tmp = destination.with_suffix(destination.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, destination)
        export_id = content_hash[:24]
        scope = {
            "source_run_id": source_run_id,
            "batch_id": batch_id,
            "export_type": "duplicate_reviews_jsonl",
        }
        self.store.record_export(
            export_id=export_id,
            export_type="duplicate_reviews_jsonl",
            destination=str(destination),
            record_count=len(out),
            content_hash=content_hash,
            export_scope=scope,
        )
        return {
            "destination": str(destination),
            "record_count": len(out),
            "content_hash": content_hash,
            "export_id": export_id,
            "scope": scope,
        }

    def export_adjudications_jsonl(
        self,
        *,
        destination: Path,
        source_run_id: str,
        batch_id: str,
    ) -> dict[str, Any]:
        if is_git_tracked_research_path(destination) and not (
            self.config.allow_real_content_in_git_tracked_paths
        ):
            raise ValueError(f"refusing export into git-tracked path: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        rows = self.store.export_latest_adjudication_events(
            source_run_id=source_run_id, batch_id=batch_id
        )
        out = []
        for row in rows:
            out.append(
                {
                    "article_id": row["article_id"],
                    "duplicate_cluster_id": row["assignment_cluster_id"],
                    "adjudicator_id": row["adjudicator_id"],
                    "guideline_version": row["assignment_guideline_version"],
                    "relevance_label": row["relevance_label"],
                    "cannot_determine": bool(row["cannot_determine"]),
                    "cannot_determine_reason": row["cannot_determine_reason"],
                    "central_entity_ids": json.loads(row["central_entity_ids"]),
                    "secondary_entity_ids": json.loads(row["secondary_entity_ids"]),
                    "disagreement_cause": row["disagreement_cause"],
                    "adjudication_reason": row["adjudication_reason"],
                    "raw_event_ids": json.loads(
                        row.get("raw_event_ids") or row.get("assignment_raw_event_ids") or "[]"
                    ),
                    "batch_id": row["assignment_batch_id"],
                    "source_run_id": row["assignment_source_run_id"],
                    "event_id": row["event_id"],
                    "revision": row["revision"],
                    "adjudicated_at": row["created_at"],
                }
            )
        out.sort(key=lambda r: (r["article_id"], r["adjudicator_id"]))
        text = "\n".join(json.dumps(r, sort_keys=True) for r in out)
        if text:
            text += "\n"
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        tmp = destination.with_suffix(destination.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, destination)
        export_id = content_hash[:24]
        scope = {
            "source_run_id": source_run_id,
            "batch_id": batch_id,
            "export_type": "adjudications_jsonl",
        }
        self.store.record_export(
            export_id=export_id,
            export_type="adjudications_jsonl",
            destination=str(destination),
            record_count=len(out),
            content_hash=content_hash,
            export_scope=scope,
        )
        return {
            "destination": str(destination),
            "record_count": len(out),
            "content_hash": content_hash,
            "export_id": export_id,
            "scope": scope,
        }

    def audit_snapshot(self, *, source_run_id: Optional[str] = None) -> dict[str, Any]:
        counts = self.store.completion_counts(source_run_id=source_run_id)
        ann_ready = counts["annotation_assigned"] > 0 and counts["annotation_incomplete"] == 0
        dup_ready = counts["duplicate_incomplete"] == 0
        adj_ready = counts["adjudications_remaining"] == 0
        double_ready = (
            counts["double_annotation_required"] == 0
            or counts["double_annotation_complete"] >= counts["double_annotation_required"]
        )
        raw_export_ready = ann_ready
        dup_export_ready = counts["duplicate_assigned"] > 0 and dup_ready
        adj_export_ready = counts["adjudication_assigned"] > 0 and adj_ready
        formal_ready = (
            ann_ready
            and double_ready
            and dup_ready
            and adj_ready
            and counts["annotation_assigned"] > 0
        )
        return {
            "schema_version": self.store.schema_version(),
            "database_path": str(self.config.database_path),
            "source_run_id": source_run_id,
            "counts": counts,
            "readiness": {
                "annotation": ann_ready,
                "duplicate_review": dup_ready,
                "adjudication": adj_ready,
                "raw_annotation_export": raw_export_ready,
                "duplicate_review_export": dup_export_ready,
                "adjudication_export": adj_export_ready,
                "formal_pilot_analysis": formal_ready,
            },
            # Global export_ready is false unless formal analysis is ready.
            "export_ready": formal_ready,
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


def entity_options() -> list[EntityOption]:
    entities = load_entity_references(None)
    return [
        EntityOption(
            entity_id=e.entity_id,
            display_name=e.display_name,
            ticker=e.ticker,
            entity_type=e.entity_type,
        )
        for e in sorted(entities, key=lambda x: (x.display_name, x.entity_id))
    ]

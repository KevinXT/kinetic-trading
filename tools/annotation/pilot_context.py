"""Selected local pilot-run context and stable UI submission tokens."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Mapping, MutableMapping, Optional, Sequence

ASSIGNMENT_SCHEMA_VERSION = "annotation-assignment-provenance-v1"
PILOT_CONTEXT_KEY = "pilot_run_context"
SUBMISSION_TOKEN_PREFIX = "submission_token|"


@dataclass(frozen=True)
class PilotRunContext:
    """Single selected local pilot context shared across workstation pages."""

    source_run_id: str
    source_task_name: str
    source_artifact_root: str
    config_path: str
    git_commit: Optional[str]
    source_corpus_sha256: str
    source_article_records_sha256: str
    source_assignment_manifest_sha256: str
    guideline_version: str
    batch_ids: tuple[str, ...]
    database_path: str
    articles_artifact_path: str
    entity_reference_version: str
    selected_batch_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PilotRunContext":
        batches = data.get("batch_ids") or ()
        if isinstance(batches, str):
            batches = [x for x in batches.split("|") if x.strip()]
        return cls(
            source_run_id=str(data["source_run_id"]),
            source_task_name=str(data["source_task_name"]),
            source_artifact_root=str(data["source_artifact_root"]),
            config_path=str(data["config_path"]),
            git_commit=(str(data["git_commit"]) if data.get("git_commit") else None),
            source_corpus_sha256=str(data["source_corpus_sha256"]),
            source_article_records_sha256=str(data["source_article_records_sha256"]),
            source_assignment_manifest_sha256=str(data["source_assignment_manifest_sha256"]),
            guideline_version=str(data["guideline_version"]),
            batch_ids=tuple(batches),
            database_path=str(data["database_path"]),
            articles_artifact_path=str(data["articles_artifact_path"]),
            entity_reference_version=str(data["entity_reference_version"]),
            selected_batch_id=(
                str(data["selected_batch_id"]) if data.get("selected_batch_id") else None
            ),
        )


@dataclass(frozen=True)
class AssignmentProvenanceV1:
    source_run_id: str
    source_task_name: str
    source_artifact_root: str
    source_assignment_manifest_sha256: str
    source_corpus_sha256: str
    source_article_records_sha256: str
    article_id: str
    duplicate_cluster_id: str
    article_title_sha256: str
    article_body_sha256: Optional[str]
    article_normalization_version: str
    guideline_version: str
    batch_id: str
    sample_roles: tuple[str, ...]
    entity_reference_version: str
    assignment_schema_version: str = ASSIGNMENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_run_id": self.source_run_id,
            "source_task_name": self.source_task_name,
            "source_artifact_root": self.source_artifact_root,
            "source_assignment_manifest_sha256": self.source_assignment_manifest_sha256,
            "source_corpus_sha256": self.source_corpus_sha256,
            "source_article_records_sha256": self.source_article_records_sha256,
            "article_id": self.article_id,
            "duplicate_cluster_id": self.duplicate_cluster_id,
            "article_title_sha256": self.article_title_sha256,
            "article_body_sha256": self.article_body_sha256,
            "article_normalization_version": self.article_normalization_version,
            "guideline_version": self.guideline_version,
            "batch_id": self.batch_id,
            "sample_roles": list(self.sample_roles),
            "entity_reference_version": self.entity_reference_version,
            "assignment_schema_version": self.assignment_schema_version,
        }


def get_or_create_submission_token(
    session_state: MutableMapping[str, Any],
    *,
    scope: str,
    form_fingerprint: str,
) -> str:
    """Stable client submission token until success acknowledgement.

    A Streamlit rerun or rapid double-click with the same form fingerprint
    reuses the token. After a successful save, call ``rotate_submission_token``.
    """
    key = f"{SUBMISSION_TOKEN_PREFIX}{scope}"
    meta_key = f"{key}|fingerprint"
    token = session_state.get(key)
    prev_fp = session_state.get(meta_key)
    if token and prev_fp == form_fingerprint:
        return str(token)
    token = str(uuid.uuid4())
    session_state[key] = token
    session_state[meta_key] = form_fingerprint
    return token


def rotate_submission_token(
    session_state: MutableMapping[str, Any],
    *,
    scope: str,
) -> None:
    key = f"{SUBMISSION_TOKEN_PREFIX}{scope}"
    session_state.pop(key, None)
    session_state.pop(f"{key}|fingerprint", None)


def form_fingerprint(parts: Sequence[Any]) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def clear_unsaved_form_state(
    session_state: MutableMapping[str, Any],
    *,
    prefixes: Sequence[str] = (
        SUBMISSION_TOKEN_PREFIX,
        "ann_index",
        "dup_index",
        "adj_index",
    ),
) -> None:
    """Invalidate unsaved form/navigation state when pilot context changes."""
    for key in list(session_state.keys()):
        sk = str(key)
        if any(sk == p or sk.startswith(p) for p in prefixes):
            session_state.pop(key, None)


@dataclass
class SourceArtifactMismatch(Exception):
    """Raised when displayed article content disagrees with assignment binding."""

    state: str = "SOURCE_ARTIFACT_MISMATCH"
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.state}: {self.detail}" if self.detail else self.state

"""SQLite append-only annotation event store for the local workstation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

SCHEMA_VERSION = 2
BUSY_TIMEOUT_MS = 5000
ASSIGNMENT_PROVENANCE_COLUMNS = (
    "source_run_id",
    "source_task_name",
    "source_artifact_root",
    "source_assignment_manifest_sha256",
    "source_corpus_sha256",
    "source_article_records_sha256",
    "article_title_sha256",
    "article_body_sha256",
    "article_normalization_version",
    "sample_roles",
    "entity_reference_version",
    "assignment_schema_version",
)


def utc_now() -> datetime:
    """Timezone-aware UTC now (production clock)."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def make_idempotency_key(
    *,
    assignment_id: str,
    actor_id: str,
    client_submission_id: str,
    event_type: str,
) -> str:
    raw = f"{assignment_id}|{actor_id}|{client_submission_id}|{event_type}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AnnotationCurrentState:
    assignment_id: str
    article_id: str
    annotator_id: str
    relevance_label: Optional[int]
    cannot_determine: bool
    cannot_determine_reason: Optional[str]
    central_entity_ids: tuple[str, ...]
    secondary_entity_ids: tuple[str, ...]
    evidence_text: Optional[str]
    evidence_start: Optional[int]
    evidence_end: Optional[int]
    content_sufficient: bool
    uncertain: bool
    uncertainty_reason: Optional[str]
    decision_reason_code: str
    notes: Optional[str]
    revision: int
    last_saved_at: str
    event_id: str


class AnnotationStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=BUSY_TIMEOUT_MS / 1000.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.Error:
            pass
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _table_columns(self, conn: sqlite3.Connection, table: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(r["name"]) for r in rows}

    def _ensure_schema(self) -> None:
        with self.transaction() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS store_metadata (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS annotation_assignments (
                  assignment_id TEXT PRIMARY KEY,
                  article_id TEXT NOT NULL,
                  duplicate_cluster_id TEXT NOT NULL,
                  annotator_id TEXT NOT NULL,
                  batch_id TEXT NOT NULL,
                  guideline_version TEXT NOT NULL,
                  assignment_status TEXT NOT NULL,
                  assignment_order INTEGER NOT NULL,
                  created_at TEXT NOT NULL,
                  source_run_id TEXT,
                  source_task_name TEXT,
                  source_artifact_root TEXT,
                  source_assignment_manifest_sha256 TEXT,
                  source_corpus_sha256 TEXT,
                  source_article_records_sha256 TEXT,
                  article_title_sha256 TEXT,
                  article_body_sha256 TEXT,
                  article_normalization_version TEXT,
                  sample_roles TEXT,
                  entity_reference_version TEXT,
                  assignment_schema_version TEXT,
                  UNIQUE(article_id, annotator_id, batch_id)
                );

                CREATE TABLE IF NOT EXISTS annotation_events (
                  event_id TEXT PRIMARY KEY,
                  idempotency_key TEXT NOT NULL UNIQUE,
                  assignment_id TEXT NOT NULL,
                  article_id TEXT NOT NULL,
                  annotator_id TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  previous_revision INTEGER,
                  revision INTEGER NOT NULL,
                  relevance_label INTEGER,
                  cannot_determine INTEGER NOT NULL,
                  cannot_determine_reason TEXT,
                  central_entity_ids TEXT NOT NULL,
                  secondary_entity_ids TEXT NOT NULL,
                  evidence_text TEXT,
                  evidence_start INTEGER,
                  evidence_end INTEGER,
                  content_sufficient INTEGER NOT NULL,
                  uncertain INTEGER NOT NULL,
                  uncertainty_reason TEXT,
                  decision_reason_code TEXT NOT NULL,
                  notes TEXT,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(assignment_id) REFERENCES annotation_assignments(assignment_id)
                );

                CREATE TABLE IF NOT EXISTS duplicate_review_assignments (
                  assignment_id TEXT PRIMARY KEY,
                  pair_id TEXT NOT NULL,
                  article_id_a TEXT NOT NULL,
                  article_id_b TEXT NOT NULL,
                  reviewer_id TEXT NOT NULL,
                  batch_id TEXT NOT NULL,
                  assignment_status TEXT NOT NULL,
                  assignment_order INTEGER NOT NULL,
                  created_at TEXT NOT NULL,
                  source_run_id TEXT,
                  source_task_name TEXT,
                  source_artifact_root TEXT,
                  source_assignment_manifest_sha256 TEXT,
                  source_corpus_sha256 TEXT,
                  source_article_records_sha256 TEXT,
                  article_a_title_sha256 TEXT,
                  article_a_body_sha256 TEXT,
                  article_b_title_sha256 TEXT,
                  article_b_body_sha256 TEXT,
                  article_normalization_version TEXT,
                  guideline_version TEXT,
                  assignment_schema_version TEXT,
                  UNIQUE(pair_id, reviewer_id, batch_id)
                );

                CREATE TABLE IF NOT EXISTS duplicate_review_events (
                  event_id TEXT PRIMARY KEY,
                  idempotency_key TEXT NOT NULL UNIQUE,
                  assignment_id TEXT NOT NULL,
                  pair_id TEXT NOT NULL,
                  reviewer_id TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  previous_revision INTEGER,
                  revision INTEGER NOT NULL,
                  pair_label TEXT NOT NULL,
                  uncertain INTEGER NOT NULL,
                  reason TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(assignment_id) REFERENCES duplicate_review_assignments(assignment_id)
                );

                CREATE TABLE IF NOT EXISTS adjudication_assignments (
                  assignment_id TEXT PRIMARY KEY,
                  article_id TEXT NOT NULL,
                  duplicate_cluster_id TEXT NOT NULL,
                  adjudicator_id TEXT NOT NULL,
                  guideline_version TEXT NOT NULL,
                  raw_event_ids TEXT NOT NULL,
                  assignment_status TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  batch_id TEXT,
                  source_run_id TEXT,
                  source_task_name TEXT,
                  source_artifact_root TEXT,
                  source_assignment_manifest_sha256 TEXT,
                  source_corpus_sha256 TEXT,
                  source_article_records_sha256 TEXT,
                  article_title_sha256 TEXT,
                  article_body_sha256 TEXT,
                  article_normalization_version TEXT,
                  assignment_schema_version TEXT,
                  UNIQUE(article_id, adjudicator_id, guideline_version)
                );

                CREATE TABLE IF NOT EXISTS adjudication_events (
                  event_id TEXT PRIMARY KEY,
                  idempotency_key TEXT NOT NULL UNIQUE,
                  assignment_id TEXT NOT NULL,
                  article_id TEXT NOT NULL,
                  adjudicator_id TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  previous_revision INTEGER,
                  revision INTEGER NOT NULL,
                  relevance_label INTEGER,
                  cannot_determine INTEGER NOT NULL,
                  cannot_determine_reason TEXT,
                  central_entity_ids TEXT NOT NULL,
                  secondary_entity_ids TEXT NOT NULL,
                  disagreement_cause TEXT NOT NULL,
                  adjudication_reason TEXT NOT NULL,
                  raw_event_ids TEXT,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(assignment_id) REFERENCES adjudication_assignments(assignment_id)
                );

                CREATE TABLE IF NOT EXISTS export_events (
                  export_id TEXT PRIMARY KEY,
                  export_type TEXT NOT NULL,
                  destination TEXT NOT NULL,
                  record_count INTEGER NOT NULL,
                  content_hash TEXT NOT NULL,
                  export_version TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  export_scope TEXT
                );

                CREATE TABLE IF NOT EXISTS pilot_contexts (
                  source_run_id TEXT PRIMARY KEY,
                  payload_json TEXT NOT NULL,
                  selected_at TEXT NOT NULL
                );
                """)
            # Migrate older DBs that predate provenance columns.
            for table, cols in (
                (
                    "annotation_assignments",
                    ASSIGNMENT_PROVENANCE_COLUMNS,
                ),
                (
                    "duplicate_review_assignments",
                    (
                        "source_run_id",
                        "source_task_name",
                        "source_artifact_root",
                        "source_assignment_manifest_sha256",
                        "source_corpus_sha256",
                        "source_article_records_sha256",
                        "article_a_title_sha256",
                        "article_a_body_sha256",
                        "article_b_title_sha256",
                        "article_b_body_sha256",
                        "article_normalization_version",
                        "guideline_version",
                        "assignment_schema_version",
                    ),
                ),
                (
                    "adjudication_assignments",
                    (
                        "batch_id",
                        "source_run_id",
                        "source_task_name",
                        "source_artifact_root",
                        "source_assignment_manifest_sha256",
                        "source_corpus_sha256",
                        "source_article_records_sha256",
                        "article_title_sha256",
                        "article_body_sha256",
                        "article_normalization_version",
                        "assignment_schema_version",
                    ),
                ),
                ("adjudication_events", ("raw_event_ids",)),
                ("export_events", ("export_scope",)),
            ):
                existing = self._table_columns(conn, table)
                for col in cols:
                    if col not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")

            row = conn.execute(
                "SELECT value FROM store_metadata WHERE key = 'schema_version'"
            ).fetchone()
            now = utc_now_iso()
            if row is None:
                conn.execute(
                    "INSERT INTO store_metadata(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
                conn.execute(
                    "INSERT INTO store_metadata(key, value) VALUES ('created_at', ?)",
                    (now,),
                )
                conn.execute(
                    "INSERT INTO store_metadata(key, value) VALUES ('last_migration_at', ?)",
                    (now,),
                )
            else:
                version = int(row["value"])
                if version > SCHEMA_VERSION:
                    raise ValueError(f"database schema version {version} is newer than code")
                if version < SCHEMA_VERSION:
                    conn.execute(
                        "UPDATE store_metadata SET value = ? WHERE key = 'schema_version'",
                        (str(SCHEMA_VERSION),),
                    )
                    conn.execute(
                        "INSERT INTO store_metadata(key, value) VALUES ('last_migration_at', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (now,),
                    )

    def schema_version(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM store_metadata WHERE key = 'schema_version'"
            ).fetchone()
            return int(row["value"]) if row else 0

    def save_pilot_context(self, *, source_run_id: str, payload: Mapping[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO pilot_contexts(source_run_id, payload_json, selected_at)
                VALUES (?, ?, ?)
                ON CONFLICT(source_run_id) DO UPDATE SET
                  payload_json = excluded.payload_json,
                  selected_at = excluded.selected_at
                """,
                (source_run_id, json.dumps(dict(payload), sort_keys=True), utc_now_iso()),
            )

    def load_pilot_context(self, source_run_id: str) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM pilot_contexts WHERE source_run_id = ?",
                (source_run_id,),
            ).fetchone()
        if row is None:
            return None
        data = json.loads(row["payload_json"])
        return data if isinstance(data, dict) else None

    def upsert_annotation_assignment(
        self,
        *,
        assignment_id: str,
        article_id: str,
        duplicate_cluster_id: str,
        annotator_id: str,
        batch_id: str,
        guideline_version: str,
        assignment_order: int,
        assignment_status: str = "assigned",
        source_run_id: Optional[str] = None,
        source_task_name: Optional[str] = None,
        source_artifact_root: Optional[str] = None,
        source_assignment_manifest_sha256: Optional[str] = None,
        source_corpus_sha256: Optional[str] = None,
        source_article_records_sha256: Optional[str] = None,
        article_title_sha256: Optional[str] = None,
        article_body_sha256: Optional[str] = None,
        article_normalization_version: Optional[str] = None,
        sample_roles: Sequence[str] = (),
        entity_reference_version: Optional[str] = None,
        assignment_schema_version: Optional[str] = None,
    ) -> None:
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT assignment_id FROM annotation_assignments WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
            if existing is None:
                conflict = conn.execute(
                    """
                    SELECT assignment_id FROM annotation_assignments
                    WHERE article_id = ? AND annotator_id = ? AND batch_id = ?
                    """,
                    (article_id, annotator_id, batch_id),
                ).fetchone()
                if conflict is not None:
                    raise ValueError(
                        f"conflicting annotation assignment for "
                        f"{article_id}/{annotator_id}/{batch_id}"
                    )
            conn.execute(
                """
                INSERT INTO annotation_assignments(
                  assignment_id, article_id, duplicate_cluster_id, annotator_id,
                  batch_id, guideline_version, assignment_status, assignment_order, created_at,
                  source_run_id, source_task_name, source_artifact_root,
                  source_assignment_manifest_sha256, source_corpus_sha256,
                  source_article_records_sha256, article_title_sha256, article_body_sha256,
                  article_normalization_version, sample_roles, entity_reference_version,
                  assignment_schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(assignment_id) DO UPDATE SET
                  assignment_status = excluded.assignment_status,
                  assignment_order = excluded.assignment_order
                """,
                (
                    assignment_id,
                    article_id,
                    duplicate_cluster_id,
                    annotator_id,
                    batch_id,
                    guideline_version,
                    assignment_status,
                    assignment_order,
                    utc_now_iso(),
                    source_run_id,
                    source_task_name,
                    source_artifact_root,
                    source_assignment_manifest_sha256,
                    source_corpus_sha256,
                    source_article_records_sha256,
                    article_title_sha256,
                    article_body_sha256,
                    article_normalization_version,
                    json.dumps(list(sample_roles)),
                    entity_reference_version,
                    assignment_schema_version,
                ),
            )

    def get_annotation_assignment(self, assignment_id: str) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM annotation_assignments WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_annotation_assignments(
        self,
        *,
        annotator_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        source_run_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if annotator_id:
            clauses.append("annotator_id = ?")
            params.append(annotator_id)
        if batch_id:
            clauses.append("batch_id = ?")
            params.append(batch_id)
        if source_run_id:
            clauses.append("source_run_id = ?")
            params.append(source_run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM annotation_assignments
                {where}
                ORDER BY assignment_order ASC, assignment_id ASC
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def append_annotation_event(
        self,
        *,
        assignment_id: str,
        article_id: str,
        annotator_id: str,
        client_submission_id: str,
        event_type: str,
        relevance_label: Optional[int],
        cannot_determine: bool,
        cannot_determine_reason: Optional[str],
        central_entity_ids: Sequence[str],
        secondary_entity_ids: Sequence[str],
        evidence_text: Optional[str],
        evidence_start: Optional[int],
        evidence_end: Optional[int],
        content_sufficient: bool,
        uncertain: bool,
        uncertainty_reason: Optional[str],
        decision_reason_code: str,
        notes: Optional[str],
    ) -> dict[str, Any]:
        idem = make_idempotency_key(
            assignment_id=assignment_id,
            actor_id=annotator_id,
            client_submission_id=client_submission_id,
            event_type=event_type,
        )
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM annotation_events WHERE idempotency_key = ?",
                (idem,),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            prev = conn.execute(
                """
                SELECT revision FROM annotation_events
                WHERE assignment_id = ?
                ORDER BY revision DESC LIMIT 1
                """,
                (assignment_id,),
            ).fetchone()
            previous_revision = int(prev["revision"]) if prev else None
            revision = (previous_revision or 0) + 1
            event_id = hashlib.sha256(
                f"{idem}|{revision}|{utc_now_iso()}".encode("utf-8")
            ).hexdigest()[:32]
            created = utc_now_iso()
            conn.execute(
                """
                INSERT INTO annotation_events(
                  event_id, idempotency_key, assignment_id, article_id, annotator_id,
                  event_type, previous_revision, revision, relevance_label, cannot_determine,
                  cannot_determine_reason, central_entity_ids, secondary_entity_ids,
                  evidence_text, evidence_start, evidence_end, content_sufficient,
                  uncertain, uncertainty_reason, decision_reason_code, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    idem,
                    assignment_id,
                    article_id,
                    annotator_id,
                    event_type,
                    previous_revision,
                    revision,
                    relevance_label,
                    1 if cannot_determine else 0,
                    cannot_determine_reason,
                    json.dumps(list(central_entity_ids)),
                    json.dumps(list(secondary_entity_ids)),
                    evidence_text,
                    evidence_start,
                    evidence_end,
                    1 if content_sufficient else 0,
                    1 if uncertain else 0,
                    uncertainty_reason,
                    decision_reason_code,
                    notes,
                    created,
                ),
            )
            conn.execute(
                """
                UPDATE annotation_assignments
                SET assignment_status = 'completed'
                WHERE assignment_id = ?
                """,
                (assignment_id,),
            )
            row = conn.execute(
                "SELECT * FROM annotation_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            assert row is not None
            return dict(row)

    def current_annotation_state(self, assignment_id: str) -> Optional[AnnotationCurrentState]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM annotation_events
                WHERE assignment_id = ?
                ORDER BY revision DESC LIMIT 1
                """,
                (assignment_id,),
            ).fetchone()
        if row is None:
            return None
        return AnnotationCurrentState(
            assignment_id=row["assignment_id"],
            article_id=row["article_id"],
            annotator_id=row["annotator_id"],
            relevance_label=row["relevance_label"],
            cannot_determine=bool(row["cannot_determine"]),
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
            notes=row["notes"],
            revision=int(row["revision"]),
            last_saved_at=row["created_at"],
            event_id=row["event_id"],
        )

    def annotation_revision_count(self, assignment_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM annotation_events WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
        return int(row["n"]) if row else 0

    def get_annotation_event(self, event_id: str) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM annotation_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return dict(row) if row else None

    def upsert_duplicate_assignment(
        self,
        *,
        assignment_id: str,
        pair_id: str,
        article_id_a: str,
        article_id_b: str,
        reviewer_id: str,
        batch_id: str,
        assignment_order: int,
        assignment_status: str = "assigned",
        source_run_id: Optional[str] = None,
        source_task_name: Optional[str] = None,
        source_artifact_root: Optional[str] = None,
        source_assignment_manifest_sha256: Optional[str] = None,
        source_corpus_sha256: Optional[str] = None,
        source_article_records_sha256: Optional[str] = None,
        article_a_title_sha256: Optional[str] = None,
        article_a_body_sha256: Optional[str] = None,
        article_b_title_sha256: Optional[str] = None,
        article_b_body_sha256: Optional[str] = None,
        article_normalization_version: Optional[str] = None,
        guideline_version: Optional[str] = None,
        assignment_schema_version: Optional[str] = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO duplicate_review_assignments(
                  assignment_id, pair_id, article_id_a, article_id_b, reviewer_id,
                  batch_id, assignment_status, assignment_order, created_at,
                  source_run_id, source_task_name, source_artifact_root,
                  source_assignment_manifest_sha256, source_corpus_sha256,
                  source_article_records_sha256, article_a_title_sha256,
                  article_a_body_sha256, article_b_title_sha256, article_b_body_sha256,
                  article_normalization_version, guideline_version, assignment_schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(assignment_id) DO UPDATE SET
                  assignment_status = excluded.assignment_status
                """,
                (
                    assignment_id,
                    pair_id,
                    article_id_a,
                    article_id_b,
                    reviewer_id,
                    batch_id,
                    assignment_status,
                    assignment_order,
                    utc_now_iso(),
                    source_run_id,
                    source_task_name,
                    source_artifact_root,
                    source_assignment_manifest_sha256,
                    source_corpus_sha256,
                    source_article_records_sha256,
                    article_a_title_sha256,
                    article_a_body_sha256,
                    article_b_title_sha256,
                    article_b_body_sha256,
                    article_normalization_version,
                    guideline_version,
                    assignment_schema_version,
                ),
            )

    def list_duplicate_assignments(
        self,
        *,
        reviewer_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        source_run_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if reviewer_id:
            clauses.append("reviewer_id = ?")
            params.append(reviewer_id)
        if batch_id:
            clauses.append("batch_id = ?")
            params.append(batch_id)
        if source_run_id:
            clauses.append("source_run_id = ?")
            params.append(source_run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM duplicate_review_assignments
                {where}
                ORDER BY assignment_order ASC, assignment_id ASC
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def append_duplicate_review_event(
        self,
        *,
        assignment_id: str,
        pair_id: str,
        reviewer_id: str,
        client_submission_id: str,
        pair_label: str,
        uncertain: bool,
        reason: str,
        event_type: str = "submit",
    ) -> dict[str, Any]:
        idem = make_idempotency_key(
            assignment_id=assignment_id,
            actor_id=reviewer_id,
            client_submission_id=client_submission_id,
            event_type=event_type,
        )
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM duplicate_review_events WHERE idempotency_key = ?",
                (idem,),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            prev = conn.execute(
                """
                SELECT revision FROM duplicate_review_events
                WHERE assignment_id = ?
                ORDER BY revision DESC LIMIT 1
                """,
                (assignment_id,),
            ).fetchone()
            previous_revision = int(prev["revision"]) if prev else None
            revision = (previous_revision or 0) + 1
            event_id = hashlib.sha256(f"dup|{idem}|{revision}".encode("utf-8")).hexdigest()[:32]
            created = utc_now_iso()
            conn.execute(
                """
                INSERT INTO duplicate_review_events(
                  event_id, idempotency_key, assignment_id, pair_id, reviewer_id,
                  event_type, previous_revision, revision, pair_label, uncertain,
                  reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    idem,
                    assignment_id,
                    pair_id,
                    reviewer_id,
                    event_type,
                    previous_revision,
                    revision,
                    pair_label,
                    1 if uncertain else 0,
                    reason,
                    created,
                ),
            )
            conn.execute(
                "UPDATE duplicate_review_assignments SET assignment_status = 'completed' "
                "WHERE assignment_id = ?",
                (assignment_id,),
            )
            row = conn.execute(
                "SELECT * FROM duplicate_review_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            assert row is not None
            return dict(row)

    def validate_adjudication_raw_events(
        self,
        *,
        raw_event_ids: Sequence[str],
        article_id: str,
        duplicate_cluster_id: str,
        batch_id: str,
        guideline_version: str,
        source_run_id: Optional[str],
        allow_same_annotator: bool = False,
        require_latest: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if len(raw_event_ids) != 2:
            raise ValueError("adjudication requires exactly two explicit raw event IDs")
        if raw_event_ids[0] == raw_event_ids[1]:
            raise ValueError("raw event IDs must be distinct")
        events = []
        for eid in raw_event_ids:
            ev = self.get_annotation_event(eid)
            if ev is None:
                raise ValueError(f"missing raw annotation event {eid}")
            events.append(ev)
        a, b = events
        for ev in events:
            if ev["article_id"] != article_id:
                raise ValueError("raw events must share the adjudication article_id")
            assignment = self.get_annotation_assignment(str(ev["assignment_id"]))
            if assignment is None:
                raise ValueError("raw event assignment missing")
            if assignment["duplicate_cluster_id"] != duplicate_cluster_id:
                raise ValueError("raw events must share duplicate_cluster_id")
            if assignment["batch_id"] != batch_id:
                raise ValueError("raw events must share batch_id")
            if assignment["guideline_version"] != guideline_version:
                raise ValueError("raw events must share guideline_version")
            if source_run_id and assignment.get("source_run_id") not in {None, source_run_id}:
                raise ValueError("raw events must share source_run_id")
            if require_latest:
                latest = self.current_annotation_state(str(ev["assignment_id"]))
                if latest is None or latest.event_id != ev["event_id"]:
                    raise ValueError(
                        "raw event is not the latest finalized revision for its assignment"
                    )
        if not allow_same_annotator and a["annotator_id"] == b["annotator_id"]:
            raise ValueError("inter-rater adjudication requires different annotator IDs")
        return a, b

    def upsert_adjudication_assignment(
        self,
        *,
        assignment_id: str,
        article_id: str,
        duplicate_cluster_id: str,
        adjudicator_id: str,
        guideline_version: str,
        raw_event_ids: Sequence[str],
        batch_id: Optional[str] = None,
        source_run_id: Optional[str] = None,
        source_task_name: Optional[str] = None,
        source_artifact_root: Optional[str] = None,
        source_assignment_manifest_sha256: Optional[str] = None,
        source_corpus_sha256: Optional[str] = None,
        source_article_records_sha256: Optional[str] = None,
        article_title_sha256: Optional[str] = None,
        article_body_sha256: Optional[str] = None,
        article_normalization_version: Optional[str] = None,
        assignment_schema_version: Optional[str] = None,
        allow_same_annotator: bool = False,
        validate_raw: bool = True,
    ) -> None:
        if validate_raw:
            if not batch_id:
                raise ValueError("batch_id required to validate adjudication raw events")
            self.validate_adjudication_raw_events(
                raw_event_ids=raw_event_ids,
                article_id=article_id,
                duplicate_cluster_id=duplicate_cluster_id,
                batch_id=batch_id,
                guideline_version=guideline_version,
                source_run_id=source_run_id,
                allow_same_annotator=allow_same_annotator,
            )
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO adjudication_assignments(
                  assignment_id, article_id, duplicate_cluster_id, adjudicator_id,
                  guideline_version, raw_event_ids, assignment_status, created_at,
                  batch_id, source_run_id, source_task_name, source_artifact_root,
                  source_assignment_manifest_sha256, source_corpus_sha256,
                  source_article_records_sha256, article_title_sha256, article_body_sha256,
                  article_normalization_version, assignment_schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, 'assigned', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(assignment_id) DO NOTHING
                """,
                (
                    assignment_id,
                    article_id,
                    duplicate_cluster_id,
                    adjudicator_id,
                    guideline_version,
                    json.dumps(list(raw_event_ids)),
                    utc_now_iso(),
                    batch_id,
                    source_run_id,
                    source_task_name,
                    source_artifact_root,
                    source_assignment_manifest_sha256,
                    source_corpus_sha256,
                    source_article_records_sha256,
                    article_title_sha256,
                    article_body_sha256,
                    article_normalization_version,
                    assignment_schema_version,
                ),
            )

    def list_adjudication_assignments(
        self, *, source_run_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if source_run_id:
                rows = conn.execute(
                    """
                    SELECT * FROM adjudication_assignments
                    WHERE source_run_id = ?
                    ORDER BY created_at ASC, assignment_id ASC
                    """,
                    (source_run_id,),
                ).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM adjudication_assignments
                    ORDER BY created_at ASC, assignment_id ASC
                    """).fetchall()
        return [dict(r) for r in rows]

    def append_adjudication_event(
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
        raw_event_ids: Sequence[str] = (),
        event_type: str = "submit",
    ) -> dict[str, Any]:
        idem = make_idempotency_key(
            assignment_id=assignment_id,
            actor_id=adjudicator_id,
            client_submission_id=client_submission_id,
            event_type=event_type,
        )
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM adjudication_events WHERE idempotency_key = ?",
                (idem,),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            prev = conn.execute(
                """
                SELECT revision FROM adjudication_events
                WHERE assignment_id = ?
                ORDER BY revision DESC LIMIT 1
                """,
                (assignment_id,),
            ).fetchone()
            previous_revision = int(prev["revision"]) if prev else None
            revision = (previous_revision or 0) + 1
            event_id = hashlib.sha256(f"adj|{idem}|{revision}".encode("utf-8")).hexdigest()[:32]
            created = utc_now_iso()
            conn.execute(
                """
                INSERT INTO adjudication_events(
                  event_id, idempotency_key, assignment_id, article_id, adjudicator_id,
                  event_type, previous_revision, revision, relevance_label, cannot_determine,
                  cannot_determine_reason, central_entity_ids, secondary_entity_ids,
                  disagreement_cause, adjudication_reason, raw_event_ids, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    idem,
                    assignment_id,
                    article_id,
                    adjudicator_id,
                    event_type,
                    previous_revision,
                    revision,
                    relevance_label,
                    1 if cannot_determine else 0,
                    cannot_determine_reason,
                    json.dumps(list(central_entity_ids)),
                    json.dumps(list(secondary_entity_ids)),
                    disagreement_cause,
                    adjudication_reason,
                    json.dumps(list(raw_event_ids)),
                    created,
                ),
            )
            conn.execute(
                "UPDATE adjudication_assignments SET assignment_status = 'completed' "
                "WHERE assignment_id = ?",
                (assignment_id,),
            )
            row = conn.execute(
                "SELECT * FROM adjudication_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            assert row is not None
            return dict(row)

    def record_export(
        self,
        *,
        export_id: str,
        export_type: str,
        destination: str,
        record_count: int,
        content_hash: str,
        export_version: str = "export-v1",
        export_scope: Optional[Mapping[str, Any]] = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO export_events(
                  export_id, export_type, destination, record_count,
                  content_hash, export_version, created_at, export_scope
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    export_id,
                    export_type,
                    destination,
                    record_count,
                    content_hash,
                    export_version,
                    utc_now_iso(),
                    json.dumps(dict(export_scope or {}), sort_keys=True),
                ),
            )

    def completion_counts(self, *, source_run_id: Optional[str] = None) -> dict[str, int]:
        with self.connect() as conn:
            if source_run_id:
                assigned = conn.execute(
                    "SELECT COUNT(*) AS n FROM annotation_assignments WHERE source_run_id = ?",
                    (source_run_id,),
                ).fetchone()["n"]
                completed = conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM annotation_assignments
                    WHERE source_run_id = ? AND assignment_status = 'completed'
                    """,
                    (source_run_id,),
                ).fetchone()["n"]
                double_required = conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM (
                      SELECT article_id, batch_id FROM annotation_assignments
                      WHERE source_run_id = ?
                      GROUP BY article_id, batch_id
                      HAVING COUNT(*) >= 2
                    )
                    """,
                    (source_run_id,),
                ).fetchone()["n"]
                double_complete = conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM (
                      SELECT article_id, batch_id FROM annotation_assignments
                      WHERE source_run_id = ? AND assignment_status = 'completed'
                      GROUP BY article_id, batch_id
                      HAVING COUNT(*) >= 2
                    )
                    """,
                    (source_run_id,),
                ).fetchone()["n"]
                dup_assigned = conn.execute(
                    "SELECT COUNT(*) AS n FROM duplicate_review_assignments "
                    "WHERE source_run_id = ?",
                    (source_run_id,),
                ).fetchone()["n"]
                dup_done = conn.execute(
                    "SELECT COUNT(*) AS n FROM duplicate_review_assignments "
                    "WHERE source_run_id = ? AND assignment_status = 'completed'",
                    (source_run_id,),
                ).fetchone()["n"]
                adj_assigned = conn.execute(
                    "SELECT COUNT(*) AS n FROM adjudication_assignments " "WHERE source_run_id = ?",
                    (source_run_id,),
                ).fetchone()["n"]
                adj_done = conn.execute(
                    "SELECT COUNT(*) AS n FROM adjudication_assignments "
                    "WHERE source_run_id = ? AND assignment_status = 'completed'",
                    (source_run_id,),
                ).fetchone()["n"]
            else:
                assigned = conn.execute(
                    "SELECT COUNT(*) AS n FROM annotation_assignments"
                ).fetchone()["n"]
                completed = conn.execute(
                    "SELECT COUNT(*) AS n FROM annotation_assignments "
                    "WHERE assignment_status = 'completed'"
                ).fetchone()["n"]
                double_required = conn.execute("""
                    SELECT COUNT(*) AS n FROM (
                      SELECT article_id, batch_id FROM annotation_assignments
                      GROUP BY article_id, batch_id
                      HAVING COUNT(*) >= 2
                    )
                    """).fetchone()["n"]
                double_complete = conn.execute("""
                    SELECT COUNT(*) AS n FROM (
                      SELECT article_id, batch_id FROM annotation_assignments
                      WHERE assignment_status = 'completed'
                      GROUP BY article_id, batch_id
                      HAVING COUNT(*) >= 2
                    )
                    """).fetchone()["n"]
                dup_assigned = conn.execute(
                    "SELECT COUNT(*) AS n FROM duplicate_review_assignments"
                ).fetchone()["n"]
                dup_done = conn.execute(
                    "SELECT COUNT(*) AS n FROM duplicate_review_assignments "
                    "WHERE assignment_status = 'completed'"
                ).fetchone()["n"]
                adj_assigned = conn.execute(
                    "SELECT COUNT(*) AS n FROM adjudication_assignments"
                ).fetchone()["n"]
                adj_done = conn.execute(
                    "SELECT COUNT(*) AS n FROM adjudication_assignments "
                    "WHERE assignment_status = 'completed'"
                ).fetchone()["n"]
            cannot = conn.execute("""
                SELECT COUNT(*) AS n FROM annotation_events e
                WHERE e.cannot_determine = 1
                  AND e.revision = (
                    SELECT MAX(e2.revision) FROM annotation_events e2
                    WHERE e2.assignment_id = e.assignment_id
                  )
                """).fetchone()["n"]
        return {
            "annotation_assigned": int(assigned),
            "annotation_completed": int(completed),
            "annotation_incomplete": int(assigned) - int(completed),
            "cannot_determine": int(cannot),
            "double_annotation_required": int(double_required),
            "double_annotation_complete": int(double_complete),
            "duplicate_assigned": int(dup_assigned),
            "duplicate_completed": int(dup_done),
            "duplicate_incomplete": int(dup_assigned) - int(dup_done),
            "adjudication_assigned": int(adj_assigned),
            "adjudication_completed": int(adj_done),
            "adjudications_remaining": int(adj_assigned) - int(adj_done),
        }

    def export_latest_annotation_events(
        self,
        *,
        source_run_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        annotator_id: Optional[str] = None,
        guideline_version: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        clauses = [
            "e.revision = ("
            "SELECT MAX(e2.revision) FROM annotation_events e2 "
            "WHERE e2.assignment_id = e.assignment_id)"
        ]
        params: list[Any] = []
        join = "INNER JOIN annotation_assignments a ON a.assignment_id = e.assignment_id"
        if source_run_id:
            clauses.append("a.source_run_id = ?")
            params.append(source_run_id)
        if batch_id:
            clauses.append("a.batch_id = ?")
            params.append(batch_id)
        if annotator_id:
            clauses.append("e.annotator_id = ?")
            params.append(annotator_id)
        if guideline_version:
            clauses.append("a.guideline_version = ?")
            params.append(guideline_version)
        where = " AND ".join(clauses)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT e.*, a.batch_id AS assignment_batch_id,
                       a.guideline_version AS assignment_guideline_version,
                       a.sample_roles AS assignment_sample_roles,
                       a.duplicate_cluster_id AS assignment_cluster_id,
                       a.source_run_id AS assignment_source_run_id,
                       a.source_assignment_manifest_sha256 AS assignment_manifest_sha256,
                       a.assignment_schema_version AS assignment_schema_version,
                       a.source_corpus_sha256 AS assignment_corpus_sha256,
                       a.entity_reference_version AS assignment_entity_reference_version
                FROM annotation_events e
                {join}
                WHERE {where}
                ORDER BY e.article_id ASC, e.annotator_id ASC
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def export_latest_duplicate_events(
        self,
        *,
        source_run_id: Optional[str] = None,
        batch_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        clauses = [
            "e.revision = ("
            "SELECT MAX(e2.revision) FROM duplicate_review_events e2 "
            "WHERE e2.assignment_id = e.assignment_id)"
        ]
        params: list[Any] = []
        if source_run_id:
            clauses.append("a.source_run_id = ?")
            params.append(source_run_id)
        if batch_id:
            clauses.append("a.batch_id = ?")
            params.append(batch_id)
        where = " AND ".join(clauses)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT e.*, a.batch_id AS assignment_batch_id,
                       a.source_run_id AS assignment_source_run_id,
                       a.guideline_version AS assignment_guideline_version,
                       a.article_id_a, a.article_id_b
                FROM duplicate_review_events e
                INNER JOIN duplicate_review_assignments a ON a.assignment_id = e.assignment_id
                WHERE {where}
                ORDER BY e.pair_id ASC, e.reviewer_id ASC
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def export_latest_adjudication_events(
        self,
        *,
        source_run_id: Optional[str] = None,
        batch_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        clauses = [
            "e.revision = ("
            "SELECT MAX(e2.revision) FROM adjudication_events e2 "
            "WHERE e2.assignment_id = e.assignment_id)"
        ]
        params: list[Any] = []
        if source_run_id:
            clauses.append("a.source_run_id = ?")
            params.append(source_run_id)
        if batch_id:
            clauses.append("a.batch_id = ?")
            params.append(batch_id)
        where = " AND ".join(clauses)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT e.*, a.batch_id AS assignment_batch_id,
                       a.guideline_version AS assignment_guideline_version,
                       a.source_run_id AS assignment_source_run_id,
                       a.duplicate_cluster_id AS assignment_cluster_id,
                       a.raw_event_ids AS assignment_raw_event_ids
                FROM adjudication_events e
                INNER JOIN adjudication_assignments a ON a.assignment_id = e.assignment_id
                WHERE {where}
                ORDER BY e.article_id ASC, e.adjudicator_id ASC
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

"""SQLite append-only annotation event store for the local workstation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 5000


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
                  created_at TEXT NOT NULL
                );
                """)
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
                    # Future migrations would run here.
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
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO annotation_assignments(
                  assignment_id, article_id, duplicate_cluster_id, annotator_id,
                  batch_id, guideline_version, assignment_status, assignment_order, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )

    def list_annotation_assignments(
        self, *, annotator_id: Optional[str] = None, batch_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if annotator_id:
            clauses.append("annotator_id = ?")
            params.append(annotator_id)
        if batch_id:
            clauses.append("batch_id = ?")
            params.append(batch_id)
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
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO duplicate_review_assignments(
                  assignment_id, pair_id, article_id_a, article_id_b, reviewer_id,
                  batch_id, assignment_status, assignment_order, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )

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

    def upsert_adjudication_assignment(
        self,
        *,
        assignment_id: str,
        article_id: str,
        duplicate_cluster_id: str,
        adjudicator_id: str,
        guideline_version: str,
        raw_event_ids: Sequence[str],
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO adjudication_assignments(
                  assignment_id, article_id, duplicate_cluster_id, adjudicator_id,
                  guideline_version, raw_event_ids, assignment_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'assigned', ?)
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
                ),
            )

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
                  disagreement_cause, adjudication_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO export_events(
                  export_id, export_type, destination, record_count,
                  content_hash, export_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    export_id,
                    export_type,
                    destination,
                    record_count,
                    content_hash,
                    export_version,
                    utc_now_iso(),
                ),
            )

    def completion_counts(self) -> dict[str, int]:
        with self.connect() as conn:
            assigned = conn.execute("SELECT COUNT(*) AS n FROM annotation_assignments").fetchone()[
                "n"
            ]
            completed = conn.execute(
                "SELECT COUNT(*) AS n FROM annotation_assignments "
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
        return {
            "annotation_assigned": int(assigned),
            "annotation_completed": int(completed),
            "annotation_incomplete": int(assigned) - int(completed),
            "cannot_determine": int(cannot),
            "duplicate_assigned": int(dup_assigned),
            "duplicate_completed": int(dup_done),
            "adjudication_assigned": int(adj_assigned),
            "adjudication_completed": int(adj_done),
            "adjudications_remaining": int(adj_assigned) - int(adj_done),
        }

    def export_latest_annotation_events(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT e.* FROM annotation_events e
                INNER JOIN (
                  SELECT assignment_id, MAX(revision) AS max_rev
                  FROM annotation_events GROUP BY assignment_id
                ) t ON e.assignment_id = t.assignment_id AND e.revision = t.max_rev
                ORDER BY e.article_id ASC, e.annotator_id ASC
                """).fetchall()
        return [dict(r) for r in rows]

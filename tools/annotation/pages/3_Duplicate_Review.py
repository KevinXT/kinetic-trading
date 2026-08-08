"""Page 3 — Blind duplicate-pair review."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import streamlit as st

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app_config import load_ui_config
from pilot_context import (
    ASSIGNMENT_SCHEMA_VERSION,
    PILOT_CONTEXT_KEY,
    PilotRunContext,
    SourceArtifactMismatch,
    form_fingerprint,
    get_or_create_submission_token,
    rotate_submission_token,
)
from pilot_service import PilotService

from kinetic.ml.relevance.pilot_models import PAIR_LABELS

st.set_page_config(page_title="Duplicate Review", layout="wide")
st.title("Duplicate Review")
st.caption("Similarity scores and thresholds are hidden.")

config = load_ui_config()
if "service" not in st.session_state:
    st.session_state.service = PilotService(config)
service: PilotService = st.session_state.service

ctx_raw = st.session_state.get(PILOT_CONTEXT_KEY)
if not ctx_raw:
    st.error("No pilot run selected. Load a bound context on Preflight / Audit first.")
    st.stop()
context = PilotRunContext.from_dict(ctx_raw)
st.info(
    f"Run `{context.source_run_id}` · batch `{context.selected_batch_id}` · "
    f"corpus `{context.source_corpus_sha256[:12]}…`"
)

reviewer_id = st.session_state.get("annotator_id", "reviewer_01")
pair_batch = Path(context.source_artifact_root) / "duplicate_pair_review_batch.csv"
if pair_batch.is_file() and st.button("Import bound pair assignments from selected run"):
    rows = list(csv.DictReader(pair_batch.open(encoding="utf-8")))
    articles = service.load_articles_index(Path(context.articles_artifact_path))
    for i, row in enumerate(rows):
        a = articles[row["article_id_a"]]
        b = articles[row["article_id_b"]]
        aid = f"dup_{row['pair_id']}_{reviewer_id}_{context.source_run_id}"[:48]
        service.store.upsert_duplicate_assignment(
            assignment_id=aid,
            pair_id=row["pair_id"],
            article_id_a=row["article_id_a"],
            article_id_b=row["article_id_b"],
            reviewer_id=reviewer_id,
            batch_id=context.selected_batch_id or f"dup_{context.source_run_id}",
            assignment_order=i + 1,
            source_run_id=context.source_run_id,
            source_task_name=context.source_task_name,
            source_artifact_root=context.source_artifact_root,
            source_assignment_manifest_sha256=context.source_assignment_manifest_sha256,
            source_corpus_sha256=context.source_corpus_sha256,
            source_article_records_sha256=context.source_article_records_sha256,
            article_a_title_sha256=a.title_sha256,
            article_a_body_sha256=a.body_sha256,
            article_b_title_sha256=b.title_sha256,
            article_b_body_sha256=b.body_sha256,
            article_normalization_version=a.normalization_version,
            guideline_version=context.guideline_version,
            assignment_schema_version=ASSIGNMENT_SCHEMA_VERSION,
        )
    st.success(f"Imported {len(rows)} bound pair assignments")

assignments = service.store.list_duplicate_assignments(
    reviewer_id=reviewer_id,
    source_run_id=context.source_run_id,
)
if not assignments:
    st.warning("No duplicate assignments for the selected pilot run.")
    st.stop()

if "dup_index" not in st.session_state:
    st.session_state.dup_index = 0
idx = max(0, min(st.session_state.dup_index, len(assignments) - 1))
assignment = assignments[idx]
try:
    articles = service.load_articles_index(Path(context.articles_artifact_path))
    a = articles[assignment["article_id_a"]]
    b = articles[assignment["article_id_b"]]
    if a.title_sha256 != assignment.get("article_a_title_sha256"):
        raise SourceArtifactMismatch(detail="article A title hash mismatch")
    if b.title_sha256 != assignment.get("article_b_title_sha256"):
        raise SourceArtifactMismatch(detail="article B title hash mismatch")
except (KeyError, SourceArtifactMismatch) as exc:
    st.error(f"SOURCE_ARTIFACT_MISMATCH: {exc}")
    st.stop()

view = service.build_blind_duplicate_view(
    assignment=assignment,
    article_a=a,
    article_b=b,
    queue_index=idx + 1,
    queue_total=len(assignments),
)

st.subheader(f"Pair {view.queue_index} of {view.queue_total}")
left, right = st.columns(2)
with left:
    st.markdown("### Article A")
    st.text(view.article_a["title"])
    st.caption(f"{view.article_a['domain']} · {view.article_a['published_at']}")
    st.text(view.article_a["description"])
    st.text(view.article_a["body"])
with right:
    st.markdown("### Article B")
    st.text(view.article_b["title"])
    st.caption(f"{view.article_b['domain']} · {view.article_b['published_at']}")
    st.text(view.article_b["description"])
    st.text(view.article_b["body"])

pair_label = st.selectbox("Pair label", options=sorted(PAIR_LABELS))
uncertain = st.checkbox("Uncertain")
reason = st.text_area("Reason", height=80)
if st.button("Save pair review", type="primary"):
    try:
        fp = form_fingerprint([view.assignment_id, reviewer_id, pair_label, uncertain, reason])
        client_id = get_or_create_submission_token(
            st.session_state,
            scope=f"dup|{view.assignment_id}|{reviewer_id}",
            form_fingerprint=fp,
        )
        service.save_duplicate_review(
            assignment_id=view.assignment_id,
            pair_id=view.pair_id,
            reviewer_id=reviewer_id,
            client_submission_id=client_id,
            pair_label=pair_label,
            uncertain=uncertain,
            reason=reason,
        )
        rotate_submission_token(st.session_state, scope=f"dup|{view.assignment_id}|{reviewer_id}")
        st.success("Saved.")
        st.session_state.dup_index = min(idx + 1, len(assignments) - 1)
        st.rerun()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Save failed: {type(exc).__name__}: {exc}")

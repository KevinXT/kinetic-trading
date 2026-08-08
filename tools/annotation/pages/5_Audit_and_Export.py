"""Page 5 — Audit and export."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app_config import load_ui_config
from pilot_context import PILOT_CONTEXT_KEY, PilotRunContext, clear_unsaved_form_state
from pilot_service import PilotService

st.set_page_config(page_title="Audit and Export", layout="wide")
st.title("Audit and Export")
st.caption(
    "Operational completion only. Live agreement/baseline metrics stay hidden "
    "until a batch is locked complete outside this UI."
)

config = load_ui_config()
if "service" not in st.session_state:
    st.session_state.service = PilotService(config)
service: PilotService = st.session_state.service

ctx_raw = st.session_state.get(PILOT_CONTEXT_KEY)
if ctx_raw:
    context = PilotRunContext.from_dict(ctx_raw)
    st.info(
        f"Selected run `{context.source_run_id}` · batch `{context.selected_batch_id}` · "
        f"guideline `{context.guideline_version}` · "
        f"corpus `{context.source_corpus_sha256[:12]}…`"
    )
    snap = service.audit_snapshot(source_run_id=context.source_run_id)
else:
    context = None
    st.warning("No pilot run selected — showing whole-database counts only.")
    snap = service.audit_snapshot()

st.subheader("Completion and readiness")
st.json(snap)

if st.button("Clear selected pilot context"):
    clear_unsaved_form_state(st.session_state)
    st.session_state.pop(PILOT_CONTEXT_KEY, None)
    st.rerun()

if context is None:
    st.stop()

export_root = config.local_export_root
dest_raw = st.text_input(
    "Raw annotation export destination",
    value=str(export_root / f"{context.source_run_id}_raw_annotations.jsonl"),
)
dest_dup = st.text_input(
    "Duplicate-review export destination",
    value=str(export_root / f"{context.source_run_id}_duplicate_reviews.jsonl"),
)
dest_adj = st.text_input(
    "Adjudication export destination",
    value=str(export_root / f"{context.source_run_id}_adjudications.jsonl"),
)

c1, c2, c3 = st.columns(3)
if c1.button("Export raw annotations", type="primary"):
    try:
        result = service.export_raw_annotations_jsonl(
            destination=Path(dest_raw),
            source_run_id=context.source_run_id,
            batch_id=str(context.selected_batch_id),
            guideline_version=context.guideline_version,
        )
        st.success(
            f"Exported {result['record_count']} records → {result['destination']} "
            f"(hash {result['content_hash'][:12]}…)"
        )
        st.json(result)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Export failed: {type(exc).__name__}: {exc}")

if c2.button("Export duplicate reviews"):
    try:
        result = service.export_duplicate_reviews_jsonl(
            destination=Path(dest_dup),
            source_run_id=context.source_run_id,
            batch_id=str(context.selected_batch_id),
        )
        st.success(f"Exported {result['record_count']} records → {result['destination']}")
        st.json(result)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Export failed: {type(exc).__name__}: {exc}")

if c3.button("Export adjudications"):
    try:
        result = service.export_adjudications_jsonl(
            destination=Path(dest_adj),
            source_run_id=context.source_run_id,
            batch_id=str(context.selected_batch_id),
        )
        st.success(f"Exported {result['record_count']} records → {result['destination']}")
        st.json(result)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Export failed: {type(exc).__name__}: {exc}")

st.caption(
    "Guideline version and sample roles are taken from stored assignments, "
    "not from free-text overwrite fields."
)

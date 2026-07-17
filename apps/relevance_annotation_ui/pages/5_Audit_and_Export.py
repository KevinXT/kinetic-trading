"""Page 5 — Audit and export."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app_config import load_ui_config
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

snap = service.audit_snapshot()
st.subheader("Completion")
st.json(snap)

dest = st.text_input(
    "Export destination",
    value=str(config.local_export_root / "raw_annotations.jsonl"),
)
guideline = st.text_input("Guideline version for export", value="pilot-guidelines-v1-calibration")

if st.button("Export raw annotations JSONL", type="primary"):
    try:
        result = service.export_raw_annotations_jsonl(
            destination=Path(dest),
            guideline_version=guideline,
        )
        st.success(
            f"Exported {result['record_count']} records → {result['destination']} "
            f"(hash {result['content_hash'][:12]}…)"
        )
        st.json(result)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Export failed: {type(exc).__name__}: {exc}")

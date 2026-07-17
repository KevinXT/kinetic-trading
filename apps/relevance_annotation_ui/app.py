"""Local semiconductor relevance annotation workstation (Streamlit).

Launch from the repository root:

    python3 -m streamlit run apps/relevance_annotation_ui/app.py

Local research workstation. Not a public application.
No model predictions. No trading controls.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app_config import UI_MODES, load_ui_config  # noqa: E402
from pilot_service import PilotService  # noqa: E402

st.set_page_config(
    page_title="Kinetic Relevance Annotation",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("Semiconductor relevance annotation workstation")
st.info(
    "Local research workstation · Not a public application · "
    "No model predictions · No trading controls"
)
st.caption(
    "Trusted local workstation. Mode selection is workflow separation, "
    "not a multi-tenant security boundary."
)

config = load_ui_config()
if "ui_mode" not in st.session_state:
    st.session_state.ui_mode = config.default_mode
if "annotator_id" not in st.session_state:
    st.session_state.annotator_id = "annotator_01"
if "service" not in st.session_state:
    st.session_state.service = PilotService(config)

service: PilotService = st.session_state.service

with st.sidebar:
    st.header("Local controls")
    mode = st.selectbox(
        "Mode",
        options=sorted(UI_MODES),
        index=(
            sorted(UI_MODES).index(st.session_state.ui_mode)
            if st.session_state.ui_mode in UI_MODES
            else 0
        ),
    )
    st.session_state.ui_mode = mode
    st.session_state.annotator_id = st.text_input("Actor ID", value=st.session_state.annotator_id)
    st.write(f"Database: `{config.database_path}`")
    st.write(f"Bind: `{config.bind_address}`")
    st.write(f"Schema version: {service.store.schema_version()}")

st.markdown("""
Use the pages in the left navigation:

1. **Corpus Preflight** — validate corpus/provenance and prepare batches  
2. **Article Annotation** — blind ordinal relevance labeling  
3. **Duplicate Review** — same-story pair review  
4. **Adjudication** — resolve disagreements without overwriting raw labels  
5. **Audit and Export** — completion and deterministic JSONL export  
""")

audit = service.audit_snapshot()
st.subheader("Operational snapshot")
st.json(audit["counts"])

"""Page 1 — Corpus preflight (no full article bodies)."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app_config import load_ui_config
from pilot_service import PilotService

st.set_page_config(page_title="Corpus Preflight", layout="wide")
st.title("Corpus Preflight")
st.caption("Validate rights-cleared inputs. Full article bodies are not shown here.")

config = load_ui_config()
if "service" not in st.session_state:
    st.session_state.service = PilotService(config)
service: PilotService = st.session_state.service

params = {}
try:
    params = service.load_pilot_params()
except FileNotFoundError as exc:
    st.error(str(exc))

default_articles = str(params.get("articles_path", config.local_corpus_root / "articles.jsonl"))
default_prov = str(params.get("provenance_path", config.local_corpus_root / "provenance.jsonl"))

articles_path = st.text_input("Corpus JSONL path", value=default_articles)
provenance_path = st.text_input("Provenance JSONL path", value=default_prov)
run_id = st.text_input("Run ID", value="ui_preflight_local")

col1, col2 = st.columns(2)
with col1:
    run = st.button("Run non-destructive preflight", type="primary")
with col2:
    st.write("Formal-pilot approval is not available on this page.")

if run:
    try:
        result = service.run_preflight(
            run_id=run_id,
            articles_path=articles_path,
            provenance_path=provenance_path,
        )
        st.session_state.last_preflight = result
        st.success(f"Preflight complete → {result['artifacts_dir']}")
    except Exception as exc:  # noqa: BLE001 — surface for local operator
        # Never include article bodies in error display.
        st.error(f"Preflight failed: {type(exc).__name__}: {exc}")

result = st.session_state.get("last_preflight")
if result:
    state = result.get("state") or {}
    st.subheader("Pipeline state")
    st.code(state.get("pilot_state", "UNKNOWN"))
    if state.get("missing_requirements"):
        st.warning("Missing requirements: " + ", ".join(state["missing_requirements"]))

    flow = result.get("exclusion_summary") or {}
    plan = result.get("sample_plan") or {}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Schema valid", flow.get("schema_valid", "—"))
    c2.metric("Rights eligible", flow.get("rights_eligible", "—"))
    c3.metric("Corpus eligible", flow.get("corpus_eligible", "—"))
    c4.metric("Sampling frame", flow.get("sampling_frame_units", "—"))

    st.subheader("Sample-size plan")
    st.write(
        {
            "operational_target_representative": plan.get("operational_target_representative"),
            "challenge_target_count": plan.get("challenge_target_count"),
            "planned_calibration_round_size": plan.get("planned_calibration_round_size"),
            "underfilled": plan.get("underfilled"),
            "warnings": plan.get("warnings"),
        }
    )
    if plan.get("underfilled"):
        st.error("Underfilled population: planned target was not quietly reduced.")

    st.subheader("Reproducibility")
    st.json(result.get("reproducibility") or {})

    batch = Path(result["artifacts_dir"]) / "calibration_annotation_batch.csv"
    if batch.is_file():
        if st.button("Import calibration assignments into SQLite"):
            n = service.import_calibration_assignments_from_batch(
                batch_csv_path=batch,
                annotator_ids=["annotator_a", "annotator_b"],
                batch_id=f"calib_{run_id}",
                guideline_version="pilot-guidelines-v1-calibration",
            )
            st.success(f"Imported {n} assignments")

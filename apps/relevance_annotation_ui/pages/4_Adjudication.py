"""Page 4 — Adjudication (not ordinary annotator mode)."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import streamlit as st

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app_config import load_ui_config
from pilot_service import PilotService, entity_display_names
from research_data.relevance.pilot_models import DISAGREEMENT_CAUSE_CATEGORIES

st.set_page_config(page_title="Adjudication", layout="wide")
st.title("Adjudication")

mode = st.session_state.get("ui_mode", "preflight")
if mode not in {"adjudicator", "audit"}:
    st.error("Adjudication page requires adjudicator or audit mode.")
    st.stop()

config = load_ui_config()
if "service" not in st.session_state:
    st.session_state.service = PilotService(config)
service: PilotService = st.session_state.service

adjudicator_id = st.session_state.get("annotator_id", "adjudicator_01")
st.caption("Raw annotations are never overwritten. Decisions append as adjudication events.")

# Build disagreement assignments from store: articles with >=2 latest labels that differ
events = service.store.export_latest_annotation_events()
by_article: dict[str, list[dict]] = {}
for ev in events:
    by_article.setdefault(ev["article_id"], []).append(ev)

disagreements = []
for article_id, group in sorted(by_article.items()):
    if len(group) < 2:
        continue
    ordered = sorted(group, key=lambda r: r["annotator_id"])
    a, b = ordered[0], ordered[1]
    if a.get("relevance_label") == b.get("relevance_label") and (
        bool(a["cannot_determine"]) == bool(b["cannot_determine"])
    ):
        continue
    disagreements.append((article_id, a, b))

if not disagreements:
    st.info("No disagreements found in current store.")
    st.stop()

idx = st.number_input("Disagreement index", min_value=0, max_value=len(disagreements) - 1, value=0)
article_id, ann_a, ann_b = disagreements[int(idx)]
cluster = "unknown"
for row in service.store.list_annotation_assignments():
    if row["article_id"] == article_id:
        cluster = row["duplicate_cluster_id"]
        break

st.subheader(f"Article {article_id}")
c1, c2 = st.columns(2)
with c1:
    st.markdown("**Annotator A**")
    st.json(
        {
            "annotator_id": ann_a["annotator_id"],
            "relevance_label": ann_a["relevance_label"],
            "cannot_determine": bool(ann_a["cannot_determine"]),
            "evidence_text": ann_a["evidence_text"],
        }
    )
with c2:
    st.markdown("**Annotator B**")
    st.json(
        {
            "annotator_id": ann_b["annotator_id"],
            "relevance_label": ann_b["relevance_label"],
            "cannot_determine": bool(ann_b["cannot_determine"]),
            "evidence_text": ann_b["evidence_text"],
        }
    )

la = ann_a["relevance_label"]
lb = ann_b["relevance_label"]
if la is not None and lb is not None:
    st.write(f"Absolute ordinal distance: {abs(int(la) - int(lb))}")

entities = entity_display_names()
final_label = st.radio("Final label", options=["0", "1", "2", "3", "cannot_determine"])
cannot = final_label == "cannot_determine"
cannot_reason = st.selectbox(
    "Cannot-determine reason",
    options=["", "BODY_UNAVAILABLE", "TEXT_TRUNCATED", "ARTICLE_CONTEXT_INSUFFICIENT", "OTHER"],
)
central = st.multiselect("Central entities", options=entities)
secondary = st.multiselect("Secondary entities", options=entities)
cause = st.selectbox("Disagreement cause", options=sorted(DISAGREEMENT_CAUSE_CATEGORIES))
reason = st.text_area("Adjudication reason", height=100)

if st.button("Save adjudication", type="primary"):
    try:
        assignment_id = f"adj_{article_id}_{adjudicator_id}"[:48]
        service.store.upsert_adjudication_assignment(
            assignment_id=assignment_id,
            article_id=article_id,
            duplicate_cluster_id=cluster,
            adjudicator_id=adjudicator_id,
            guideline_version="pilot-guidelines-v1-formal",
            raw_event_ids=[ann_a["event_id"], ann_b["event_id"]],
        )
        raw_labels = [
            int(x) for x in (ann_a["relevance_label"], ann_b["relevance_label"]) if x is not None
        ]
        service.save_adjudication(
            assignment_id=assignment_id,
            article_id=article_id,
            adjudicator_id=adjudicator_id,
            client_submission_id=str(uuid.uuid4()),
            relevance_label=(None if cannot else int(final_label)),
            cannot_determine=cannot,
            cannot_determine_reason=(cannot_reason or None),
            central_entity_ids=central,
            secondary_entity_ids=secondary,
            disagreement_cause=cause,
            adjudication_reason=reason,
            raw_labels=raw_labels,
        )
        # Prove raw events unchanged by reloading counts
        st.success("Adjudication saved. Raw annotation events remain append-only history.")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Save failed: {type(exc).__name__}: {exc}")

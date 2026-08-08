"""Page 4 — Adjudication (not ordinary annotator mode)."""

from __future__ import annotations

import json
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

from kinetic.ml.relevance.pilot_models import DISAGREEMENT_CAUSE_CATEGORIES

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

ctx_raw = st.session_state.get(PILOT_CONTEXT_KEY)
if not ctx_raw:
    st.error("No pilot run selected. Load a bound context on Preflight / Audit first.")
    st.stop()
context = PilotRunContext.from_dict(ctx_raw)
st.info(
    f"Run `{context.source_run_id}` · batch `{context.selected_batch_id}` · "
    f"guideline `{context.guideline_version}`"
)

adjudicator_id = st.session_state.get("annotator_id", "adjudicator_01")
st.caption("Raw annotations are never overwritten. Decisions append as adjudication events.")

events = service.store.export_latest_annotation_events(
    source_run_id=context.source_run_id,
    batch_id=context.selected_batch_id,
    guideline_version=context.guideline_version,
)
by_article: dict[str, list[dict]] = {}
for ev in events:
    by_article.setdefault(ev["article_id"], []).append(ev)

disagreements = []
for article_id, group in sorted(by_article.items()):
    if len(group) < 2:
        continue
    # Require explicit pairs by annotator; do not infer from list ordering alone
    # when more than two annotations exist — operator selects event IDs below.
    ordered = sorted(group, key=lambda r: r["annotator_id"])
    if len(ordered) == 2:
        a, b = ordered[0], ordered[1]
        if a.get("relevance_label") == b.get("relevance_label") and (
            bool(a["cannot_determine"]) == bool(b["cannot_determine"])
        ):
            continue
        disagreements.append((article_id, a, b, ordered))
    else:
        disagreements.append((article_id, None, None, ordered))

if not disagreements:
    st.info("No disagreements found for the selected pilot batch.")
    st.stop()

idx = st.number_input("Disagreement index", min_value=0, max_value=len(disagreements) - 1, value=0)
article_id, ann_a, ann_b, all_events = disagreements[int(idx)]

event_options = {
    f"{e['event_id']} · {e['annotator_id']} · label={e['relevance_label']}": e["event_id"]
    for e in all_events
}
default_a = ann_a["event_id"] if ann_a else list(event_options.values())[0]
default_b = ann_b["event_id"] if ann_b else list(event_options.values())[1]
sel_a = st.selectbox(
    "Raw event A (explicit)",
    options=list(event_options.keys()),
    index=list(event_options.values()).index(default_a),
)
sel_b = st.selectbox(
    "Raw event B (explicit)",
    options=list(event_options.keys()),
    index=list(event_options.values()).index(default_b),
)
raw_a_id = event_options[sel_a]
raw_b_id = event_options[sel_b]
ann_a = next(e for e in all_events if e["event_id"] == raw_a_id)
ann_b = next(e for e in all_events if e["event_id"] == raw_b_id)

cluster = str(ann_a.get("assignment_cluster_id") or "unknown")
batch_id = str(ann_a.get("assignment_batch_id") or context.selected_batch_id)
guideline_version = str(ann_a.get("assignment_guideline_version") or context.guideline_version)

# Bound article for adjudicator
assignment_probe = {
    "article_id": article_id,
    "source_run_id": context.source_run_id,
    "article_title_sha256": ann_a.get("assignment_corpus_sha256"),  # placeholder
}
# Prefer assignment row hashes.
for row in service.store.list_annotation_assignments(
    source_run_id=context.source_run_id, batch_id=batch_id
):
    if row["article_id"] == article_id:
        assignment_probe = row
        cluster = row["duplicate_cluster_id"]
        break

try:
    article = service.load_bound_article(assignment=assignment_probe, context=context)
except SourceArtifactMismatch as exc:
    st.error(str(exc))
    st.stop()

st.subheader(f"Article {article_id}")
st.markdown(f"**Title.** {article.title}")
st.markdown(f"**Source/domain.** {article.domain}")
st.markdown(
    f"**Published.** "
    f"{article.published_at.isoformat() if article.published_at else '—'} "
    f"({article.published_at_source or 'unknown'})"
)
st.markdown(f"**Content status.** {article.content_status}")
if article.content_status != "available":
    st.warning("Content may be incomplete.")
st.markdown(f"**Guideline version.** {guideline_version}")
st.markdown("**Description**")
st.text(article.description or "")
st.markdown("**Body**")
st.text(article.body or "")

c1, c2 = st.columns(2)
with c1:
    st.markdown("**Annotator A (latest raw)**")
    st.json(
        {
            "event_id": ann_a["event_id"],
            "annotator_id": ann_a["annotator_id"],
            "relevance_label": ann_a["relevance_label"],
            "cannot_determine": bool(ann_a["cannot_determine"]),
            "cannot_determine_reason": ann_a["cannot_determine_reason"],
            "central_entity_ids": json.loads(ann_a["central_entity_ids"]),
            "secondary_entity_ids": json.loads(ann_a["secondary_entity_ids"]),
            "evidence_text": ann_a["evidence_text"],
            "uncertain": bool(ann_a["uncertain"]),
            "decision_reason_code": ann_a["decision_reason_code"],
        }
    )
with c2:
    st.markdown("**Annotator B (latest raw)**")
    st.json(
        {
            "event_id": ann_b["event_id"],
            "annotator_id": ann_b["annotator_id"],
            "relevance_label": ann_b["relevance_label"],
            "cannot_determine": bool(ann_b["cannot_determine"]),
            "cannot_determine_reason": ann_b["cannot_determine_reason"],
            "central_entity_ids": json.loads(ann_b["central_entity_ids"]),
            "secondary_entity_ids": json.loads(ann_b["secondary_entity_ids"]),
            "evidence_text": ann_b["evidence_text"],
            "uncertain": bool(ann_b["uncertain"]),
            "decision_reason_code": ann_b["decision_reason_code"],
        }
    )

la = ann_a["relevance_label"]
lb = ann_b["relevance_label"]
if la is not None and lb is not None:
    st.write(f"Absolute ordinal distance: {abs(int(la) - int(lb))}")
cent_a = set(json.loads(ann_a["central_entity_ids"]))
cent_b = set(json.loads(ann_b["central_entity_ids"]))
st.write(f"Central entity set difference: {sorted(cent_a ^ cent_b)}")
st.write(
    "Cannot-determine difference: "
    f"{bool(ann_a['cannot_determine'])} vs {bool(ann_b['cannot_determine'])}"
)

options = {o.label(): o.entity_id for o in service.entity_options()}
final_label = st.radio("Final label", options=["0", "1", "2", "3", "cannot_determine"])
cannot = final_label == "cannot_determine"
cannot_reason = st.selectbox(
    "Cannot-determine reason",
    options=["", "BODY_UNAVAILABLE", "TEXT_TRUNCATED", "ARTICLE_CONTEXT_INSUFFICIENT", "OTHER"],
)
central = st.multiselect("Central entities", options=list(options.keys()))
secondary = st.multiselect("Secondary entities", options=list(options.keys()))
cause = st.selectbox("Disagreement cause", options=sorted(DISAGREEMENT_CAUSE_CATEGORIES))
reason = st.text_area("Adjudication reason", height=100)

if st.button("Save adjudication", type="primary"):
    try:
        assignment_id = f"adj_{article_id}_{adjudicator_id}_{context.source_run_id}"[:64]
        service.store.upsert_adjudication_assignment(
            assignment_id=assignment_id,
            article_id=article_id,
            duplicate_cluster_id=cluster,
            adjudicator_id=adjudicator_id,
            guideline_version=guideline_version,
            raw_event_ids=[raw_a_id, raw_b_id],
            batch_id=batch_id,
            source_run_id=context.source_run_id,
            source_task_name=context.source_task_name,
            source_artifact_root=context.source_artifact_root,
            source_assignment_manifest_sha256=context.source_assignment_manifest_sha256,
            source_corpus_sha256=context.source_corpus_sha256,
            source_article_records_sha256=context.source_article_records_sha256,
            article_title_sha256=assignment_probe.get("article_title_sha256"),
            article_body_sha256=assignment_probe.get("article_body_sha256"),
            article_normalization_version=assignment_probe.get("article_normalization_version"),
            assignment_schema_version=ASSIGNMENT_SCHEMA_VERSION,
            validate_raw=True,
        )
        raw_labels = [
            int(x) for x in (ann_a["relevance_label"], ann_b["relevance_label"]) if x is not None
        ]
        fp = form_fingerprint(
            [
                assignment_id,
                adjudicator_id,
                final_label,
                cannot_reason,
                tuple(central),
                tuple(secondary),
                cause,
                reason,
                raw_a_id,
                raw_b_id,
            ]
        )
        client_id = get_or_create_submission_token(
            st.session_state,
            scope=f"adj|{assignment_id}|{adjudicator_id}",
            form_fingerprint=fp,
        )
        service.save_adjudication(
            assignment_id=assignment_id,
            article_id=article_id,
            adjudicator_id=adjudicator_id,
            client_submission_id=client_id,
            relevance_label=(None if cannot else int(final_label)),
            cannot_determine=cannot,
            cannot_determine_reason=(cannot_reason or None),
            central_entity_ids=[options[x] for x in central],
            secondary_entity_ids=[options[x] for x in secondary],
            disagreement_cause=cause,
            adjudication_reason=reason,
            raw_labels=raw_labels,
            raw_event_ids=[raw_a_id, raw_b_id],
        )
        rotate_submission_token(st.session_state, scope=f"adj|{assignment_id}|{adjudicator_id}")
        st.success("Adjudication saved. Raw annotation events remain append-only history.")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Save failed: {type(exc).__name__}: {exc}")

"""Page 2 — Blind article annotation."""

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

st.set_page_config(page_title="Article Annotation", layout="wide")
st.title("Article Annotation")
st.caption("Blind labeling. Baselines, challenge reasons, and other labels are hidden.")

config = load_ui_config()
if "service" not in st.session_state:
    st.session_state.service = PilotService(config)
service: PilotService = st.session_state.service

annotator_id = st.session_state.get("annotator_id", "annotator_01")
batch_id = st.text_input("Batch ID filter (optional)", value="")
articles_path = st.text_input(
    "Articles JSONL for display",
    value="tests/fixtures/research/relevance_pilot/articles.jsonl",
)

assignments = service.store.list_annotation_assignments(
    annotator_id=annotator_id, batch_id=(batch_id or None)
)
if not assignments:
    st.warning("No assignments for this annotator/batch. Import from Preflight first.")
    st.stop()

if "ann_index" not in st.session_state:
    st.session_state.ann_index = 0
idx = max(0, min(st.session_state.ann_index, len(assignments) - 1))
assignment = assignments[idx]

articles = service.load_articles_index(Path(articles_path))
article = articles.get(assignment["article_id"])
if article is None:
    st.error("Article missing from corpus index.")
    st.stop()

view = service.build_blind_article_view(
    assignment=assignment,
    article=article,
    queue_index=idx + 1,
    queue_total=len(assignments),
    entity_names=entity_display_names(),
)

st.subheader(
    f"Article {view.queue_index} of {view.queue_total} · Annotator: {view.annotator_id} · "
    f"Guideline: {view.guideline_version} · Saved revision: {view.saved_revision}"
)
if view.last_saved_at:
    st.caption(f"Last saved: {view.last_saved_at}")

st.markdown(f"**Title.** {view.title}")
st.markdown(f"**Source/domain.** {view.domain}")
st.markdown(f"**Published.** {view.published_at or '—'} ({view.published_at_precision})")
st.markdown(f"**Language.** {view.language or '—'}")
st.markdown(f"**Content status.** {view.content_status}")
if view.content_status != "available":
    st.warning("Content may be incomplete.")
if view.description:
    st.markdown("**Description**")
    st.text(view.description)
st.markdown("**Body**")
if view.body_truncated:
    st.warning("Displayed body is truncated or redacted by configuration.")
st.text(view.body)
st.markdown("**Entity references (protocol)**")
st.write(", ".join(view.entity_reference_names))

st.divider()
label_choice = st.radio(
    "Relevance",
    options=[
        "0 — Unrelated",
        "1 — Incidental mention",
        "2 — Meaningful secondary topic",
        "3 — Primary topic",
    ],
)
cannot = st.checkbox("Cannot determine")
cannot_reason = st.selectbox(
    "Cannot-determine reason",
    options=[
        "",
        "BODY_UNAVAILABLE",
        "TEXT_TRUNCATED",
        "LANGUAGE_UNSUPPORTED",
        "ENTITY_REFERENCE_UNCLEAR",
        "ARTICLE_CONTEXT_INSUFFICIENT",
        "CORRUPTED_TEXT",
        "OTHER",
    ],
)
central = st.multiselect("Central companies", options=list(view.entity_reference_names))
secondary = st.multiselect("Secondary companies", options=list(view.entity_reference_names))
content_ok = st.checkbox("Content sufficient", value=True)
uncertain = st.checkbox("Uncertain")
uncertainty_reason = st.text_input("Uncertainty reason")
decision_code = st.text_input("Decision reason code", value="MANUAL")
evidence = st.text_area("Evidence excerpt (exact paste)", height=80)
notes = st.text_area("Notes", height=60)
occurrence = st.text_input("Evidence occurrence selector (field@offset) if needed")

nav1, nav2, nav3, nav4 = st.columns(4)
save = nav1.button("Save")
save_next = nav2.button("Save and next", type="primary")
prev = nav3.button("Previous")
nxt = nav4.button("Next incomplete")


def _submit() -> None:
    label = None if cannot else int(label_choice[0])
    client_id = str(uuid.uuid4())
    service.validate_and_save_annotation(
        assignment_id=view.assignment_id,
        article=article,
        annotator_id=annotator_id,
        client_submission_id=client_id,
        relevance_label=label,
        cannot_determine=cannot,
        cannot_determine_reason=(cannot_reason or None),
        central_entity_ids=central,
        secondary_entity_ids=secondary,
        evidence_excerpt=(evidence or None),
        content_sufficient=content_ok,
        uncertain=uncertain,
        uncertainty_reason=(uncertainty_reason or None),
        decision_reason_code=decision_code,
        notes=(notes or None),
        chosen_evidence_occurrence=(occurrence or None),
    )


if save or save_next:
    try:
        _submit()
        st.success("Saved.")
        if save_next:
            st.session_state.ann_index = min(idx + 1, len(assignments) - 1)
            st.rerun()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Save failed: {type(exc).__name__}: {exc}")

if prev:
    st.session_state.ann_index = max(0, idx - 1)
    st.rerun()
if nxt:
    for j in range(idx + 1, len(assignments)):
        if service.store.current_annotation_state(assignments[j]["assignment_id"]) is None:
            st.session_state.ann_index = j
            st.rerun()
    st.info("No incomplete assignments after current item.")

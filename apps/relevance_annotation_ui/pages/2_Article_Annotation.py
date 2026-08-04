"""Page 2 — Blind article annotation."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app_config import load_ui_config
from pilot_context import (
    PILOT_CONTEXT_KEY,
    PilotRunContext,
    SourceArtifactMismatch,
    form_fingerprint,
    get_or_create_submission_token,
    rotate_submission_token,
)
from pilot_service import PilotService
from view_models import article_content_hash, generate_evidence_candidates

st.set_page_config(page_title="Article Annotation", layout="wide")
st.title("Article Annotation")
st.caption("Blind labeling. Baselines, challenge reasons, and other labels are hidden.")

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
    f"guideline `{context.guideline_version}` · corpus `{context.source_corpus_sha256[:12]}…`"
)

annotator_id = st.session_state.get("annotator_id", "annotator_01")
assignments = service.store.list_annotation_assignments(
    annotator_id=annotator_id,
    batch_id=context.selected_batch_id,
    source_run_id=context.source_run_id,
)
if not assignments:
    st.warning("No bound assignments for this annotator/batch/run.")
    st.stop()

if "ann_index" not in st.session_state:
    st.session_state.ann_index = 0
idx = max(0, min(st.session_state.ann_index, len(assignments) - 1))
assignment = assignments[idx]

try:
    article = service.load_bound_article(assignment=assignment, context=context)
except SourceArtifactMismatch as exc:
    st.error(str(exc))
    st.stop()

view = service.build_blind_article_view(
    assignment=assignment,
    article=article,
    queue_index=idx + 1,
    queue_total=len(assignments),
    entity_options=service.entity_options(),
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
option_labels = {o.label(): o.entity_id for o in view.entity_options}
st.write(", ".join(option_labels.keys()))

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
central_labels = st.multiselect("Central companies", options=list(option_labels.keys()))
secondary_labels = st.multiselect("Secondary companies", options=list(option_labels.keys()))
content_ok = st.checkbox("Content sufficient", value=True)
uncertain = st.checkbox("Uncertain")
uncertainty_reason = st.text_input("Uncertainty reason")
decision_code = st.text_input("Decision reason code", value="MANUAL")
evidence = st.text_area("Evidence excerpt (exact paste)", height=80)
no_span_reason = st.selectbox(
    "No-exact-span reason (only if excerpt is not an exact substring)",
    options=["", "PARAPHRASED_EVIDENCE", "NO_SINGLE_EXACT_SPAN", "METADATA_ONLY", "OTHER"],
)
notes = st.text_area("Notes", height=60)

article_hash = article_content_hash(
    title=article.title,
    description=article.description or "",
    body=article.body or "",
    title_sha256=article.title_sha256,
    body_sha256=article.body_sha256,
)
candidates = []
chosen_candidate = None
if evidence and evidence.strip():
    candidates = generate_evidence_candidates(
        excerpt=evidence,
        title=article.title,
        description=article.description or "",
        body=article.body or "",
        article_hash=article_hash,
    )
    if len(candidates) > 1:
        cand_options = {
            f"{c.candidate_id} · {c.field}@{c.start}": c.candidate_id for c in candidates
        }
        chosen_label = st.selectbox(
            "Evidence occurrence (generated candidates)", options=list(cand_options)
        )
        chosen_candidate = cand_options[chosen_label]
    elif len(candidates) == 1:
        chosen_candidate = candidates[0].candidate_id
        st.caption(f"Unique evidence match: `{chosen_candidate}`")

nav1, nav2, nav3, nav4 = st.columns(4)
save = nav1.button("Save")
save_next = nav2.button("Save and next", type="primary")
prev = nav3.button("Previous")
nxt = nav4.button("Next incomplete")


def _submit() -> None:
    label = None if cannot else int(label_choice[0])
    fp = form_fingerprint(
        [
            view.assignment_id,
            annotator_id,
            label,
            cannot,
            cannot_reason,
            tuple(central_labels),
            tuple(secondary_labels),
            evidence,
            chosen_candidate,
            no_span_reason,
            content_ok,
            uncertain,
            uncertainty_reason,
            decision_code,
            notes,
            view.saved_revision,
        ]
    )
    client_id = get_or_create_submission_token(
        st.session_state,
        scope=f"annotation|{view.assignment_id}|{annotator_id}",
        form_fingerprint=fp,
    )
    service.validate_and_save_annotation(
        assignment_id=view.assignment_id,
        article=article,
        annotator_id=annotator_id,
        client_submission_id=client_id,
        relevance_label=label,
        cannot_determine=cannot,
        cannot_determine_reason=(cannot_reason or None),
        central_entity_ids=[option_labels[x] for x in central_labels],
        secondary_entity_ids=[option_labels[x] for x in secondary_labels],
        evidence_excerpt=(evidence or None),
        content_sufficient=content_ok,
        uncertain=uncertain,
        uncertainty_reason=(uncertainty_reason or None),
        decision_reason_code=decision_code,
        notes=(notes or None),
        chosen_evidence_candidate_id=chosen_candidate,
        no_exact_span_reason=(no_span_reason or None),
        allow_no_exact_span=bool(no_span_reason),
    )
    rotate_submission_token(
        st.session_state,
        scope=f"annotation|{view.assignment_id}|{annotator_id}",
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

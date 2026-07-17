"""Page 3 — Blind duplicate-pair review."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import streamlit as st

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app_config import load_ui_config
from pilot_service import PilotService
from research_data.relevance.pilot_models import PAIR_LABELS

st.set_page_config(page_title="Duplicate Review", layout="wide")
st.title("Duplicate Review")
st.caption("Similarity scores and thresholds are hidden.")

config = load_ui_config()
if "service" not in st.session_state:
    st.session_state.service = PilotService(config)
service: PilotService = st.session_state.service

reviewer_id = st.session_state.get("annotator_id", "reviewer_01")
articles_path = st.text_input(
    "Articles JSONL",
    value="tests/fixtures/research/relevance_pilot/articles.jsonl",
)
pair_batch = st.text_input(
    "Duplicate pair review batch CSV",
    value="",
    help="Optional: path to duplicate_pair_review_batch.csv from a pilot run",
)

if pair_batch and Path(pair_batch).is_file() and st.button("Import pair assignments"):
    import csv

    rows = list(csv.DictReader(Path(pair_batch).open(encoding="utf-8")))
    for i, row in enumerate(rows):
        aid = f"dup_{row['pair_id']}_{reviewer_id}"[:48]
        service.store.upsert_duplicate_assignment(
            assignment_id=aid,
            pair_id=row["pair_id"],
            article_id_a=row["article_id_a"],
            article_id_b=row["article_id_b"],
            reviewer_id=reviewer_id,
            batch_id="ui_dup_batch",
            assignment_order=i + 1,
        )
    st.success(f"Imported {len(rows)} pair assignments")

with service.store.connect() as conn:
    assignments = [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM duplicate_review_assignments
            WHERE reviewer_id = ?
            ORDER BY assignment_order ASC
            """,
            (reviewer_id,),
        ).fetchall()
    ]

if not assignments:
    st.warning("No duplicate assignments. Import a pair-review batch first.")
    st.stop()

if "dup_index" not in st.session_state:
    st.session_state.dup_index = 0
idx = max(0, min(st.session_state.dup_index, len(assignments) - 1))
assignment = assignments[idx]
articles = service.load_articles_index(Path(articles_path))
a = articles[assignment["article_id_a"]]
b = articles[assignment["article_id_b"]]
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
        service.save_duplicate_review(
            assignment_id=view.assignment_id,
            pair_id=view.pair_id,
            reviewer_id=reviewer_id,
            client_submission_id=str(uuid.uuid4()),
            pair_label=pair_label,
            uncertain=uncertain,
            reason=reason,
        )
        st.success("Saved.")
        st.session_state.dup_index = min(idx + 1, len(assignments) - 1)
        st.rerun()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Save failed: {type(exc).__name__}: {exc}")

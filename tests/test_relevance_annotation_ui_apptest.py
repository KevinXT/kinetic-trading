"""Streamlit AppTest smoke for the local annotation workstation."""

from __future__ import annotations

from pathlib import Path

import pytest

streamlit = pytest.importorskip("streamlit")
try:
    from streamlit.testing.v1 import AppTest
except Exception:  # pragma: no cover
    pytest.skip("Streamlit AppTest unavailable", allow_module_level=True)


APP = Path("apps/relevance_annotation_ui/app.py")
PREFLIGHT = Path("apps/relevance_annotation_ui/pages/1_Corpus_Preflight.py")


def test_app_starts_and_shows_local_banner() -> None:
    at = AppTest.from_file(str(APP), default_timeout=15)
    at.run()
    assert not at.exception
    text_blob = " ".join(str(x.value) for x in at.info) + " ".join(
        str(x.value) for x in at.markdown
    )
    assert "Local research workstation" in text_blob or any(
        "Local research workstation" in str(el.value) for el in at.info
    )
    assert "No model predictions" in text_blob or any(
        "No model predictions" in str(el.value) for el in at.info
    )
    forbidden = ("baseline_prediction", "challenge_reason", "design_weight", "abnormal_return")
    for token in forbidden:
        assert token not in text_blob


def test_preflight_page_renders() -> None:
    at = AppTest.from_file(str(PREFLIGHT), default_timeout=15)
    at.run()
    assert not at.exception
    titles = [str(t.value) for t in at.title]
    assert any("Preflight" in t for t in titles)

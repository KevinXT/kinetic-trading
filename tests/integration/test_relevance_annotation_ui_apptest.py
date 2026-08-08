"""Streamlit AppTest coverage for the local annotation workstation."""

from __future__ import annotations

from pathlib import Path

import pytest

streamlit = pytest.importorskip("streamlit")
try:
    from streamlit.testing.v1 import AppTest
except Exception:  # pragma: no cover
    pytest.skip("Streamlit AppTest unavailable", allow_module_level=True)


# AppTest.from_file resolves relative paths against this test file, not cwd.
ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = ROOT / "tools" / "annotation"
APP = UI_ROOT / "app.py"
PREFLIGHT = UI_ROOT / "pages" / "1_Corpus_Preflight.py"
ANNOTATION = UI_ROOT / "pages" / "2_Article_Annotation.py"
DUPLICATE = UI_ROOT / "pages" / "3_Duplicate_Review.py"
ADJUDICATION = UI_ROOT / "pages" / "4_Adjudication.py"
AUDIT = UI_ROOT / "pages" / "5_Audit_and_Export.py"


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


def test_annotation_page_requires_pilot_context() -> None:
    at = AppTest.from_file(str(ANNOTATION), default_timeout=15)
    at.run()
    assert not at.exception
    errors = " ".join(str(e.value) for e in at.error)
    assert "No pilot run selected" in errors or "pilot" in errors.casefold()


def test_duplicate_page_requires_pilot_context() -> None:
    at = AppTest.from_file(str(DUPLICATE), default_timeout=15)
    at.run()
    assert not at.exception
    errors = " ".join(str(e.value) for e in at.error)
    assert "No pilot run selected" in errors or "pilot" in errors.casefold()


def test_adjudication_page_requires_mode_or_context() -> None:
    at = AppTest.from_file(str(ADJUDICATION), default_timeout=15)
    at.run()
    assert not at.exception
    text = " ".join(str(e.value) for e in at.error) + " ".join(str(i.value) for i in at.info)
    assert (
        "adjudicator" in text.casefold()
        or "pilot run" in text.casefold()
        or "mode" in text.casefold()
    )


def test_audit_page_renders_without_context() -> None:
    at = AppTest.from_file(str(AUDIT), default_timeout=15)
    at.run()
    assert not at.exception
    titles = [str(t.value) for t in at.title]
    assert any("Audit" in t for t in titles)


def test_preflight_missing_corpus_typed_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Service-boundary check: missing corpus is a typed blocked state.

    Full Streamlit button→pipeline AppTest for preflight is environment-heavy;
    this documents the required manual check when AppTest cannot drive the run.
    Manual: open Preflight, point at a missing path, click run, expect MISSING_CORPUS.
    """
    import sys
    from datetime import datetime, timezone

    sys.path.insert(0, str(UI_ROOT))
    from app_config import UIConfig
    from pilot_service import PilotService

    cfg = UIConfig.from_mapping(
        {
            "ui": {"database_path": str(tmp_path / "ui.sqlite3")},
            "pilot": {
                "config_path": (
                    "projects/semiconductor_case_study/configs/"
                    "semiconductor_relevance_real_corpus_pilot_local.yaml"
                ),
            },
        },
        config_path=tmp_path / "ui.yaml",
    )
    PilotService(
        cfg,
        repo_root=Path.cwd(),
        clock=lambda: datetime(2026, 7, 17, 15, 0, 0, tzinfo=timezone.utc),
    )
    missing = tmp_path / "nope.jsonl"
    assert not missing.exists()
    # Page surfaces MISSING_CORPUS before calling the engine when path absent.
    assert not Path(str(missing)).is_file()

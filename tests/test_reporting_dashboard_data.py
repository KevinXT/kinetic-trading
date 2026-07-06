"""
Tests for the reporting dashboard sample data.

These keep the committed dashboard sample JSON honest: the files must exist, be
non-empty JSON arrays of row objects, and expose exactly the output-column
aliases of the corresponding BigQuery reporting view. They are intentionally
lenient (extra keys are allowed) so they are not brittle.
"""

import json
from pathlib import Path

import pytest
from news_data.reporting.views import get_view

# Repo root: tests/ -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SAMPLE_DIR = _REPO_ROOT / "apps" / "reporting_dashboard" / "public" / "sample_data"

# Dashboard sample file -> reporting view name (view.name doubles as filename).
_VIEW_NAMES = [
    "daily_event_volume",
    "top_sources",
    "top_themes_or_entities",
    "data_quality_summary",
]


def _load(view_name: str):
    path = _SAMPLE_DIR / f"{view_name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("view_name", _VIEW_NAMES)
def test_sample_file_exists(view_name: str) -> None:
    assert (_SAMPLE_DIR / f"{view_name}.json").is_file()


@pytest.mark.parametrize("view_name", _VIEW_NAMES)
def test_sample_file_is_non_empty_json_array(view_name: str) -> None:
    rows = _load(view_name)
    assert isinstance(rows, list)
    assert len(rows) >= 1
    assert all(isinstance(r, dict) for r in rows)


@pytest.mark.parametrize("view_name", _VIEW_NAMES)
def test_sample_columns_match_view_aliases(view_name: str) -> None:
    view = get_view(view_name)
    rows = _load(view_name)
    required = set(view.required_columns)
    for row in rows:
        missing = required - set(row.keys())
        assert not missing, f"{view_name} row missing columns: {sorted(missing)}"


def test_daily_event_volume_shape() -> None:
    rows = _load("daily_event_volume")
    for row in rows:
        assert isinstance(row["record_count"], int)
        # event_date looks like YYYY-MM-DD.
        assert len(str(row["event_date"]).split("-")) == 3


def test_data_quality_summary_is_single_row() -> None:
    rows = _load("data_quality_summary")
    assert len(rows) == 1
    row = rows[0]
    assert row["total_rows"] > 0
    assert row["missing_required_field_count"] >= 0
    assert row["duplicate_count"] >= 0


def test_top_lists_are_descending_by_count() -> None:
    for view_name, value_key in (
        ("top_sources", "record_count"),
        ("top_themes_or_entities", "record_count"),
    ):
        counts = [r[value_key] for r in _load(view_name)]
        assert counts == sorted(counts, reverse=True), f"{view_name} not sorted desc"

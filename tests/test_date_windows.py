"""Tests for minimal date-window resolution."""

from datetime import date

import pytest
from common.date_windows import (
    resolve_date_window,
    to_gdelt_gkg_end_datetime_int,
    to_gdelt_gkg_start_datetime_int,
    to_yyyymmdd,
)

TODAY = date(2026, 5, 30)


def test_last_30_days_inclusive() -> None:
    start, end = resolve_date_window({"preset": "last_30_days"}, today=TODAY)
    assert end == TODAY
    assert start == date(2026, 5, 1)  # 30 inclusive days


def test_last_1_day_is_today_only() -> None:
    start, end = resolve_date_window({"preset": "last_1_day"}, today=TODAY)
    assert start == TODAY
    assert end == TODAY


def test_last_1_year() -> None:
    start, end = resolve_date_window({"preset": "last_1_year"}, today=TODAY)
    assert end == TODAY
    assert (end - start).days == 364


def test_explicit_start_end() -> None:
    start, end = resolve_date_window({"start": "2026-01-01", "end": "2026-01-31"}, today=TODAY)
    assert start == date(2026, 1, 1)
    assert end == date(2026, 1, 31)


def test_explicit_yyyymmdd() -> None:
    start, end = resolve_date_window({"start": "20260101", "end": "20260102"})
    assert start == date(2026, 1, 1)
    assert end == date(2026, 1, 2)


def test_default_preset_when_empty() -> None:
    start, end = resolve_date_window({}, today=TODAY)
    assert start == date(2026, 5, 1)


def test_unknown_preset_raises() -> None:
    with pytest.raises(ValueError, match="preset"):
        resolve_date_window({"preset": "last_decade"}, today=TODAY)


def test_partial_explicit_raises() -> None:
    with pytest.raises(ValueError, match="both"):
        resolve_date_window({"start": "2026-01-01"}, today=TODAY)


def test_inverted_explicit_range_raises() -> None:
    with pytest.raises(ValueError, match="after"):
        resolve_date_window({"start": "2026-05-30", "end": "2026-05-01"})


def test_to_yyyymmdd() -> None:
    assert to_yyyymmdd(date(2026, 5, 3)) == "20260503"


# ── GKG 14-digit datetime helpers (YYYYMMDDHHMMSS) ───────────────────────────


def test_gkg_start_datetime_is_midnight() -> None:
    assert to_gdelt_gkg_start_datetime_int(date(2026, 5, 1)) == 20260501000000


def test_gkg_end_datetime_is_end_of_day() -> None:
    assert to_gdelt_gkg_end_datetime_int(date(2026, 5, 30)) == 20260530235959


def test_gkg_datetime_helpers_are_14_digits() -> None:
    assert len(str(to_gdelt_gkg_start_datetime_int(date(2026, 5, 1)))) == 14
    assert len(str(to_gdelt_gkg_end_datetime_int(date(2026, 5, 30)))) == 14


def test_gkg_datetime_month_boundary() -> None:
    assert to_gdelt_gkg_start_datetime_int(date(2026, 6, 1)) == 20260601000000
    assert to_gdelt_gkg_end_datetime_int(date(2026, 5, 31)) == 20260531235959


def test_gkg_datetime_year_boundary() -> None:
    assert to_gdelt_gkg_start_datetime_int(date(2026, 1, 1)) == 20260101000000
    assert to_gdelt_gkg_end_datetime_int(date(2025, 12, 31)) == 20251231235959


def test_gkg_datetime_differs_from_8_digit() -> None:
    # The GKG datetime int must NOT equal the 8-digit YYYYMMDD form.
    d = date(2026, 5, 1)
    assert to_gdelt_gkg_start_datetime_int(d) != int(to_yyyymmdd(d))

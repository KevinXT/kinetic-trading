"""
Minimal date-window resolution for time-bounded research queries.

Kept intentionally small (v0.1): enough to turn a config ``window`` block into a
concrete ``[start, end]`` date range for providers like BigQuery. Supports a few
named presets plus an explicit ``start`` / ``end`` override.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

JsonDict = Dict[str, Any]

# preset name -> number of days in the (inclusive) lookback window.
_PRESET_DAYS = {
    "last_1_day": 1,
    "last_7_days": 7,
    "last_14_days": 14,
    "last_30_days": 30,
    "last_90_days": 90,
    "last_180_days": 180,
    "last_1_year": 365,
}


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _parse_date(value: Any, *, field: str) -> date:
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("-", "")
    if len(text) == 8 and text.isdigit():
        return date(int(text[0:4]), int(text[4:6]), int(text[6:8]))
    raise ValueError(f"window.{field} must be YYYY-MM-DD or YYYYMMDD, got {value!r}.")


def resolve_date_window(
    window: Optional[JsonDict], *, today: Optional[date] = None
) -> Tuple[date, date]:
    """
    Resolve a config ``window`` block into ``(start_date, end_date)``.

    Accepted shapes::

        window: {preset: last_30_days}            # inclusive lookback ending today
        window: {start: 2026-05-01, end: 2026-05-30}

    An explicit ``start``/``end`` takes precedence over ``preset``. The window is
    inclusive on both ends (``last_30_days`` spans 30 calendar days ending today).

    Raises:
        ValueError: unknown preset, missing/invalid dates, or inverted range.
    """
    window = window or {}
    if not isinstance(window, dict):
        raise ValueError(f"window must be a mapping, got {type(window).__name__}.")

    today = today or _utc_today()

    start_raw = window.get("start") or window.get("start_date")
    end_raw = window.get("end") or window.get("end_date")
    if start_raw is not None or end_raw is not None:
        if start_raw is None or end_raw is None:
            raise ValueError("window with explicit dates requires both 'start' and 'end'.")
        start = _parse_date(start_raw, field="start")
        end = _parse_date(end_raw, field="end")
    else:
        preset = str(window.get("preset", "last_30_days")).strip()
        if preset not in _PRESET_DAYS:
            raise ValueError(
                f"unknown window preset {preset!r}; " f"supported: {sorted(_PRESET_DAYS)}."
            )
        days = _PRESET_DAYS[preset]
        end = today
        start = today - timedelta(days=days - 1)

    if start > end:
        raise ValueError(f"invalid window: start {start} is after end {end}.")
    return start, end


def to_yyyymmdd(d: date) -> str:
    """Format a date as a compact 8-digit ``YYYYMMDD`` string.

    This is for human-readable labels and partition timestamps — **not** for the
    GKG ``DATE`` column, which is a 14-digit ``YYYYMMDDHHMMSS`` integer (see the
    GKG datetime helpers below).
    """
    return d.strftime("%Y%m%d")


def to_gdelt_gkg_start_datetime_int(d: date) -> int:
    """
    Start-of-day GKG ``DATE`` value as a 14-digit ``YYYYMMDDHHMMSS`` int.

    The ``DATE`` column on ``gdelt-bq.gdeltv2.gkg_partitioned`` is a 14-digit
    datetime integer (e.g. ``20260501123456``), **not** an 8-digit ``YYYYMMDD``.
    Filtering it with 8 digits (``... BETWEEN 20260501 AND 20260530``) matches no
    rows. Use this for the inclusive lower bound: ``2026-05-01`` ->
    ``20260501000000``.
    """
    return int(d.strftime("%Y%m%d")) * 1_000_000


def to_gdelt_gkg_end_datetime_int(d: date) -> int:
    """
    End-of-day GKG ``DATE`` value as a 14-digit ``YYYYMMDDHHMMSS`` int.

    Inclusive upper bound covering the whole day: ``2026-05-30`` ->
    ``20260530235959``. See :func:`to_gdelt_gkg_start_datetime_int` for why GKG
    ``DATE`` filters must be 14 digits.
    """
    return int(d.strftime("%Y%m%d")) * 1_000_000 + 235959

"""Tests for alignment policies (news publication time -> target session)."""

from datetime import datetime, timezone

from research_data.alignment import (
    ConservativeCalendarDayPolicy,
    SessionInformationWindowPolicy,
)
from research_data.calendar import MarketCalendar

CAL = MarketCalendar()
SW = SessionInformationWindowPolicy(CAL)
CD = ConservativeCalendarDayPolicy(CAL)


def _utc(y, m, d, h, mi=0, sec=0) -> datetime:
    return datetime(y, m, d, h, mi, sec, tzinfo=timezone.utc)


def test_premarket_monday_assigned_to_monday() -> None:
    # Monday 08:00 ET = 12:00 UTC (EDT), before the 09:30 open.
    assert SW.assign_session(_utc(2026, 6, 15, 12, 0)).isoformat() == "2026-06-15"


def test_intraday_monday_assigned_to_next_session() -> None:
    # Monday 15:00 UTC = 11:00 ET, after the cutoff -> Tuesday.
    assert SW.assign_session(_utc(2026, 6, 15, 15, 0)).isoformat() == "2026-06-16"


def test_weekend_news_folds_into_monday() -> None:
    for ts in (_utc(2026, 6, 13, 16), _utc(2026, 6, 14, 20)):  # Sat, Sun
        assert SW.assign_session(ts).isoformat() == "2026-06-15"


def test_friday_after_close_folds_into_monday() -> None:
    # Friday 18:00 ET = 22:00 UTC, after Friday cutoff -> Monday.
    assert SW.assign_session(_utc(2026, 6, 12, 22)).isoformat() == "2026-06-15"


def test_holiday_week_folds_into_next_session() -> None:
    # Sat 5/23, Sun 5/24, Memorial Day 5/25 -> Tuesday 5/26.
    for ts in (_utc(2026, 5, 23, 16), _utc(2026, 5, 24, 16), _utc(2026, 5, 25, 16)):
        assert SW.assign_session(ts).isoformat() == "2026-05-26"


def test_window_available_at_not_after_open() -> None:
    from datetime import date

    result = SW.window_for_session(date(2026, 6, 15))
    assert result.available_at == result.feature_cutoff
    assert result.feature_cutoff < result.target_session_open
    assert result.cutoff_buffer_seconds == 300
    assert result.window_start < result.window_end


def test_conservative_policy_maps_full_day_to_next_session() -> None:
    # A Monday article (any time) maps to the NEXT session under Policy A.
    assert CD.assign_session(_utc(2026, 6, 15, 12)).isoformat() == "2026-06-16"


def test_conservative_daily_feature_is_available_before_cutoff() -> None:
    from datetime import date

    result = CD.window_for_session(date(2026, 6, 15))
    assert result.available_at < result.feature_cutoff < result.target_session_open
    assert result.alignment_precision == "daily_approximation"


def test_cutoff_boundary_equality_and_after_behavior() -> None:
    # Summer open 13:30Z, default cutoff 13:25Z.
    assert SW.assign_session(_utc(2026, 6, 15, 13, 25)).isoformat() == "2026-06-15"
    assert SW.assign_session(_utc(2026, 6, 15, 13, 25, 1)).isoformat() == "2026-06-16"


def test_zero_buffer_is_explicitly_supported() -> None:
    policy = SessionInformationWindowPolicy(CAL, cutoff_buffer_seconds=0)
    result = policy.window_for_session(__import__("datetime").date(2026, 6, 15))
    assert result.feature_cutoff == result.target_session_open
    assert result.cutoff_buffer_seconds == 0


def test_cutoff_tracks_daylight_saving() -> None:
    from datetime import date

    winter = SW.window_for_session(date(2026, 1, 5))
    summer = SW.window_for_session(date(2026, 7, 6))
    assert (winter.feature_cutoff.hour, winter.feature_cutoff.minute) == (14, 25)
    assert (summer.feature_cutoff.hour, summer.feature_cutoff.minute) == (13, 25)


def test_early_close_affects_next_session_window_start() -> None:
    from datetime import date

    # Monday 11/30 follows the Friday 11/27 early close at 13:00 ET.
    result = SW.window_for_session(date(2026, 11, 30))
    assert result.window_start == CAL.session_close(date(2026, 11, 27))
    assert result.window_start.hour == 18

"""Tests for the U.S. market-calendar abstraction."""

from datetime import date

import pytest

from kinetic.processing.market.calendar import USEquitySessionCalendarV1

CAL = USEquitySessionCalendarV1()


def test_ordinary_weekday_is_session() -> None:
    assert CAL.is_session(date(2026, 6, 2))  # Tuesday


def test_weekends_are_not_sessions() -> None:
    assert not CAL.is_session(date(2026, 6, 6))  # Saturday
    assert not CAL.is_session(date(2026, 6, 7))  # Sunday


def test_fixed_and_floating_holidays() -> None:
    assert not CAL.is_session(date(2026, 1, 1))  # New Year's Day
    assert not CAL.is_session(date(2026, 5, 25))  # Memorial Day (last Mon May)
    assert not CAL.is_session(date(2026, 12, 25))  # Christmas
    assert not CAL.is_session(date(2026, 7, 3))  # Independence Day observed (4th is Sat)
    assert not CAL.is_session(date(2026, 4, 3))  # Good Friday 2026


@pytest.mark.parametrize(
    "holiday",
    [
        date(2026, 1, 1),  # New Year
        date(2026, 1, 19),  # MLK
        date(2026, 2, 16),  # Presidents Day
        date(2026, 4, 3),  # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),  # Independence Day observed
        date(2026, 9, 7),  # Labor Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas
    ],
)
def test_supported_2026_full_day_holidays(holiday: date) -> None:
    assert not CAL.is_session(holiday)


def test_juneteenth_exchange_closure_starts_in_2022() -> None:
    assert CAL.is_session(date(2021, 6, 18))
    assert not CAL.is_session(date(2022, 6, 20))


def test_next_session_skips_weekend_to_monday() -> None:
    assert CAL.next_session(date(2026, 6, 5)) == date(2026, 6, 8)  # Fri -> Mon
    assert CAL.next_session(date(2026, 6, 6)) == date(2026, 6, 8)  # Sat -> Mon
    assert CAL.next_session(date(2026, 6, 7)) == date(2026, 6, 8)  # Sun -> Mon


def test_consecutive_non_sessions_before_holiday() -> None:
    # Sat 5/23, Sun 5/24, Memorial Day Mon 5/25 all roll to Tue 5/26.
    for d in (date(2026, 5, 23), date(2026, 5, 24), date(2026, 5, 25)):
        assert CAL.next_session(d) == date(2026, 5, 26)


def test_year_boundary_previous_session() -> None:
    # First session of 2026 is Jan 2 (Jan 1 holiday); previous is Dec 31, 2025.
    assert CAL.next_session(date(2025, 12, 31)) == date(2026, 1, 2)
    assert CAL.previous_session(date(2026, 1, 2)) == date(2025, 12, 31)


def test_session_open_close_are_utc_and_dst_aware() -> None:
    # Winter session: 09:30 EST = 14:30 UTC.
    jan_open = CAL.session_open(date(2026, 1, 5))
    assert (jan_open.hour, jan_open.minute) == (14, 30)
    # Summer session: 09:30 EDT = 13:30 UTC.
    jul_open = CAL.session_open(date(2026, 7, 6))
    assert (jul_open.hour, jul_open.minute) == (13, 30)


def test_early_close_day_closes_early() -> None:
    # Day after Thanksgiving 2026 (Thanksgiving = 4th Thu Nov = Nov 26 -> Nov 27).
    black_friday = date(2026, 11, 27)
    assert CAL.is_session(black_friday)
    assert CAL.is_early_close(black_friday)
    close = CAL.session_close(black_friday)
    assert (close.hour, close.minute) == (18, 0)  # 13:00 EST = 18:00 UTC


def test_sessions_between_inclusive_and_ordered() -> None:
    sessions = CAL.sessions_between(date(2026, 6, 1), date(2026, 6, 5))
    assert sessions == [
        date(2026, 6, 1),
        date(2026, 6, 2),
        date(2026, 6, 3),
        date(2026, 6, 4),
        date(2026, 6, 5),
    ]


def test_out_of_range_raises() -> None:
    with pytest.raises(ValueError):
        CAL.is_session(date(1990, 1, 2))

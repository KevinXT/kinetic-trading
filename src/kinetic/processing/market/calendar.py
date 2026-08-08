"""Curated V1 calendar rules for regular U.S. equity cash sessions.

The research layer needs exchange-session-aware semantics, not a weekday check.
Weekends, fixed and observed holidays, consecutive holidays, year boundaries, and
daylight-saving transitions all matter for aligning news to the *next tradable*
session.

Design choices:

- ``America/New_York`` defines session wall-clock; persisted timestamps are UTC.
- A curated, transparent rule set approximates the common NYSE/Nasdaq cash
  schedule. It is not an authoritative exchange calendar and is not verified
  against an exchange schedule service.
- Common full-day holidays and common 13:00 ET closes are modeled. Unscheduled
  closures, weather/emergency closures, and historical rule exceptions are not.
- The default calendar covers 2018-2035, which brackets the provider data this
  project can realistically obtain. Requesting a session date outside the loaded
  range raises, rather than silently guessing.

Tests use deterministic dates inside this range; no network or system-locale
dependency is introduced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")

# Regular U.S. equity cash session (Eastern wall-clock).
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

_MIN_YEAR = 2018
_MAX_YEAR = 2035
CALENDAR_NAME = "us_equity_session_calendar_v1"


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return the date of the ``n``-th ``weekday`` (Mon=0) in ``month``."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Return the date of the last ``weekday`` (Mon=0) in ``month``."""
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    last = nxt - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def _observed(holiday: date) -> date:
    """Apply the standard weekend observation shift for a fixed-date holiday."""
    if holiday.weekday() == 5:  # Saturday -> observed Friday
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:  # Sunday -> observed Monday
        return holiday + timedelta(days=1)
    return holiday


def _good_friday(year: int) -> date:
    """Good Friday = two days before Easter Sunday (anonymous Gregorian algorithm)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    easter = date(year, month, day)
    return easter - timedelta(days=2)


@lru_cache(maxsize=None)
def _full_holidays(year: int) -> frozenset[date]:
    """Full-day market holidays for a single year (observed dates)."""
    holidays: set[date] = set()
    holidays.add(_observed(date(year, 1, 1)))  # New Year's Day
    holidays.add(_nth_weekday(year, 1, 0, 3))  # MLK Jr. Day (3rd Mon Jan)
    holidays.add(_nth_weekday(year, 2, 0, 3))  # Washington's Birthday (3rd Mon Feb)
    holidays.add(_good_friday(year))  # Good Friday
    holidays.add(_last_weekday(year, 5, 0))  # Memorial Day (last Mon May)
    if year >= 2022:
        # First NYSE/Nasdaq Juneteenth closure was 2022.
        holidays.add(_observed(date(year, 6, 19)))
    holidays.add(_observed(date(year, 7, 4)))  # Independence Day
    holidays.add(_nth_weekday(year, 9, 0, 1))  # Labor Day (1st Mon Sep)
    holidays.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving (4th Thu Nov)
    holidays.add(_observed(date(year, 12, 25)))  # Christmas Day
    return frozenset(holidays)


@lru_cache(maxsize=None)
def _early_closes(year: int) -> frozenset[date]:
    """Half-day early-close sessions (still tradable) for a single year."""
    closes: set[date] = set()
    # Day after Thanksgiving.
    closes.add(_nth_weekday(year, 11, 3, 4) + timedelta(days=1))
    # Christmas Eve, when it is a weekday and not itself a holiday.
    christmas_eve = date(year, 12, 24)
    if christmas_eve.weekday() < 5 and christmas_eve not in _full_holidays(year):
        closes.add(christmas_eve)
    # July 3rd, when it is a weekday and July 4th is observed on the 4th.
    july_third = date(year, 7, 3)
    if july_third.weekday() < 5 and _observed(date(year, 7, 4)) == date(year, 7, 4):
        closes.add(july_third)
    return frozenset(closes)


@dataclass(frozen=True)
class USEquitySessionCalendarV1:
    """Curated 2018–2035 U.S. equity cash-session rules.

    Extra holidays/early closes (e.g. one-off closures) can be injected for tests
    or corrections without editing the algorithmic set. This class deliberately
    does not claim authoritative exchange-calendar coverage.
    """

    tz: ZoneInfo = NEW_YORK
    extra_full_holidays: frozenset[date] = field(default_factory=frozenset)
    extra_early_closes: frozenset[date] = field(default_factory=frozenset)

    def _check_range(self, day: date) -> None:
        if not _MIN_YEAR <= day.year <= _MAX_YEAR:
            raise ValueError(
                f"date {day.isoformat()} is outside the supported market-calendar "
                f"range {_MIN_YEAR}-{_MAX_YEAR}"
            )

    def is_holiday(self, day: date) -> bool:
        self._check_range(day)
        return day in _full_holidays(day.year) or day in self.extra_full_holidays

    def is_early_close(self, day: date) -> bool:
        self._check_range(day)
        if self.is_holiday(day) or day.weekday() >= 5:
            return False
        return day in _early_closes(day.year) or day in self.extra_early_closes

    def is_session(self, day: date) -> bool:
        """True when regular trading occurs on ``day`` (early closes count)."""
        self._check_range(day)
        if day.weekday() >= 5:
            return False
        return not self.is_holiday(day)

    def next_session(self, day: date) -> date:
        """First session strictly after ``day``."""
        cursor = day + timedelta(days=1)
        while not (self.is_session(cursor)):
            cursor += timedelta(days=1)
        return cursor

    def previous_session(self, day: date) -> date:
        """Last session strictly before ``day``."""
        cursor = day - timedelta(days=1)
        while not (self.is_session(cursor)):
            cursor -= timedelta(days=1)
        return cursor

    def next_or_same_session(self, day: date) -> date:
        """``day`` if it is a session, otherwise the next session."""
        if self.is_session(day):
            return day
        return self.next_session(day)

    def sessions_between(self, start: date, end: date) -> list[date]:
        """All sessions with ``start <= session <= end`` (inclusive), ascending."""
        if start > end:
            return []
        out: list[date] = []
        cursor = start
        while cursor <= end:
            if self.is_session(cursor):
                out.append(cursor)
            cursor += timedelta(days=1)
        return out

    def session_open(self, session_date: date) -> datetime:
        """UTC open timestamp for a session (raises if not a session)."""
        if not self.is_session(session_date):
            raise ValueError(f"{session_date.isoformat()} is not a market session")
        local = datetime.combine(session_date, REGULAR_OPEN, tzinfo=self.tz)
        return local.astimezone(ZoneInfo("UTC"))

    def session_close(self, session_date: date) -> datetime:
        """UTC close timestamp for a session; early closes at 13:00 ET."""
        if not self.is_session(session_date):
            raise ValueError(f"{session_date.isoformat()} is not a market session")
        close_time = EARLY_CLOSE if self.is_early_close(session_date) else REGULAR_CLOSE
        local = datetime.combine(session_date, close_time, tzinfo=self.tz)
        return local.astimezone(ZoneInfo("UTC"))


# Backward-compatible source alias for the still-unreleased milestone API. New
# code should use the precise versioned class name.
MarketCalendar = USEquitySessionCalendarV1
DEFAULT_CALENDAR = USEquitySessionCalendarV1()

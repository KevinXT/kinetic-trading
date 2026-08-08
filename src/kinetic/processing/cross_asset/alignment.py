"""Alignment policies: mapping news publication time to a target market session.

The architecture makes the selected policy explicit and versioned. Two policies
are provided; the builder defaults to the session-information window (Policy B),
which folds weekend/holiday information into a single pre-open decision interval.

Every policy answers two questions:

- ``assign_session(published_at)`` — which target session does an article inform?
- ``window_for_session(session_date)`` — the (start, end, available_at) interval
  whose news is legitimately usable before that session.

All datetimes are timezone-aware and returned in UTC. The leakage-critical value
is ``feature_cutoff``: ``feature_available_at`` must be at or before it. The
default cutoff is five minutes before the target-session open. This buffer is an
operational conservatism rule, not a model of provider delivery or indexing delay.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

from kinetic.processing.market.calendar import MarketCalendar

POLICY_SESSION_WINDOW = "session_information_window_v2"
POLICY_CONSERVATIVE = "conservative_calendar_day_v2"
ALIGNMENT_ARTICLE_TIMESTAMP = "article_timestamp"
ALIGNMENT_DAILY_APPROXIMATION = "daily_approximation"
DEFAULT_CUTOFF_BUFFER_SECONDS = 300


@dataclass(frozen=True)
class AlignmentResult:
    session_date: date
    window_start: datetime
    window_end: datetime
    available_at: datetime
    feature_cutoff: datetime
    target_session_open: datetime
    cutoff_buffer_seconds: int
    calendar_days_aggregated: int
    alignment_precision: str


class AlignmentPolicy(Protocol):
    @property
    def name(self) -> str: ...

    def assign_session(self, published_at: datetime) -> date: ...

    def window_for_session(self, session_date: date) -> AlignmentResult: ...


@dataclass(frozen=True)
class SessionInformationWindowPolicy:
    """Policy B: previous session close → configurable pre-open cutoff.

    ``cutoff_buffer_seconds`` shifts the feature cutoff earlier than the open.
    Equality at the cutoff is allowed; news published after it is assigned to the
    following session.
    """

    calendar: MarketCalendar
    cutoff_buffer_seconds: int = DEFAULT_CUTOFF_BUFFER_SECONDS
    name: str = POLICY_SESSION_WINDOW

    def __post_init__(self) -> None:
        if self.cutoff_buffer_seconds < 0:
            raise ValueError("cutoff_buffer_seconds must be non-negative")

    def _cutoff(self, session_date: date) -> datetime:
        return self.calendar.session_open(session_date) - timedelta(
            seconds=self.cutoff_buffer_seconds
        )

    def assign_session(self, published_at: datetime) -> date:
        if published_at.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
        published_utc = published_at.astimezone(timezone.utc)
        et_date = published_utc.astimezone(self.calendar.tz).date()
        candidate = self.calendar.next_or_same_session(et_date)
        if published_utc <= self._cutoff(candidate):
            return candidate
        return self.calendar.next_session(candidate)

    def window_for_session(self, session_date: date) -> AlignmentResult:
        if not self.calendar.is_session(session_date):
            raise ValueError(f"{session_date} is not a market session")
        prev = self.calendar.previous_session(session_date)
        window_start = self.calendar.session_close(prev)
        cutoff = self._cutoff(session_date)
        session_open = self.calendar.session_open(session_date)
        # How many calendar days of news feed this single decision interval.
        calendar_days = (session_date - prev).days
        return AlignmentResult(
            session_date=session_date,
            window_start=window_start,
            window_end=cutoff,
            available_at=cutoff,
            feature_cutoff=cutoff,
            target_session_open=session_open,
            cutoff_buffer_seconds=self.cutoff_buffer_seconds,
            calendar_days_aggregated=calendar_days,
            alignment_precision=ALIGNMENT_ARTICLE_TIMESTAMP,
        )


@dataclass(frozen=True)
class ConservativeCalendarDayPolicy:
    """Policy A: news for calendar date D → next valid session after D.

    Simple and conservative. Several source dates (Fri/Sat/Sun) can map to the
    same target session; that sharing is recorded by the builder rather than
    duplicated into independent rows. The whole calendar day is treated as the
    window, so same-day session information cannot leak backward.
    """

    calendar: MarketCalendar
    cutoff_buffer_seconds: int = DEFAULT_CUTOFF_BUFFER_SECONDS
    name: str = POLICY_CONSERVATIVE

    def __post_init__(self) -> None:
        if self.cutoff_buffer_seconds < 0:
            raise ValueError("cutoff_buffer_seconds must be non-negative")

    def _cutoff(self, session_date: date) -> datetime:
        return self.calendar.session_open(session_date) - timedelta(
            seconds=self.cutoff_buffer_seconds
        )

    def _source_date(self, published_at: datetime) -> date:
        return published_at.astimezone(self.calendar.tz).date()

    def assign_session(self, published_at: datetime) -> date:
        if published_at.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
        return self.calendar.next_session(self._source_date(published_at))

    def window_for_session(self, session_date: date) -> AlignmentResult:
        if not self.calendar.is_session(session_date):
            raise ValueError(f"{session_date} is not a market session")
        source_day = self.calendar.previous_session(session_date)
        # The conservative window spans from the previous session date through the
        # end of the calendar day before the target session.
        start_local = datetime.combine(source_day, datetime.min.time(), tzinfo=self.calendar.tz)
        end_local = datetime.combine(session_date, datetime.min.time(), tzinfo=self.calendar.tz)
        session_open = self.calendar.session_open(session_date)
        feature_cutoff = self._cutoff(session_date)
        calendar_days = (session_date - source_day).days
        return AlignmentResult(
            session_date=session_date,
            window_start=start_local.astimezone(timezone.utc),
            window_end=end_local.astimezone(timezone.utc),
            # A complete prior calendar-day measurement is conservatively treated
            # as available at midnight, not at the market open.
            available_at=end_local.astimezone(timezone.utc),
            feature_cutoff=feature_cutoff,
            target_session_open=session_open,
            cutoff_buffer_seconds=self.cutoff_buffer_seconds,
            calendar_days_aggregated=calendar_days,
            alignment_precision=ALIGNMENT_DAILY_APPROXIMATION,
        )


def build_alignment_policy(
    name: str,
    calendar: MarketCalendar,
    *,
    cutoff_buffer_seconds: int = DEFAULT_CUTOFF_BUFFER_SECONDS,
) -> AlignmentPolicy:
    if name == POLICY_SESSION_WINDOW:
        return SessionInformationWindowPolicy(calendar, cutoff_buffer_seconds=cutoff_buffer_seconds)
    if name == POLICY_CONSERVATIVE:
        return ConservativeCalendarDayPolicy(calendar, cutoff_buffer_seconds=cutoff_buffer_seconds)
    raise ValueError(f"unknown alignment policy: {name!r}")

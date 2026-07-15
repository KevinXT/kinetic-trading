"""Provider-independent request contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from market_data.domain.models import normalize_cik


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _date(value: date, field_name: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{field_name} must be a date")
    return value


def _text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _symbols(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = (_text(value, "symbol").upper() for value in values)
    return tuple(sorted(set(normalized)))


@dataclass(frozen=True, slots=True)
class BarsRequest:
    symbols: tuple[str, ...]
    timeframe: str
    start: datetime
    end: datetime
    feed: str
    adjustment: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbols", _symbols(self.symbols))
        if not self.symbols:
            raise ValueError("symbols must contain at least one symbol")
        for field_name in ("timeframe", "feed", "adjustment"):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        object.__setattr__(self, "start", _utc(self.start, "start"))
        object.__setattr__(self, "end", _utc(self.end, "end"))
        if self.start > self.end:
            raise ValueError("start must not be after end")


@dataclass(frozen=True, slots=True)
class FilingRequest:
    symbols: tuple[str, ...] = ()
    ciks: tuple[str, ...] = ()
    forms: tuple[str, ...] = ("8-K", "10-Q", "10-K", "4")
    start_date: date | None = None
    end_date: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbols", _symbols(self.symbols))
        object.__setattr__(self, "ciks", tuple(sorted({normalize_cik(cik) for cik in self.ciks})))
        object.__setattr__(
            self,
            "forms",
            tuple(sorted({_text(form, "form").upper() for form in self.forms})),
        )
        if not self.symbols and not self.ciks:
            raise ValueError("at least one symbol or CIK is required")
        if not self.forms:
            raise ValueError("forms must contain at least one form")
        if self.start_date is not None:
            object.__setattr__(self, "start_date", _date(self.start_date, "start_date"))
        if self.end_date is not None:
            object.__setattr__(self, "end_date", _date(self.end_date, "end_date"))
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.start_date > self.end_date
        ):
            raise ValueError("start_date must not be after end_date")


@dataclass(frozen=True, slots=True)
class CompanyFactsRequest:
    symbols: tuple[str, ...] = ()
    ciks: tuple[str, ...] = ()
    concepts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbols", _symbols(self.symbols))
        object.__setattr__(self, "ciks", tuple(sorted({normalize_cik(cik) for cik in self.ciks})))
        object.__setattr__(
            self,
            "concepts",
            tuple(sorted({_text(concept, "concept") for concept in self.concepts})),
        )
        if not self.symbols and not self.ciks:
            raise ValueError("at least one symbol or CIK is required")


@dataclass(frozen=True, slots=True)
class MacroSeriesRequest:
    series_id: str
    observation_start: date | None = None
    observation_end: date | None = None
    realtime_start: date | None = None
    realtime_end: date | None = None
    vintage_dates: tuple[date, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "series_id", _text(self.series_id, "series_id").upper())
        for field_name in (
            "observation_start",
            "observation_end",
            "realtime_start",
            "realtime_end",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _date(value, field_name))
        object.__setattr__(
            self,
            "vintage_dates",
            tuple(sorted({_date(value, "vintage_date") for value in self.vintage_dates})),
        )
        if (
            self.observation_start is not None
            and self.observation_end is not None
            and self.observation_start > self.observation_end
        ):
            raise ValueError("observation_start must not be after observation_end")
        if (
            self.realtime_start is not None
            and self.realtime_end is not None
            and self.realtime_start > self.realtime_end
        ):
            raise ValueError("realtime_start must not be after realtime_end")

"""Provider-independent records used by ingestion, storage, and research."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import TypeAlias

LogicalKey: TypeAlias = tuple[object, ...]


def normalize_cik(value: str) -> str:
    """Return the SEC's canonical ten-digit CIK representation."""
    normalized = value.strip()
    if normalized.lower().startswith("cik"):
        normalized = normalized[3:]
    if not normalized.isdigit() or len(normalized) > 10:
        raise ValueError(f"CIK must contain at most ten digits, got {value!r}")
    return normalized.zfill(10)


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _identity_text(value: str, field_name: str) -> str:
    """Validate identity text without changing the provider's representation."""
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _symbol(value: str) -> str:
    return _required_text(value, "symbol").upper()


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _date(value: date, field_name: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{field_name} must be a date")
    return value


def _finite(value: float, field_name: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{field_name} must be finite")
    return normalized


@dataclass(frozen=True, slots=True)
class PriceBar:
    symbol: str
    timestamp: datetime
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float | None
    trade_count: int | None
    provider: str
    feed: str
    adjustment: str
    retrieved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        object.__setattr__(self, "timestamp", _utc(self.timestamp, "timestamp"))
        object.__setattr__(self, "retrieved_at", _utc(self.retrieved_at, "retrieved_at"))
        for field_name in ("timeframe", "feed", "adjustment"):
            object.__setattr__(
                self, field_name, _identity_text(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "provider", _identity_text(self.provider, "provider"))
        for field_name in ("open", "high", "low", "close"):
            object.__setattr__(self, field_name, _finite(getattr(self, field_name), field_name))
        if self.vwap is not None:
            object.__setattr__(self, "vwap", _finite(self.vwap, "vwap"))
        if isinstance(self.volume, bool) or not isinstance(self.volume, int) or self.volume < 0:
            raise ValueError("volume must be a non-negative integer")
        if self.trade_count is not None and (
            isinstance(self.trade_count, bool)
            or not isinstance(self.trade_count, int)
            or self.trade_count < 0
        ):
            raise ValueError("trade_count must be a non-negative integer or None")
        if self.high < self.low:
            raise ValueError("high must be greater than or equal to low")

    @property
    def logical_key(self) -> LogicalKey:
        return (
            self.symbol,
            self.timestamp,
            self.timeframe,
            self.provider,
            self.feed,
            self.adjustment,
        )


@dataclass(frozen=True, slots=True)
class FilingEvent:
    accession_number: str
    cik: str
    symbol: str | None
    form: str
    accepted_at: datetime | None
    filed_date: date
    report_date: date | None
    primary_document: str | None
    filing_url: str
    items: tuple[str, ...]
    provider: str
    retrieved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "accession_number", _identity_text(self.accession_number, "accession_number")
        )
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        if self.symbol is not None:
            object.__setattr__(self, "symbol", _symbol(self.symbol))
        object.__setattr__(self, "form", _identity_text(self.form, "form"))
        if self.accepted_at is not None:
            object.__setattr__(self, "accepted_at", _utc(self.accepted_at, "accepted_at"))
        object.__setattr__(self, "filed_date", _date(self.filed_date, "filed_date"))
        if self.report_date is not None:
            object.__setattr__(self, "report_date", _date(self.report_date, "report_date"))
        if self.primary_document is not None:
            object.__setattr__(
                self,
                "primary_document",
                _required_text(self.primary_document, "primary_document"),
            )
        object.__setattr__(self, "filing_url", _required_text(self.filing_url, "filing_url"))
        object.__setattr__(
            self,
            "items",
            tuple(dict.fromkeys(item.strip() for item in self.items if item.strip())),
        )
        object.__setattr__(self, "provider", _identity_text(self.provider, "provider"))
        object.__setattr__(self, "retrieved_at", _utc(self.retrieved_at, "retrieved_at"))

    @property
    def logical_key(self) -> LogicalKey:
        return (self.accession_number,)


@dataclass(frozen=True, slots=True)
class FinancialFact:
    cik: str
    symbol: str | None
    taxonomy: str
    concept: str
    unit: str
    period_start: date | None
    period_end: date
    value: float
    fiscal_year: int | None
    fiscal_period: str | None
    form: str
    filed_date: date
    accession_number: str
    provider: str
    retrieved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        if self.symbol is not None:
            object.__setattr__(self, "symbol", _symbol(self.symbol))
        for field_name in ("taxonomy", "concept", "unit", "form", "accession_number", "provider"):
            object.__setattr__(
                self, field_name, _identity_text(getattr(self, field_name), field_name)
            )
        if self.period_start is not None:
            object.__setattr__(self, "period_start", _date(self.period_start, "period_start"))
        object.__setattr__(self, "period_end", _date(self.period_end, "period_end"))
        object.__setattr__(self, "filed_date", _date(self.filed_date, "filed_date"))
        object.__setattr__(self, "value", _finite(self.value, "value"))
        if self.fiscal_year is not None and (
            isinstance(self.fiscal_year, bool) or not isinstance(self.fiscal_year, int)
        ):
            raise ValueError("fiscal_year must be an integer or None")
        if self.fiscal_period is not None:
            object.__setattr__(
                self, "fiscal_period", _required_text(self.fiscal_period, "fiscal_period")
            )
        object.__setattr__(self, "retrieved_at", _utc(self.retrieved_at, "retrieved_at"))
        if self.period_start is not None and self.period_start > self.period_end:
            raise ValueError("period_start must not be after period_end")

    @property
    def logical_key(self) -> LogicalKey:
        return (
            self.cik,
            self.taxonomy,
            self.concept,
            self.unit,
            self.period_start,
            self.period_end,
            self.form,
            self.filed_date,
            self.accession_number,
        )


@dataclass(frozen=True, slots=True)
class MacroObservation:
    series_id: str
    observation_date: date
    value: float | None
    realtime_start: date
    realtime_end: date
    vintage_date: date | None
    provider: str
    retrieved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "series_id", _identity_text(self.series_id, "series_id"))
        for field_name in ("observation_date", "realtime_start", "realtime_end"):
            object.__setattr__(self, field_name, _date(getattr(self, field_name), field_name))
        if self.vintage_date is not None:
            object.__setattr__(self, "vintage_date", _date(self.vintage_date, "vintage_date"))
        if self.value is not None:
            object.__setattr__(self, "value", _finite(self.value, "value"))
        object.__setattr__(self, "provider", _identity_text(self.provider, "provider"))
        object.__setattr__(self, "retrieved_at", _utc(self.retrieved_at, "retrieved_at"))
        if self.realtime_start > self.realtime_end:
            raise ValueError("realtime_start must not be after realtime_end")

    @property
    def logical_key(self) -> LogicalKey:
        return (
            self.series_id,
            self.observation_date,
            self.realtime_start,
            self.realtime_end,
            self.vintage_date,
            self.provider,
        )


@dataclass(frozen=True, slots=True)
class MarketEvent:
    event_id: str
    event_type: str
    effective_at: datetime
    observed_at: datetime
    instrument_id: str | None
    symbol: str | None
    macro_series_id: str | None
    source: str
    source_id: str
    title: str | None
    payload_path: str | None

    def __post_init__(self) -> None:
        for field_name in ("event_id", "event_type", "source", "source_id"):
            object.__setattr__(
                self, field_name, _identity_text(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "effective_at", _utc(self.effective_at, "effective_at"))
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        if self.instrument_id is not None:
            object.__setattr__(
                self, "instrument_id", _required_text(self.instrument_id, "instrument_id")
            )
        if self.symbol is not None:
            object.__setattr__(self, "symbol", _symbol(self.symbol))
        if self.macro_series_id is not None:
            object.__setattr__(
                self,
                "macro_series_id",
                _identity_text(self.macro_series_id, "macro_series_id"),
            )
        if self.title is not None:
            object.__setattr__(self, "title", _required_text(self.title, "title"))
        if self.payload_path is not None:
            object.__setattr__(
                self, "payload_path", _required_text(self.payload_path, "payload_path")
            )

    @property
    def logical_key(self) -> LogicalKey:
        return (self.event_id,)


@dataclass(frozen=True, slots=True)
class Instrument:
    instrument_id: str
    symbol: str
    cik: str
    company_name: str
    aliases: tuple[str, ...]
    exchange: str
    sector: str | None
    sector_etf: str | None
    valid_from: date | None
    valid_to: date | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "instrument_id", _identity_text(self.instrument_id, "instrument_id")
        )
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        object.__setattr__(self, "company_name", _identity_text(self.company_name, "company_name"))
        object.__setattr__(
            self,
            "aliases",
            tuple(dict.fromkeys(alias.strip() for alias in self.aliases if alias.strip())),
        )
        object.__setattr__(self, "exchange", _identity_text(self.exchange, "exchange"))
        if self.sector is not None:
            object.__setattr__(self, "sector", _identity_text(self.sector, "sector"))
        if self.sector_etf is not None:
            object.__setattr__(self, "sector_etf", _symbol(self.sector_etf))
        if self.valid_from is not None:
            object.__setattr__(self, "valid_from", _date(self.valid_from, "valid_from"))
        if self.valid_to is not None:
            object.__setattr__(self, "valid_to", _date(self.valid_to, "valid_to"))
        if self.valid_from is not None and self.valid_to is not None:
            if self.valid_from > self.valid_to:
                raise ValueError("valid_from must not be after valid_to")

    @property
    def logical_key(self) -> LogicalKey:
        return (self.instrument_id,)

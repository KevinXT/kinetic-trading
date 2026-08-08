"""Canonical company-fundamentals records: filing events and reported facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from kinetic.data.schemas._validation import (
    LogicalKey,
    _date,
    _finite,
    _identity_text,
    _required_text,
    _symbol,
    _utc,
    normalize_cik,
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

"""Focused interfaces implemented by financial data providers."""

from __future__ import annotations

from typing import Protocol, Sequence

from market_data.domain.models import FilingEvent, FinancialFact, MacroObservation, PriceBar
from market_data.domain.requests import (
    BarsRequest,
    CompanyFactsRequest,
    FilingRequest,
    MacroSeriesRequest,
)


class PriceDataProvider(Protocol):
    def get_bars(self, request: BarsRequest) -> Sequence[PriceBar]:
        """Return normalized price bars for the request."""
        ...


class CompanyDataProvider(Protocol):
    def get_submissions(self, request: FilingRequest) -> Sequence[FilingEvent]:
        """Return normalized filing metadata."""
        ...

    def get_company_facts(self, request: CompanyFactsRequest) -> Sequence[FinancialFact]:
        """Return normalized company facts without collapsing revisions."""
        ...


class MacroDataProvider(Protocol):
    def get_observations(self, request: MacroSeriesRequest) -> Sequence[MacroObservation]:
        """Return normalized macro observations, including vintage metadata."""
        ...

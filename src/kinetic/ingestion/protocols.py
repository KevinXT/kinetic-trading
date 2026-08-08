"""Focused interfaces implemented by financial data providers."""

from __future__ import annotations

from typing import Protocol, Sequence

from kinetic.data.schemas.market import PriceBar
from kinetic.ingestion.requests import BarsRequest


class PriceDataProvider(Protocol):
    def get_bars(self, request: BarsRequest) -> Sequence[PriceBar]:
        """Return normalized price bars for the request."""
        ...

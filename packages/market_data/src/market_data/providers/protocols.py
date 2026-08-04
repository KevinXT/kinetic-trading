"""Focused interfaces implemented by financial data providers."""

from __future__ import annotations

from typing import Protocol, Sequence

from market_data.domain.models import PriceBar
from market_data.domain.requests import BarsRequest


class PriceDataProvider(Protocol):
    def get_bars(self, request: BarsRequest) -> Sequence[PriceBar]:
        """Return normalized price bars for the request."""
        ...

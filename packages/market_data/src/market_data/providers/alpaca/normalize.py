"""Pure Alpaca response normalization."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from market_data.domain.models import PriceBar
from market_data.domain.requests import BarsRequest
from market_data.providers.alpaca.errors import AlpacaNormalizationConflictError
from market_data.providers.alpaca.raw_models import AlpacaBarsPage


def normalize_alpaca_bars(
    pages: Sequence[AlpacaBarsPage],
    *,
    request: BarsRequest,
    retrieved_at: datetime,
) -> tuple[PriceBar, ...]:
    """Translate validated raw pages into deterministic provider-neutral bars."""
    by_key: dict[tuple[object, ...], PriceBar] = {}
    for page in pages:
        for symbol, raw_bars in page.bars.items():
            for raw_bar in raw_bars:
                bar = PriceBar(
                    symbol=symbol,
                    timestamp=raw_bar.timestamp,
                    timeframe=request.timeframe,
                    open=raw_bar.open,
                    high=raw_bar.high,
                    low=raw_bar.low,
                    close=raw_bar.close,
                    volume=raw_bar.volume,
                    vwap=raw_bar.vwap,
                    trade_count=raw_bar.trade_count,
                    provider="alpaca",
                    feed=request.feed,
                    adjustment=request.adjustment,
                    retrieved_at=retrieved_at,
                )
                existing = by_key.get(bar.logical_key)
                if existing is None:
                    by_key[bar.logical_key] = bar
                elif existing != bar:
                    raise AlpacaNormalizationConflictError(
                        f"conflicting Alpaca bars share logical key {bar.logical_key!r}"
                    )
    return tuple(by_key[key] for key in sorted(by_key))

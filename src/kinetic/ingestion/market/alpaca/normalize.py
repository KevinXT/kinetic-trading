"""Pure Alpaca response normalization."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from kinetic.data.schemas.market import DEFAULT_PRICE_BAR_CURRENCY, PriceBar
from kinetic.ingestion.market.alpaca.errors import AlpacaNormalizationConflictError
from kinetic.ingestion.market.alpaca.raw_models import AlpacaBarsPage
from kinetic.ingestion.requests import BarsRequest


def resolve_bar_currency(request: BarsRequest) -> str:
    """
    Currency stamped onto normalized bars for identity and storage.

    When the request omits currency, Kinetic stores ``USD`` to match Alpaca's
    documented default for the stock bars ``currency`` query parameter. When set,
    the value is forwarded as the requested price denomination. Kinetic does not
    model exchange rates or guarantee how Alpaca derives non-USD prices.
    """
    return request.currency if request.currency is not None else DEFAULT_PRICE_BAR_CURRENCY


def normalize_alpaca_bars(
    pages: Sequence[AlpacaBarsPage],
    *,
    request: BarsRequest,
    retrieved_at: datetime,
) -> tuple[PriceBar, ...]:
    """Translate validated raw pages into deterministic provider-neutral bars."""
    currency = resolve_bar_currency(request)
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
                    currency=currency,
                    retrieved_at=retrieved_at,
                )
                existing = by_key.get(bar.logical_key)
                if existing is None:
                    by_key[bar.logical_key] = bar
                elif not existing.market_data_equal(bar):
                    raise AlpacaNormalizationConflictError(
                        f"conflicting Alpaca bars share logical key {bar.logical_key!r}"
                    )
    return tuple(by_key[key] for key in sorted(by_key))

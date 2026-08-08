"""Canonical market records: price bars and market events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kinetic.data.schemas._validation import (
    LogicalKey,
    _identity_text,
    _non_negative_finite,
    _required_text,
    _symbol,
    _utc,
    normalize_currency,
)

# Alpaca (and every other price provider so far) quotes in USD unless a
# request asks otherwise. Kinetic does not model FX rates; this is the
# denomination the bar was reported in, not a conversion.
DEFAULT_PRICE_BAR_CURRENCY = "USD"


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
    currency: str
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
        object.__setattr__(self, "currency", normalize_currency(self.currency))
        for field_name in ("open", "high", "low", "close"):
            object.__setattr__(
                self, field_name, _non_negative_finite(getattr(self, field_name), field_name)
            )
        if self.vwap is not None:
            object.__setattr__(self, "vwap", _non_negative_finite(self.vwap, "vwap"))
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
        if not self.low <= self.open <= self.high:
            raise ValueError("open must be between low and high inclusive")
        if not self.low <= self.close <= self.high:
            raise ValueError("close must be between low and high inclusive")
        # Non-negative OHLC/VWAP matches current equity-bar usage. Instruments that
        # can print negative prices are out of scope for this model version.

    @property
    def logical_key(self) -> LogicalKey:
        return (
            self.symbol,
            self.timestamp,
            self.timeframe,
            self.provider,
            self.feed,
            self.adjustment,
            self.currency,
        )

    def market_data_equal(self, other: PriceBar) -> bool:
        """Compare provider market payload, ignoring retrieval metadata."""
        return (
            self.symbol == other.symbol
            and self.timestamp == other.timestamp
            and self.timeframe == other.timeframe
            and self.open == other.open
            and self.high == other.high
            and self.low == other.low
            and self.close == other.close
            and self.volume == other.volume
            and self.vwap == other.vwap
            and self.trade_count == other.trade_count
            and self.provider == other.provider
            and self.feed == other.feed
            and self.adjustment == other.adjustment
            and self.currency == other.currency
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

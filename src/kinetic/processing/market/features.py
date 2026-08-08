"""Build financially meaningful market-session features from ``PriceBar`` records.

Only homogeneous groups of bars are combined: the same symbol, timeframe,
provider, feed, adjustment, and currency. A return is never computed across
incompatible bar identities. All rolling estimators used as predictors are
computed from strictly prior sessions; same-session descriptive fields are kept
separate so the join can forbid them as predictors of their own session.

Estimators use precise names (trailing close-to-close volatility, rolling
Parkinson variance, Amihud illiquidity proxy) and are documented as daily
estimators, not high-frequency realized quantities.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Sequence

from kinetic.data.schemas.market import PriceBar
from kinetic.data.schemas.research import COVERAGE_OK, MarketSessionFeature
from kinetic.processing import stats

# Bar identity that must match for bars to be combined into one series.
IdentityKey = tuple[str, str, str, str, str, str]  # symbol, tf, provider, feed, adj, currency


def bar_identity(bar: PriceBar) -> IdentityKey:
    return (
        bar.symbol,
        bar.timeframe,
        bar.provider,
        bar.feed,
        bar.adjustment,
        bar.currency,
    )


def bar_session_date(bar: PriceBar) -> date:
    """Session date for a daily bar.

    Alpaca timestamps daily bars at ET midnight (04:00Z in EDT, 05:00Z in EST),
    so the UTC calendar date equals the trading session date.
    """
    return bar.timestamp.date()


def _parkinson_variance(high: float, low: float) -> float | None:
    """Daily Parkinson variance estimate: (1/(4 ln2)) * ln(high/low)^2.

    Returns None for a non-positive low (cannot take the log-range).
    """
    if high <= 0 or low <= 0:
        return None
    return (1.0 / (4.0 * math.log(2.0))) * (math.log(high / low) ** 2)


def _true_range(high: float, low: float, prev_close: float | None) -> float:
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def _safe_ratio_minus_one(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator - 1.0


def _self_inputs(bar: PriceBar, prev_close: float | None) -> dict[str, object]:
    """Self-contained per-session derived fields (no external series needed)."""
    high_low = bar.high - bar.low
    close_location: float | None
    if high_low <= 0:
        close_location = 0.5  # explicit zero-range policy
    else:
        close_location = (bar.close - bar.low) / high_low

    simple_return = _safe_ratio_minus_one(bar.close, prev_close) if prev_close else None
    log_return = (
        math.log(bar.close / prev_close)
        if (prev_close and prev_close > 0 and bar.close > 0)
        else None
    )
    overnight_return = _safe_ratio_minus_one(bar.open, prev_close) if prev_close else None
    intraday_return = _safe_ratio_minus_one(bar.close, bar.open)
    dollar_volume = bar.close * bar.volume
    parkinson = _parkinson_variance(bar.high, bar.low)

    amihud: float | None = None
    if simple_return is not None and dollar_volume > 0:
        amihud = abs(simple_return) / dollar_volume

    vwap_deviation: float | None = None
    if bar.vwap is not None and bar.vwap > 0:
        vwap_deviation = bar.close / bar.vwap - 1.0

    volume_per_trade: float | None = None
    if bar.trade_count is not None and bar.trade_count > 0:
        volume_per_trade = bar.volume / bar.trade_count

    return {
        "simple_return": simple_return,
        "log_return": log_return,
        "overnight_return": overnight_return,
        "intraday_return": intraday_return,
        "high_low_range": (high_low / bar.close) if bar.close > 0 else None,
        "absolute_return": abs(simple_return) if simple_return is not None else None,
        "true_range": _true_range(bar.high, bar.low, prev_close),
        "parkinson_variance": parkinson,
        "dollar_volume": dollar_volume,
        "log_volume": math.log1p(bar.volume),
        "volume_per_trade": volume_per_trade,
        "vwap_deviation": vwap_deviation,
        "close_location": close_location,
        "amihud_illiquidity_proxy": amihud,
    }


@dataclass(frozen=True)
class MarketSeries:
    """Ordered market-session features for one or more homogeneous instruments."""

    feature_version: str
    _by_symbol: dict[str, list[MarketSessionFeature]]
    _index: dict[tuple[str, str], int]  # (symbol, session_date iso) -> position

    def symbols(self) -> list[str]:
        return sorted(self._by_symbol)

    def features(self) -> list[MarketSessionFeature]:
        out: list[MarketSessionFeature] = []
        for symbol in self.symbols():
            out.extend(self._by_symbol[symbol])
        return out

    def position(self, symbol: str, session_date: date) -> int | None:
        return self._index.get((symbol, session_date.isoformat()))

    def feature_at(self, symbol: str, session_date: date) -> MarketSessionFeature | None:
        pos = self.position(symbol, session_date)
        if pos is None:
            return None
        return self._by_symbol[symbol][pos]

    def feature_by_offset(
        self, symbol: str, session_date: date, offset: int
    ) -> MarketSessionFeature | None:
        pos = self.position(symbol, session_date)
        if pos is None:
            return None
        target = pos + offset
        series = self._by_symbol[symbol]
        if 0 <= target < len(series):
            return series[target]
        return None

    def has_symbol(self, symbol: str) -> bool:
        return symbol in self._by_symbol


def build_market_series(
    bars: Sequence[PriceBar],
    *,
    feature_version: str,
    session_open_fn,
    session_close_fn,
) -> MarketSeries:
    """Build market-session features, one homogeneous series per symbol.

    ``session_open_fn`` / ``session_close_fn`` map a session date to UTC open/close
    datetimes (from the market calendar). Bars whose session date is not a valid
    session raise via the calendar functions, surfacing calendar mismatches loudly.
    """
    grouped: dict[str, list[PriceBar]] = {}
    identities: dict[str, IdentityKey] = {}
    for bar in bars:
        identity = bar_identity(bar)
        symbol = bar.symbol
        if symbol in identities and identities[symbol] != identity:
            raise ValueError(
                f"heterogeneous bar identities for {symbol}: "
                f"{identities[symbol]} vs {identity}; refusing to mix feed/adjustment/currency"
            )
        identities.setdefault(symbol, identity)
        grouped.setdefault(symbol, []).append(bar)

    by_symbol: dict[str, list[MarketSessionFeature]] = {}
    index: dict[tuple[str, str], int] = {}

    for symbol, symbol_bars in grouped.items():
        ordered = sorted(symbol_bars, key=lambda b: b.timestamp)
        closes: list[float] = []
        log_returns: list[float] = []
        parkinsons: list[float] = []
        volumes: list[float] = []
        features: list[MarketSessionFeature] = []

        for i, bar in enumerate(ordered):
            prev_close = closes[-1] if closes else None
            self_inputs = _self_inputs(bar, prev_close)

            # Rolling, as-of-close descriptive fields (use history up to this bar).
            prior_closes = closes[:]  # strictly before this session
            ret_5 = _safe_ratio_minus_one(bar.close, closes[-5]) if len(closes) >= 5 else None
            ret_20 = _safe_ratio_minus_one(bar.close, closes[-20]) if len(closes) >= 20 else None
            current_log_return = self_inputs["log_return"]
            log_window = list(log_returns)
            if current_log_return is not None:
                log_window.append(float(current_log_return))  # type: ignore[arg-type]
            vol_cc_20 = stats.sample_std(log_window[-20:]) if len(log_window) >= 20 else None
            current_parkinson = self_inputs["parkinson_variance"]
            parkinson_window = list(parkinsons)
            if current_parkinson is not None:
                parkinson_window.append(float(current_parkinson))  # type: ignore[arg-type]
            park_20 = stats.mean(parkinson_window[-20:]) if len(parkinson_window) >= 20 else None
            vol_z_20 = (
                stats.lag_zscore(float(bar.volume), volumes[-20:]) if len(volumes) >= 20 else None
            )
            dist_20 = None
            if len(prior_closes) >= 20:
                mean_20 = stats.mean(closes[-20:])
                if mean_20:
                    dist_20 = bar.close / mean_20 - 1.0

            rolling = {
                "trailing_return_5": ret_5,
                "trailing_return_20": ret_20,
                "trailing_close_to_close_vol_20": vol_cc_20,
                "rolling_parkinson_20": park_20,
                "volume_zscore_20": vol_z_20,
                "distance_from_mean_20": dist_20,
                "prior_direction": (
                    None
                    if self_inputs["simple_return"] is None
                    else (1 if float(self_inputs["simple_return"]) > 0 else -1)  # type: ignore[arg-type]
                ),
                "market_history_len": len(prior_closes),
            }

            session_date = bar_session_date(bar)
            feature = MarketSessionFeature(
                instrument_id=f"symbol:{symbol}",
                symbol=symbol,
                session_date=session_date,
                provider=bar.provider,
                feed=bar.feed,
                adjustment=bar.adjustment,
                currency=bar.currency,
                timeframe=bar.timeframe,
                feature_version=feature_version,
                session_open=session_open_fn(session_date),
                session_close=session_close_fn(session_date),
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                trade_count=bar.trade_count,
                vwap=bar.vwap,
                inputs={**self_inputs, **rolling},
                coverage_status=COVERAGE_OK,
                quality_flags={"bar_present": True},
            )
            features.append(feature)
            index[(symbol, session_date.isoformat())] = i

            closes.append(bar.close)
            if self_inputs["log_return"] is not None:
                log_returns.append(float(self_inputs["log_return"]))  # type: ignore[arg-type]
            if self_inputs["parkinson_variance"] is not None:
                parkinsons.append(float(self_inputs["parkinson_variance"]))  # type: ignore[arg-type]
            volumes.append(float(bar.volume))

        by_symbol[symbol] = features

    return MarketSeries(feature_version=feature_version, _by_symbol=by_symbol, _index=index)

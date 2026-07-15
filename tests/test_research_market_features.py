"""Tests for market-session feature formulas, using independent expected values."""

import math
from datetime import date, datetime, timezone

import pytest
from market_data.domain.models import PriceBar
from research_data.calendar import MarketCalendar
from research_data.market_features import build_market_series

CAL = MarketCalendar()


def _bar(symbol, sess, o, h, low, c, v, vwap=None, tc=None) -> PriceBar:
    ts = datetime(sess.year, sess.month, sess.day, 4, 0, tzinfo=timezone.utc)
    return PriceBar(
        symbol=symbol,
        timestamp=ts,
        timeframe="1Day",
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
        vwap=vwap,
        trade_count=tc,
        provider="alpaca",
        feed="iex",
        adjustment="all",
        currency="USD",
        retrieved_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


def _series(bars):
    return build_market_series(
        bars,
        feature_version="v1",
        session_open_fn=CAL.session_open,
        session_close_fn=CAL.session_close,
    )


def test_return_decomposition_formulas() -> None:
    bars = [
        _bar("NVDA", date(2026, 6, 15), 100, 110, 95, 100, 1000, vwap=100, tc=50),
        _bar("NVDA", date(2026, 6, 16), 100, 120, 100, 110, 2000, vwap=110, tc=100),
    ]
    series = _series(bars)
    f = series.feature_at("NVDA", date(2026, 6, 16))
    assert f is not None
    inp = f.inputs
    assert math.isclose(inp["simple_return"], 0.1)
    assert math.isclose(inp["log_return"], math.log(1.1))
    assert math.isclose(inp["overnight_return"], 0.0, abs_tol=1e-12)
    assert math.isclose(inp["intraday_return"], 0.1)
    assert math.isclose(inp["true_range"], 20.0)
    assert math.isclose(inp["absolute_return"], 0.1)
    expected_parkinson = (1.0 / (4.0 * math.log(2.0))) * (math.log(120 / 100) ** 2)
    assert math.isclose(inp["parkinson_variance"], expected_parkinson)
    assert math.isclose(inp["dollar_volume"], 220000.0)
    assert math.isclose(inp["amihud_illiquidity_proxy"], 0.1 / 220000.0)
    assert math.isclose(inp["vwap_deviation"], 0.0, abs_tol=1e-12)
    assert math.isclose(inp["close_location"], 0.5)
    assert math.isclose(inp["volume_per_trade"], 20.0)


def test_first_bar_has_no_return() -> None:
    bars = [_bar("NVDA", date(2026, 6, 15), 100, 110, 95, 100, 1000)]
    f = _series(bars).feature_at("NVDA", date(2026, 6, 15))
    assert f is not None
    assert f.inputs["simple_return"] is None


def test_zero_range_close_location_is_half() -> None:
    bars = [_bar("NVDA", date(2026, 6, 15), 100, 100, 100, 100, 1000)]
    f = _series(bars).feature_at("NVDA", date(2026, 6, 15))
    assert f is not None
    assert f.inputs["close_location"] == 0.5


def test_trailing_return_uses_prior_closes() -> None:
    sessions = CAL.sessions_between(date(2026, 6, 1), date(2026, 6, 30))[:6]
    bars = []
    price = 100.0
    for s in sessions:
        bars.append(_bar("NVDA", s, price, price * 1.05, price * 0.95, price, 1000))
        price *= 1.10  # 10% up each session
    series = _series(bars)
    sixth = series.feature_at("NVDA", sessions[5])
    assert sixth is not None
    # trailing_return_5 = close_5 / close_0 - 1 (five sessions back).
    expected = (100 * 1.10**5) / 100 - 1
    assert math.isclose(sixth.inputs["trailing_return_5"], expected, rel_tol=1e-9)


def test_heterogeneous_identity_rejected() -> None:
    b1 = _bar("NVDA", date(2026, 6, 15), 100, 110, 95, 100, 1000)
    b2 = PriceBar(
        symbol="NVDA",
        timestamp=datetime(2026, 6, 16, 4, tzinfo=timezone.utc),
        timeframe="1Day",
        open=100,
        high=110,
        low=95,
        close=105,
        volume=1000,
        vwap=None,
        trade_count=None,
        provider="alpaca",
        feed="sip",  # different feed -> incompatible identity
        adjustment="all",
        currency="USD",
        retrieved_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    with pytest.raises(ValueError, match="heterogeneous"):
        _series([b1, b2])

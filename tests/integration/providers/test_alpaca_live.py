"""Opt-in live smoke test for Alpaca historical daily bars."""

import os
from datetime import datetime, timezone

import pytest

from kinetic.ingestion.market.alpaca.client import AlpacaBarsClient
from kinetic.ingestion.market.alpaca.config import AlpacaProviderConfig, resolve_credentials
from kinetic.ingestion.market.alpaca.normalize import normalize_alpaca_bars
from kinetic.ingestion.requests import BarsRequest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_PROVIDER_INTEGRATION_TESTS") != "1",
    reason="set RUN_PROVIDER_INTEGRATION_TESTS=1 to run provider integration tests",
)


def test_live_alpaca_completed_iex_daily_interval() -> None:
    config = AlpacaProviderConfig(maximum_pages=5)
    client = AlpacaBarsClient(config, resolve_credentials(config))
    request = BarsRequest(
        symbols=("AAPL", "MSFT"),
        timeframe="1Day",
        start=datetime(2024, 1, 2, tzinfo=timezone.utc),
        end=datetime(2024, 1, 6, tzinfo=timezone.utc),
        feed="iex",
        adjustment="all",
        limit=100,
        sort="asc",
    )
    try:
        raw = client.fetch_pages(request)
    finally:
        client.close()
    bars = normalize_alpaca_bars(
        raw.pages,
        request=request,
        retrieved_at=datetime.now(timezone.utc),
    )
    assert bars
    assert {bar.symbol for bar in bars} <= {"AAPL", "MSFT"}
    assert all(bar.provider == "alpaca" and bar.feed == "iex" for bar in bars)

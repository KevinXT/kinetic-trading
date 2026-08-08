"""Configuration and composition-root tests for Alpaca Phase 2."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from kinetic.core.errors import ConfigError
from kinetic.ingestion.market.alpaca.provider import AlpacaPriceProvider
from kinetic.ingestion.market.alpaca.tasks import (
    create_price_provider_registry,
    resolve_bars_request,
)


def test_bootstrap_registers_the_alpaca_bars_task() -> None:
    from kinetic.bootstrap import build_default_registry

    registry = build_default_registry()
    assert "market.alpaca.fetch_bars" in registry.task_ids()


def test_price_provider_factory_is_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALPACA_API_KEY_ID", "offline-test-id")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "offline-test-secret")
    provider = create_price_provider_registry().create_price_provider("alpaca", {})
    assert isinstance(provider, AlpacaPriceProvider)
    provider.close()


def test_unknown_price_provider_fails_clearly() -> None:
    with pytest.raises(ConfigError, match="unknown price provider"):
        create_price_provider_registry().create_price_provider("missing", {})


def test_committed_example_uses_explicit_historical_iex_request() -> None:
    path = Path(__file__).parents[3] / "configs" / "pipelines" / "alpaca_daily_bars.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = config["pipeline"]["steps"]
    assert len(steps) == 1
    assert steps[0]["task"] == "market.alpaca.fetch_bars"
    params = steps[0]["params"]
    assert params["request"]["feed"] == "iex"
    assert params["request"]["adjustment"] == "all"
    request = resolve_bars_request(
        params,
        resolved_now=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    assert request.start.isoformat() == "2026-06-01T00:00:00+00:00"
    assert request.end.isoformat() == "2026-06-30T23:59:59+00:00"


def test_lookback_is_resolved_once_to_explicit_boundaries() -> None:
    now = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
    request = resolve_bars_request(
        {
            "request": {
                "symbols": ["AAPL"],
                "timeframe": "1Day",
                "lookback_days": 30,
                "feed": "iex",
                "adjustment": "all",
            }
        },
        resolved_now=now,
    )
    assert request.end == now
    assert (request.end - request.start).days == 30


@pytest.mark.parametrize("missing", ["symbols", "timeframe", "feed", "adjustment"])
def test_missing_request_fields_fail_before_network(missing: str) -> None:
    request = {
        "symbols": ["AAPL"],
        "timeframe": "1Day",
        "start": "2026-06-01T00:00:00Z",
        "end": "2026-06-30T00:00:00Z",
        "feed": "iex",
        "adjustment": "all",
    }
    request.pop(missing)
    with pytest.raises((ConfigError, ValueError)):
        resolve_bars_request(
            {"request": request},
            resolved_now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )

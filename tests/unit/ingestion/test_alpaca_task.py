"""Offline pipeline-task tests for Alpaca historical bars."""

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kinetic.core.errors import ConfigError
from kinetic.core.pipeline.context import RunContext
from kinetic.data.storage.errors import ConcurrentDatasetWriteError
from kinetic.ingestion import caching as cache
from kinetic.ingestion.market.alpaca.client import AlpacaRawPages
from kinetic.ingestion.market.alpaca.config import AlpacaProviderConfig
from kinetic.ingestion.market.alpaca.provider import AlpacaPriceProvider
from kinetic.ingestion.market.alpaca.raw_models import parse_alpaca_bars_page
from kinetic.ingestion.market.alpaca.tasks import alpaca_historical_bars_task
from kinetic.ingestion.registry import ProviderRegistry
from kinetic.ingestion.requests import BarsRequest

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "alpaca"
NOW = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)


def _payload(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class _Client:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = False

    def fetch_pages(self, request: BarsRequest) -> AlpacaRawPages:
        self.calls += 1
        return AlpacaRawPages(
            pages=(
                parse_alpaca_bars_page(_payload("two_page_first.json")),
                parse_alpaca_bars_page(_payload("two_page_second.json")),
            ),
            retry_count=1,
        )

    def close(self) -> None:
        self.closed = True


class _CorrectedClient(_Client):
    def fetch_pages(self, request: BarsRequest) -> AlpacaRawPages:
        self.calls += 1
        corrected = copy.deepcopy(_payload("two_page_first.json"))
        corrected["bars"]["AAPL"][0]["c"] = 203.0  # type: ignore[index]
        return AlpacaRawPages(
            pages=(
                parse_alpaca_bars_page(corrected),
                parse_alpaca_bars_page(_payload("two_page_second.json")),
            ),
            retry_count=0,
        )


def _config(tmp_path: Path) -> dict:
    return {
        "providers": {
            "market": {
                "alpaca": {
                    "base_url": "https://data.alpaca.markets",
                    "key_id_env": "ALPACA_API_KEY_ID",
                    "secret_key_env": "ALPACA_API_SECRET_KEY",
                }
            }
        },
        "cache": {
            "ttl_seconds": None,
            "force_refresh": False,
            "schema_version": "alpaca-bars-v2",
        },
        "storage": {"type": "jsonl", "root": str(tmp_path / "market")},
    }


def _params(**request_overrides: object) -> dict:
    request = {
        "symbols": ["AAPL", "MSFT", "QQQ"],
        "timeframe": "1Day",
        "start": "2026-06-01T00:00:00Z",
        "end": "2026-06-30T23:59:59Z",
        "feed": "iex",
        "adjustment": "all",
        "limit": 1000,
        "sort": "asc",
    }
    request.update(request_overrides)
    return {"provider": "alpaca", "request": request}


def _ctx(tmp_path: Path, run_id: str = "run") -> RunContext:
    return RunContext(
        cfg=_config(tmp_path),
        run_name="alpaca-test",
        run_id=run_id,
        run_dir=tmp_path / run_id,
    )


def _registry(created: list[_Client]) -> ProviderRegistry:
    registry = ProviderRegistry()

    def factory(config: object) -> AlpacaPriceProvider:
        client = _Client()
        created.append(client)
        return AlpacaPriceProvider(AlpacaProviderConfig(), client)  # type: ignore[arg-type]

    registry.register_price_provider("alpaca", factory)  # type: ignore[arg-type]
    return registry


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")


def test_task_uses_registry_populates_state_and_writes_safe_artifacts(
    tmp_path: Path,
) -> None:
    created: list[_Client] = []
    ctx = _ctx(tmp_path)
    alpaca_historical_bars_task(
        ctx,
        _params(),
        registry=_registry(created),
        clock=lambda: NOW,
    )
    assert len(created) == 1
    assert created[0].calls == 1
    assert created[0].closed is True
    assert [bar.symbol for bar in ctx.state["price_bars"]] == ["AAPL", "MSFT"]

    expected = {
        "alpaca_request.json",
        "alpaca_cache_summary.json",
        "alpaca_pages_summary.json",
        "normalized_price_bars.jsonl",
        "alpaca_validation_summary.json",
        "alpaca_store_summary.json",
        "alpaca_summary.json",
    }
    assert expected <= {path.name for path in ctx.artifacts_dir.iterdir()}

    page_summary = json.loads(
        (ctx.artifacts_dir / "alpaca_pages_summary.json").read_text(encoding="utf-8")
    )
    assert page_summary["page_count"] == 2
    assert page_summary["records_per_page"] == [1, 1]
    assert page_summary["symbols_per_page"] == [["AAPL"], ["MSFT"]]
    assert page_summary["symbols_with_no_returned_bars"] == ["QQQ"]

    normalized = (ctx.artifacts_dir / "normalized_price_bars.jsonl").read_text(encoding="utf-8")
    assert '"schema_version": "financial-data.v1"' in normalized
    assert '"symbol": "AAPL"' in normalized

    all_artifacts = "\n".join(
        path.read_text(encoding="utf-8") for path in ctx.artifacts_dir.iterdir()
    )
    assert "fixture-key-id" not in all_artifacts
    assert "fixture-secret-key" not in all_artifacts


def test_identical_rerun_hits_cache_and_store_skips_duplicates(tmp_path: Path) -> None:
    created: list[_Client] = []
    registry = _registry(created)
    first = _ctx(tmp_path, "first")
    second = _ctx(tmp_path, "second")
    alpaca_historical_bars_task(first, _params(), registry=registry, clock=lambda: NOW)
    alpaca_historical_bars_task(second, _params(), registry=registry, clock=lambda: NOW)
    assert [client.calls for client in created] == [1, 0]
    cache_summary = json.loads(
        (second.artifacts_dir / "alpaca_cache_summary.json").read_text(encoding="utf-8")
    )
    store_summary = json.loads(
        (second.artifacts_dir / "alpaca_store_summary.json").read_text(encoding="utf-8")
    )
    assert cache_summary["status"] == "hit"
    assert store_summary["inserted"] == 0
    assert store_summary["updated"] == 0
    assert store_summary["skipped"] == 2


def test_force_refresh_without_payload_change_does_not_count_as_update(
    tmp_path: Path,
) -> None:
    created: list[_Client] = []
    registry = _registry(created)
    first = _ctx(tmp_path, "first")
    second = _ctx(tmp_path, "second")
    second.cfg["cache"]["force_refresh"] = True
    later = datetime(2026, 7, 2, 12, tzinfo=timezone.utc)
    alpaca_historical_bars_task(first, _params(), registry=registry, clock=lambda: NOW)
    alpaca_historical_bars_task(second, _params(), registry=registry, clock=lambda: later)
    summary = json.loads(
        (second.artifacts_dir / "alpaca_store_summary.json").read_text(encoding="utf-8")
    )
    request_artifact = json.loads(
        (second.artifacts_dir / "alpaca_request.json").read_text(encoding="utf-8")
    )
    assert summary["inserted"] == 0
    assert summary["updated"] == 0
    assert summary["skipped"] == 2
    assert request_artifact["api_origin"] == "https://data.alpaca.markets"
    assert request_artifact["currency"] == "USD"
    assert "fixture-key-id" not in json.dumps(request_artifact)
    assert "fixture-secret-key" not in json.dumps(request_artifact)


def test_force_refreshed_corrected_bar_is_counted_as_update(tmp_path: Path) -> None:
    clients = [_Client(), _CorrectedClient()]
    registry = ProviderRegistry()

    def factory(config: object) -> AlpacaPriceProvider:
        return AlpacaPriceProvider(AlpacaProviderConfig(), clients.pop(0))  # type: ignore[arg-type]

    registry.register_price_provider("alpaca", factory)  # type: ignore[arg-type]
    first = _ctx(tmp_path, "first")
    second = _ctx(tmp_path, "second")
    second.cfg["cache"]["force_refresh"] = True
    alpaca_historical_bars_task(first, _params(), registry=registry, clock=lambda: NOW)
    alpaca_historical_bars_task(second, _params(), registry=registry, clock=lambda: NOW)
    summary = json.loads(
        (second.artifacts_dir / "alpaca_store_summary.json").read_text(encoding="utf-8")
    )
    assert summary["inserted"] == 0
    assert summary["updated"] == 1
    assert summary["skipped"] == 1


def test_invalid_request_fails_before_provider_creation(tmp_path: Path) -> None:
    created: list[_Client] = []
    with pytest.raises(ConfigError, match="missing required"):
        alpaca_historical_bars_task(
            _ctx(tmp_path),
            _params(feed=""),
            registry=_registry(created),
            clock=lambda: NOW,
        )
    assert created == []


def test_unknown_provider_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing provider configuration"):
        alpaca_historical_bars_task(
            _ctx(tmp_path),
            {**_params(), "provider": "missing"},
            registry=ProviderRegistry(),
            clock=lambda: NOW,
        )


def test_store_lock_conflict_is_surfaced(tmp_path: Path) -> None:
    root = tmp_path / "market"
    root.mkdir()
    lock = root / "market_bars.jsonl.lock"
    lock.write_text("other-writer\n", encoding="utf-8")
    with pytest.raises(ConcurrentDatasetWriteError):
        alpaca_historical_bars_task(
            _ctx(tmp_path),
            _params(),
            registry=_registry([]),
            clock=lambda: NOW,
        )
    assert lock.read_text(encoding="utf-8") == "other-writer\n"

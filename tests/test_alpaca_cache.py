"""Cache behavior tests for the Alpaca provider adapter."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from common import cache
from common.cache import CachePolicy, make_cache_key
from market_data.domain.requests import BarsRequest
from market_data.providers.alpaca.client import AlpacaRawPages
from market_data.providers.alpaca.config import AlpacaProviderConfig, normalize_api_origin
from market_data.providers.alpaca.provider import (
    ALPACA_CACHE_NAMESPACE,
    ALPACA_CACHE_SCHEMA_VERSION,
    AlpacaPriceProvider,
    build_alpaca_cache_payload,
)
from market_data.providers.alpaca.raw_models import parse_alpaca_bars_page

FIXTURES = Path(__file__).parent / "fixtures" / "alpaca"
NOW = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)


def _payload(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _request(**overrides: object) -> BarsRequest:
    values: dict[str, object] = {
        "symbols": ("MSFT", "AAPL"),
        "timeframe": "1Day",
        "start": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "end": datetime(2026, 6, 30, tzinfo=timezone.utc),
        "feed": "iex",
        "adjustment": "all",
    }
    values.update(overrides)
    return BarsRequest(**values)  # type: ignore[arg-type]


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
            retry_count=0,
        )

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")


def _provider(
    client: _Client,
    *,
    base_url: str = "https://data.alpaca.markets",
) -> AlpacaPriceProvider:
    return AlpacaPriceProvider(
        AlpacaProviderConfig(base_url=base_url),
        client,  # type: ignore[arg-type]
    )


def test_cache_miss_fetches_all_pages_and_writes_raw_result() -> None:
    client = _Client()
    result = _provider(client).fetch_bars(
        _request(),
        cache_policy=CachePolicy(schema_version=ALPACA_CACHE_SCHEMA_VERSION),
        retrieved_at=NOW,
    )
    assert result.cache_status == "miss"
    assert result.cache_written is True
    assert client.calls == 1
    assert [bar.symbol for bar in result.bars] == ["AAPL", "MSFT"]
    assert all(bar.currency == "USD" for bar in result.bars)


def test_cache_hit_avoids_http_and_preserves_pagination() -> None:
    request = _request()
    policy = CachePolicy(schema_version=ALPACA_CACHE_SCHEMA_VERSION)
    first_client = _Client()
    _provider(first_client).fetch_bars(request, cache_policy=policy, retrieved_at=NOW)
    second_client = _Client()
    result = _provider(second_client).fetch_bars(
        request,
        cache_policy=policy,
        retrieved_at=NOW + timedelta(days=1),
    )
    assert result.cache_status == "hit"
    assert result.cache_written is False
    assert second_client.calls == 0
    assert len(result.pages) == 2
    assert [bar.symbol for bar in result.bars] == ["AAPL", "MSFT"]
    assert result.retrieved_at == NOW


def test_expired_cache_invokes_client() -> None:
    request = _request()
    policy = CachePolicy(ttl_seconds=60, schema_version=ALPACA_CACHE_SCHEMA_VERSION)
    first = _provider(_Client()).fetch_bars(request, cache_policy=policy, retrieved_at=NOW)
    path = cache.CACHE_ROOT / ALPACA_CACHE_NAMESPACE / f"{first.cache_key}.json"
    old_time = path.stat().st_mtime - 120
    os.utime(path, (old_time, old_time))
    client = _Client()
    result = _provider(client).fetch_bars(
        request, cache_policy=policy, retrieved_at=NOW + timedelta(days=1)
    )
    assert result.cache_status == "expired"
    assert result.cache_written is True
    assert client.calls == 1


def test_force_refresh_invokes_client() -> None:
    request = _request()
    normal = CachePolicy(schema_version=ALPACA_CACHE_SCHEMA_VERSION)
    _provider(_Client()).fetch_bars(request, cache_policy=normal, retrieved_at=NOW)
    client = _Client()
    result = _provider(client).fetch_bars(
        request,
        cache_policy=CachePolicy(force_refresh=True, schema_version=ALPACA_CACHE_SCHEMA_VERSION),
        retrieved_at=NOW,
    )
    assert result.cache_status == "force_refresh"
    assert client.calls == 1


def test_cache_payload_is_stable_and_excludes_credentials() -> None:
    config = AlpacaProviderConfig()
    payload = build_alpaca_cache_payload(_request(), config)
    reversed_payload = dict(reversed(list(payload.items())))
    assert make_cache_key(payload) == make_cache_key(reversed_payload)
    serialized = json.dumps(payload)
    assert "fixture-key" not in serialized
    assert "secret" not in serialized
    assert "key_id_env" not in serialized
    assert payload["api_origin"] == "https://data.alpaca.markets"
    assert payload["currency"] == "USD"


def test_response_affecting_request_values_change_cache_payload() -> None:
    config = AlpacaProviderConfig()
    base = build_alpaca_cache_payload(_request(), config)
    assert base != build_alpaca_cache_payload(_request(feed="sip"), config)
    assert base != build_alpaca_cache_payload(_request(adjustment="raw"), config)
    assert base != build_alpaca_cache_payload(
        _request(end=datetime(2026, 6, 29, tzinfo=timezone.utc)),
        config,
    )
    assert base != build_alpaca_cache_payload(_request(currency="CAD"), config)


def test_different_api_origins_do_not_share_cache_entries() -> None:
    request = _request()
    policy = CachePolicy(schema_version=ALPACA_CACHE_SCHEMA_VERSION)
    production = _provider(_Client(), base_url="https://data.alpaca.markets")
    sandbox = _provider(_Client(), base_url="https://data.sandbox.alpaca.markets")
    first = production.fetch_bars(request, cache_policy=policy, retrieved_at=NOW)
    second = sandbox.fetch_bars(request, cache_policy=policy, retrieved_at=NOW)
    assert first.cache_key != second.cache_key
    assert second.cache_status == "miss"


def test_equivalent_normalized_base_urls_share_cache_identity() -> None:
    request = _request()
    left = build_alpaca_cache_payload(
        request, AlpacaProviderConfig(base_url="https://data.alpaca.markets/")
    )
    right = build_alpaca_cache_payload(
        request, AlpacaProviderConfig(base_url="HTTPS://DATA.ALPACA.MARKETS:443")
    )
    assert left == right
    assert normalize_api_origin("https://data.alpaca.markets/") == ("https://data.alpaca.markets")


def test_omitted_and_explicit_usd_share_cache_identity() -> None:
    config = AlpacaProviderConfig()
    assert build_alpaca_cache_payload(_request(), config) == build_alpaca_cache_payload(
        _request(currency="USD"), config
    )

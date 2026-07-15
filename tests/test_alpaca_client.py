"""Offline tests for the Alpaca historical bars HTTP client."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from market_data.domain.requests import BarsRequest
from market_data.providers.alpaca.client import AlpacaBarsClient
from market_data.providers.alpaca.config import (
    AlpacaCredentials,
    AlpacaProviderConfig,
    resolve_credentials,
)
from market_data.providers.alpaca.errors import (
    AlpacaAuthenticationError,
    AlpacaConfigError,
    AlpacaInvalidRequestError,
    AlpacaMaximumPagesError,
    AlpacaPaginationLoopError,
    AlpacaPayloadError,
    AlpacaPermissionError,
    AlpacaRateLimitError,
    AlpacaTransientError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "alpaca"
NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _request(**overrides: object) -> BarsRequest:
    values: dict[str, object] = {
        "symbols": ("MSFT", "AAPL"),
        "timeframe": "1Day",
        "start": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "end": datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc),
        "feed": "iex",
        "adjustment": "all",
        "limit": 1000,
        "sort": "asc",
    }
    values.update(overrides)
    return BarsRequest(**values)  # type: ignore[arg-type]


class _Response:
    def __init__(
        self,
        status: int,
        payload: object = None,
        *,
        headers: Mapping[str, str] | None = None,
        json_error: Exception | None = None,
    ) -> None:
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = json.dumps(payload)
        self.reason = "test response"
        self._json_error = json_error

    def json(self) -> object:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _Transport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []
        self.closed = False

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, object],
        timeout: float,
    ) -> _Response:
        self.calls.append(
            {"url": url, "headers": dict(headers), "params": dict(params), "timeout": timeout}
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        assert isinstance(response, _Response)
        return response

    def close(self) -> None:
        self.closed = True


def _client(
    responses: list[object],
    *,
    config: AlpacaProviderConfig | None = None,
    sleeps: list[float] | None = None,
) -> tuple[AlpacaBarsClient, _Transport]:
    transport = _Transport(responses)
    sleep_values = sleeps if sleeps is not None else []
    client = AlpacaBarsClient(
        config or AlpacaProviderConfig(initial_backoff_seconds=1, maximum_backoff_seconds=10),
        AlpacaCredentials("fixture-key-id", "fixture-secret-key"),
        transport=transport,
        sleep=sleep_values.append,
    )
    return client, transport


def test_request_uses_endpoint_headers_parameters_and_timeout() -> None:
    client, transport = _client([_Response(200, _fixture("one_page_multi_symbol.json"))])
    result = client.fetch_pages(_request())
    assert len(result.pages) == 1
    call = transport.calls[0]
    assert call["url"] == "https://data.alpaca.markets/v2/stocks/bars"
    assert set(call["headers"]) == {"APCA-API-KEY-ID", "APCA-API-SECRET-KEY"}
    assert call["params"] == {
        "symbols": "AAPL,MSFT",
        "timeframe": "1Day",
        "start": "2026-06-01T00:00:00Z",
        "end": "2026-06-30T23:59:59Z",
        "limit": 1000,
        "adjustment": "all",
        "feed": "iex",
        "sort": "asc",
    }
    assert call["timeout"] == 30.0


def test_optional_asof_currency_and_page_token_are_serialized() -> None:
    client, transport = _client(
        [
            _Response(200, _fixture("two_page_first.json")),
            _Response(200, _fixture("two_page_second.json")),
        ]
    )
    request = _request(asof=datetime(2026, 6, 1).date(), currency="usd")
    result = client.fetch_pages(request)
    assert len(result.pages) == 2
    assert transport.calls[0]["params"]["asof"] == "2026-06-01"  # type: ignore[index]
    assert transport.calls[0]["params"]["currency"] == "USD"  # type: ignore[index]
    assert "page_token" not in transport.calls[0]["params"]  # type: ignore[operator]
    assert transport.calls[1]["params"]["page_token"] == "page-two"  # type: ignore[index]


def test_pagination_keeps_symbol_returned_only_on_later_page() -> None:
    client, _ = _client(
        [
            _Response(200, _fixture("two_page_first.json")),
            _Response(200, _fixture("two_page_second.json")),
        ]
    )
    result = client.fetch_pages(_request())
    assert [page.symbols for page in result.pages] == [("AAPL",), ("MSFT",)]
    assert result.pages[-1].next_page_token is None


def test_repeated_pagination_token_fails() -> None:
    repeated = _fixture("two_page_first.json")
    client, _ = _client([_Response(200, repeated), _Response(200, repeated)])
    with pytest.raises(AlpacaPaginationLoopError, match="repeated"):
        client.fetch_pages(_request())


def test_maximum_page_guard_fails_before_extra_request() -> None:
    config = AlpacaProviderConfig(maximum_pages=1)
    client, transport = _client([_Response(200, _fixture("two_page_first.json"))], config=config)
    with pytest.raises(AlpacaMaximumPagesError, match="maximum_pages=1"):
        client.fetch_pages(_request())
    assert len(transport.calls) == 1


def test_429_honors_retry_after_then_succeeds() -> None:
    sleeps: list[float] = []
    client, _ = _client(
        [
            _Response(429, _fixture("http_errors.json")["429"], headers={"Retry-After": "2.5"}),  # type: ignore[index]
            _Response(200, _fixture("empty.json")),
        ],
        sleeps=sleeps,
    )
    result = client.fetch_pages(_request())
    assert result.retry_count == 1
    assert sleeps == [2.5]


def test_500_retries_with_bounded_backoff() -> None:
    sleeps: list[float] = []
    client, _ = _client(
        [_Response(500, {"message": "temporary"}), _Response(200, _fixture("empty.json"))],
        sleeps=sleeps,
    )
    assert client.fetch_pages(_request()).retry_count == 1
    assert sleeps == [1]


def test_temporary_transport_error_retries() -> None:
    client, _ = _client([requests.Timeout("temporary"), _Response(200, _fixture("empty.json"))])
    assert client.fetch_pages(_request()).retry_count == 1


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (400, AlpacaInvalidRequestError),
        (401, AlpacaAuthenticationError),
        (403, AlpacaPermissionError),
        (404, AlpacaInvalidRequestError),
        (422, AlpacaInvalidRequestError),
    ],
)
def test_non_retryable_http_errors_are_not_retried(
    status: int, error_type: type[Exception]
) -> None:
    errors = _fixture("http_errors.json")
    payload = errors.get(str(status), {"message": "request failed"})  # type: ignore[union-attr]
    client, transport = _client([_Response(status, payload)])
    with pytest.raises(error_type):
        client.fetch_pages(_request())
    assert len(transport.calls) == 1


def test_retry_exhaustion_uses_specific_errors() -> None:
    config = AlpacaProviderConfig(maximum_attempts=2, initial_backoff_seconds=0)
    rate_client, _ = _client([_Response(429, {}), _Response(429, {})], config=config)
    with pytest.raises(AlpacaRateLimitError):
        rate_client.fetch_pages(_request())
    server_client, _ = _client([_Response(503, {}), _Response(503, {})], config=config)
    with pytest.raises(AlpacaTransientError):
        server_client.fetch_pages(_request())


def test_invalid_json_and_malformed_payload_fail() -> None:
    invalid_client, _ = _client([_Response(200, json_error=ValueError("invalid JSON"))])
    with pytest.raises(AlpacaPayloadError, match="invalid JSON"):
        invalid_client.fetch_pages(_request())
    malformed_client, _ = _client([_Response(200, _fixture("malformed_top_level.json"))])
    with pytest.raises(AlpacaPayloadError, match="JSON object"):
        malformed_client.fetch_pages(_request())


def test_invalid_request_is_rejected_before_transport() -> None:
    client, transport = _client([])
    with pytest.raises(AlpacaInvalidRequestError, match="timeframe"):
        client.fetch_pages(_request(timeframe="banana"))
    assert transport.calls == []


def test_missing_credentials_fail_before_client_construction() -> None:
    config = AlpacaProviderConfig()
    with pytest.raises(AlpacaConfigError, match="ALPACA_API_KEY_ID"):
        resolve_credentials(config, {})


def test_credentials_do_not_appear_in_representations_or_errors() -> None:
    credentials = AlpacaCredentials("visible-id-if-bug", "visible-secret-if-bug")
    client = AlpacaBarsClient(
        AlpacaProviderConfig(maximum_attempts=1),
        credentials,
        transport=_Transport([_Response(401, {"message": "rejected"})]),
    )
    assert "visible-id-if-bug" not in repr(credentials)
    assert "visible-secret-if-bug" not in repr(credentials)
    assert "visible-id-if-bug" not in repr(client)
    with pytest.raises(AlpacaAuthenticationError) as error:
        client.fetch_pages(_request())
    assert "visible-id-if-bug" not in str(error.value)
    assert "visible-secret-if-bug" not in str(error.value)

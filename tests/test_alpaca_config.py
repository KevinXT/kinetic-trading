"""Strict Alpaca configuration parsing tests."""

from datetime import datetime, timezone

import pytest
from common.errors import ConfigError
from market_data.providers.alpaca.config import (
    AlpacaProviderConfig,
    normalize_api_origin,
    parse_strict_float,
    parse_strict_int,
)
from market_data.providers.alpaca.errors import AlpacaConfigError
from market_data.providers.alpaca.task import resolve_bars_request


def test_from_mapping_accepts_yaml_numbers_and_env_style_strings() -> None:
    config = AlpacaProviderConfig.from_mapping(
        {
            "base_url": "https://data.alpaca.markets/",
            "timeout_seconds": 30,
            "maximum_attempts": "4",
            "initial_backoff_seconds": "1.5",
            "maximum_backoff_seconds": 30.0,
            "maximum_pages": 100,
        }
    )
    assert config.base_url == "https://data.alpaca.markets"
    assert config.timeout_seconds == 30.0
    assert config.maximum_attempts == 4
    assert config.initial_backoff_seconds == 1.5
    assert config.maximum_pages == 100


@pytest.mark.parametrize(
    ("value",),
    [
        (30,),
        ("30",),
    ],
)
def test_parse_strict_int_accepts_int_and_digit_string(value: object) -> None:
    assert parse_strict_int(value, "field", minimum=1) == 30


@pytest.mark.parametrize(
    ("value",),
    [
        (True,),
        (False,),
        (30.5,),
        ("30.5",),
        ("thirty",),
        ("",),
        (None,),
    ],
)
def test_parse_strict_int_rejects_invalid_values(value: object) -> None:
    with pytest.raises(AlpacaConfigError, match="field"):
        parse_strict_int(value, "field", minimum=1)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (30, 30.0),
        (30.0, 30.0),
        ("30", 30.0),
        ("30.0", 30.0),
    ],
)
def test_parse_strict_float_accepts_numeric_forms(value: object, expected: float) -> None:
    assert parse_strict_float(value, "field", exclusive_minimum=0.0) == expected


@pytest.mark.parametrize(
    ("value",),
    [
        (True,),
        (False,),
        ("thirty",),
        ("",),
        (None,),
        (float("nan"),),
        (float("inf"),),
        (float("-inf"),),
    ],
)
def test_parse_strict_float_rejects_invalid_values(value: object) -> None:
    with pytest.raises(AlpacaConfigError, match="field"):
        parse_strict_float(value, "field", exclusive_minimum=0.0)


def test_rejects_boolean_where_integer_expected() -> None:
    with pytest.raises(AlpacaConfigError, match="maximum_attempts"):
        AlpacaProviderConfig.from_mapping({"maximum_attempts": True})
    with pytest.raises(AlpacaConfigError, match="maximum_pages"):
        AlpacaProviderConfig.from_mapping({"maximum_pages": False})


def test_rejects_fractional_values_for_integers() -> None:
    with pytest.raises(AlpacaConfigError, match="maximum_attempts"):
        AlpacaProviderConfig.from_mapping({"maximum_attempts": 1.5})
    with pytest.raises(AlpacaConfigError, match="maximum_pages"):
        parse_strict_int("2.0", "maximum_pages", minimum=1)


def test_rejects_zero_or_negative_limits_and_timeouts() -> None:
    with pytest.raises(AlpacaConfigError, match="timeout_seconds"):
        AlpacaProviderConfig.from_mapping({"timeout_seconds": 0})
    with pytest.raises(AlpacaConfigError, match="maximum_pages"):
        AlpacaProviderConfig.from_mapping({"maximum_pages": 0})
    with pytest.raises(AlpacaConfigError, match="maximum_attempts"):
        AlpacaProviderConfig.from_mapping({"maximum_attempts": -1})


def test_rejects_invalid_backoff_relationship() -> None:
    with pytest.raises(AlpacaConfigError, match="initial_backoff_seconds"):
        AlpacaProviderConfig.from_mapping(
            {
                "initial_backoff_seconds": 40,
                "maximum_backoff_seconds": 30,
            }
        )


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("https://data.alpaca.markets", "https://data.alpaca.markets/"),
        ("https://data.alpaca.markets", "HTTPS://DATA.ALPACA.MARKETS"),
        ("https://data.alpaca.markets", "https://data.alpaca.markets:443"),
        ("http://localhost:8080/alpaca", "http://localhost:8080/alpaca/"),
    ],
)
def test_normalize_api_origin_equates_safe_variants(left: str, right: str) -> None:
    assert normalize_api_origin(left) == normalize_api_origin(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("https://data.alpaca.markets", "https://data.sandbox.alpaca.markets"),
        ("https://example.com/alpaca", "https://example.com/other-api"),
        ("http://localhost:8080/alpaca", "http://localhost:8081/alpaca"),
        ("http://localhost:8080/alpaca", "http://localhost:8080/other"),
    ],
)
def test_normalize_api_origin_distinguishes_material_differences(left: str, right: str) -> None:
    assert normalize_api_origin(left) != normalize_api_origin(right)


def test_normalize_api_origin_rejects_credentials_query_and_fragment() -> None:
    with pytest.raises(AlpacaConfigError, match="must not contain credentials"):
        normalize_api_origin("https://key:secret@data.alpaca.markets")
    with pytest.raises(AlpacaConfigError, match="query string"):
        normalize_api_origin("https://data.alpaca.markets?api_key=secret")
    with pytest.raises(AlpacaConfigError, match="fragment"):
        normalize_api_origin("https://data.alpaca.markets#section")
    with pytest.raises(AlpacaConfigError, match="absolute http"):
        normalize_api_origin("not-a-url")


def test_resolve_bars_request_rejects_boolean_and_fractional_limit() -> None:
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    base = {
        "request": {
            "symbols": ["AAPL"],
            "timeframe": "1Day",
            "start": "2026-06-01T00:00:00Z",
            "end": "2026-06-30T23:59:59Z",
            "feed": "iex",
            "adjustment": "all",
        }
    }
    with pytest.raises(ConfigError, match="limit"):
        resolve_bars_request(
            {**base, "request": {**base["request"], "limit": True}}, resolved_now=now
        )
    with pytest.raises(ConfigError, match="limit"):
        resolve_bars_request(
            {**base, "request": {**base["request"], "limit": 1.5}},
            resolved_now=now,
        )
    request = resolve_bars_request(
        {**base, "request": {**base["request"], "limit": "500"}},
        resolved_now=now,
    )
    assert request.limit == 500

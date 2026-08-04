"""Tests for price provider construction."""

from typing import Any, Mapping

import pytest
from common.errors import ConfigError
from market_data.providers.registry import ProviderRegistry


class _PriceProvider:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = config

    def get_bars(self, request: object) -> list[object]:
        return []


def test_registry_constructs_configured_provider_case_insensitively() -> None:
    registry = ProviderRegistry()
    registry.register_price_provider("Alpaca", _PriceProvider)
    provider = registry.create_price_provider("alpaca", {"feed": "iex"})
    assert isinstance(provider, _PriceProvider)
    assert provider.config == {"feed": "iex"}


def test_duplicate_registration_is_rejected() -> None:
    registry = ProviderRegistry()
    registry.register_price_provider("alpaca", _PriceProvider)
    with pytest.raises(ConfigError, match="already registered"):
        registry.register_price_provider("ALPACA", _PriceProvider)


def test_unknown_provider_lists_available_names() -> None:
    registry = ProviderRegistry()
    registry.register_price_provider("alpaca", _PriceProvider)
    with pytest.raises(ConfigError, match="available: alpaca"):
        registry.create_price_provider("missing", {})


def test_registry_instances_do_not_share_mutable_state() -> None:
    first = ProviderRegistry()
    second = ProviderRegistry()
    first.register_price_provider("alpaca", _PriceProvider)
    with pytest.raises(ConfigError, match="available: none"):
        second.create_price_provider("alpaca", {})

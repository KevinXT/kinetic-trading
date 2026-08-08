"""
Category-specific provider factory registry.

Only price providers are registered today (Alpaca). Company/macro provider
factories will be added when those integrations exist — not as empty slots.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast

from kinetic.core.errors import ConfigError
from kinetic.ingestion.protocols import PriceDataProvider

ProviderConfig = Mapping[str, Any]
ProviderFactory = Callable[[ProviderConfig], object]


class ProviderRegistry:
    """Construct configured price providers without exposing implementations."""

    def __init__(self) -> None:
        self._price: dict[str, ProviderFactory] = {}

    @staticmethod
    def _name(name: str) -> str:
        normalized = name.strip().lower()
        if not normalized:
            raise ConfigError("provider name must not be empty")
        return normalized

    def register_price_provider(
        self, name: str, factory: Callable[[ProviderConfig], PriceDataProvider]
    ) -> None:
        normalized = self._name(name)
        if normalized in self._price:
            raise ConfigError(f"price provider {normalized!r} is already registered")
        self._price[normalized] = cast(ProviderFactory, factory)

    def create_price_provider(self, name: str, config: ProviderConfig) -> PriceDataProvider:
        normalized = self._name(name)
        try:
            factory = self._price[normalized]
        except KeyError as exc:
            available = ", ".join(sorted(self._price)) or "none"
            raise ConfigError(
                f"unknown price provider {normalized!r}; available: {available}"
            ) from exc
        return cast(PriceDataProvider, factory(config))

"""Category-specific provider factory registry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar, cast

from common.errors import ConfigError

from market_data.providers.protocols import (
    CompanyDataProvider,
    MacroDataProvider,
    PriceDataProvider,
)

ProviderConfig = Mapping[str, Any]
ProviderT = TypeVar("ProviderT")
ProviderFactory = Callable[[ProviderConfig], object]


class ProviderRegistry:
    """Construct configured providers without exposing implementations downstream."""

    def __init__(self) -> None:
        self._price: dict[str, ProviderFactory] = {}
        self._company: dict[str, ProviderFactory] = {}
        self._macro: dict[str, ProviderFactory] = {}

    @staticmethod
    def _name(name: str) -> str:
        normalized = name.strip().lower()
        if not normalized:
            raise ConfigError("provider name must not be empty")
        return normalized

    def _register(
        self,
        registry: dict[str, ProviderFactory],
        category: str,
        name: str,
        factory: ProviderFactory,
    ) -> None:
        normalized = self._name(name)
        if normalized in registry:
            raise ConfigError(f"{category} provider {normalized!r} is already registered")
        registry[normalized] = factory

    def _create(
        self,
        registry: dict[str, ProviderFactory],
        category: str,
        name: str,
        config: ProviderConfig,
    ) -> object:
        normalized = self._name(name)
        try:
            factory = registry[normalized]
        except KeyError as exc:
            available = ", ".join(sorted(registry)) or "none"
            raise ConfigError(
                f"unknown {category} provider {normalized!r}; available: {available}"
            ) from exc
        return factory(config)

    def register_price_provider(
        self, name: str, factory: Callable[[ProviderConfig], PriceDataProvider]
    ) -> None:
        self._register(self._price, "price", name, cast(ProviderFactory, factory))

    def register_company_provider(
        self, name: str, factory: Callable[[ProviderConfig], CompanyDataProvider]
    ) -> None:
        self._register(self._company, "company", name, cast(ProviderFactory, factory))

    def register_macro_provider(
        self, name: str, factory: Callable[[ProviderConfig], MacroDataProvider]
    ) -> None:
        self._register(self._macro, "macro", name, cast(ProviderFactory, factory))

    def create_price_provider(self, name: str, config: ProviderConfig) -> PriceDataProvider:
        return cast(PriceDataProvider, self._create(self._price, "price", name, config))

    def create_company_provider(self, name: str, config: ProviderConfig) -> CompanyDataProvider:
        return cast(CompanyDataProvider, self._create(self._company, "company", name, config))

    def create_macro_provider(self, name: str, config: ProviderConfig) -> MacroDataProvider:
        return cast(MacroDataProvider, self._create(self._macro, "macro", name, config))

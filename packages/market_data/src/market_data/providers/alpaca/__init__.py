"""Alpaca historical stock-bars provider."""

from market_data.providers.alpaca.client import AlpacaBarsClient, AlpacaRawPages
from market_data.providers.alpaca.config import (
    AlpacaCredentials,
    AlpacaProviderConfig,
    resolve_credentials,
)
from market_data.providers.alpaca.normalize import normalize_alpaca_bars
from market_data.providers.alpaca.provider import (
    ALPACA_CACHE_SCHEMA_VERSION,
    AlpacaFetchResult,
    AlpacaPriceProvider,
    build_alpaca_cache_payload,
    create_alpaca_price_provider,
    safe_request_summary,
)
from market_data.providers.alpaca.raw_models import (
    AlpacaBarsPage,
    AlpacaRawBar,
    parse_alpaca_bars_page,
)

__all__ = [
    "ALPACA_CACHE_SCHEMA_VERSION",
    "AlpacaBarsClient",
    "AlpacaBarsPage",
    "AlpacaCredentials",
    "AlpacaFetchResult",
    "AlpacaPriceProvider",
    "AlpacaProviderConfig",
    "AlpacaRawBar",
    "AlpacaRawPages",
    "build_alpaca_cache_payload",
    "create_alpaca_price_provider",
    "normalize_alpaca_bars",
    "parse_alpaca_bars_page",
    "resolve_credentials",
    "safe_request_summary",
]

"""Alpaca price-provider adapter with raw-response caching."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from kinetic.data.schemas.market import PriceBar
from kinetic.ingestion.caching import CachePolicy, get_or_fetch_json_result
from kinetic.ingestion.market.alpaca.client import AlpacaBarsClient
from kinetic.ingestion.market.alpaca.config import AlpacaProviderConfig, resolve_credentials
from kinetic.ingestion.market.alpaca.errors import AlpacaPayloadError
from kinetic.ingestion.market.alpaca.normalize import normalize_alpaca_bars, resolve_bar_currency
from kinetic.ingestion.market.alpaca.raw_models import AlpacaBarsPage, parse_alpaca_bars_page
from kinetic.ingestion.requests import BarsRequest

ALPACA_CACHE_NAMESPACE = "alpaca_historical_stock_bars"
# v2 includes normalized api_origin so sandbox/prod/mock hosts cannot share entries.
ALPACA_CACHE_SCHEMA_VERSION = "alpaca-bars-v2"


def _rfc3339(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def safe_request_summary(
    request: BarsRequest,
    config: AlpacaProviderConfig,
) -> dict[str, object]:
    result: dict[str, object] = {
        "provider": "alpaca",
        "operation": "historical_stock_bars",
        "api_origin": config.api_origin,
        "symbols": list(request.symbols),
        "timeframe": request.timeframe,
        "start": _rfc3339(request.start),
        "end": _rfc3339(request.end),
        "feed": request.feed,
        "adjustment": request.adjustment,
        "limit": request.limit,
        "sort": request.sort,
        "currency": resolve_bar_currency(request),
        "key_id_env": config.key_id_env,
        "secret_key_env": config.secret_key_env,
    }
    if request.asof is not None:
        result["asof"] = request.asof.isoformat()
    return result


def build_alpaca_cache_payload(
    request: BarsRequest,
    config: AlpacaProviderConfig,
) -> dict[str, object]:
    """Build a credential-free key for one complete paginated raw result."""
    payload: dict[str, object] = {
        "provider": "alpaca",
        "endpoint": "historical_stock_bars",
        "api_origin": config.api_origin,
        "symbols": list(request.symbols),
        "timeframe": request.timeframe,
        "start": _rfc3339(request.start),
        "end": _rfc3339(request.end),
        "feed": request.feed,
        "adjustment": request.adjustment,
        "limit": request.limit,
        "sort": request.sort,
        # Always stamp currency so omitted vs explicit USD share one cache identity.
        "currency": resolve_bar_currency(request),
    }
    if request.asof is not None:
        payload["asof"] = request.asof.isoformat()
    return payload


@dataclass(frozen=True, slots=True)
class AlpacaFetchResult:
    bars: tuple[PriceBar, ...]
    pages: tuple[AlpacaBarsPage, ...]
    cache_status: str
    cache_written: bool
    cache_key: str
    retry_count: int
    retrieved_at: datetime

    @property
    def page_record_counts(self) -> tuple[int, ...]:
        return tuple(page.record_count for page in self.pages)


class AlpacaPriceProvider:
    """Provider adapter returning only normalized `PriceBar` records."""

    def __init__(
        self,
        config: AlpacaProviderConfig,
        client: AlpacaBarsClient,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.config = config
        self._client = client
        self._clock = clock

    def close(self) -> None:
        self._client.close()

    def get_bars(self, request: BarsRequest) -> Sequence[PriceBar]:
        return self.fetch_bars(
            request,
            cache_policy=CachePolicy(schema_version=ALPACA_CACHE_SCHEMA_VERSION),
            retrieved_at=self._clock(),
        ).bars

    def fetch_bars(
        self,
        request: BarsRequest,
        *,
        cache_policy: CachePolicy,
        retrieved_at: datetime,
    ) -> AlpacaFetchResult:
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        retrieved_at = retrieved_at.astimezone(timezone.utc)
        cache_payload = build_alpaca_cache_payload(request, self.config)

        def fetch_raw() -> dict[str, Any]:
            raw = self._client.fetch_pages(request)
            return {
                "pages": [page.raw_payload for page in raw.pages],
                "retrieved_at": _rfc3339(retrieved_at),
                "retry_count": raw.retry_count,
            }

        cached = get_or_fetch_json_result(
            namespace=ALPACA_CACHE_NAMESPACE,
            payload=cache_payload,
            fetch_fn=fetch_raw,
            policy=cache_policy,
        )
        raw_pages = cached.data.get("pages")
        if not isinstance(raw_pages, list):
            raise AlpacaPayloadError("cached Alpaca result field 'pages' must be a list")
        pages = tuple(parse_alpaca_bars_page(page) for page in raw_pages)

        raw_retrieved_at = cached.data.get("retrieved_at")
        if not isinstance(raw_retrieved_at, str):
            raise AlpacaPayloadError(
                "cached Alpaca result field 'retrieved_at' must be a timestamp string"
            )
        text = (
            raw_retrieved_at[:-1] + "+00:00" if raw_retrieved_at.endswith("Z") else raw_retrieved_at
        )
        try:
            normalized_retrieved_at = datetime.fromisoformat(text)
        except ValueError as exc:
            raise AlpacaPayloadError("cached Alpaca retrieved_at is not a valid timestamp") from exc
        if normalized_retrieved_at.tzinfo is None or normalized_retrieved_at.utcoffset() is None:
            raise AlpacaPayloadError("cached Alpaca retrieved_at must be timezone-aware")
        normalized_retrieved_at = normalized_retrieved_at.astimezone(timezone.utc)

        retry_count = cached.data.get("retry_count", 0)
        if isinstance(retry_count, bool) or not isinstance(retry_count, int):
            raise AlpacaPayloadError("cached Alpaca retry_count must be an integer")
        bars = normalize_alpaca_bars(
            pages,
            request=request,
            retrieved_at=normalized_retrieved_at,
        )
        return AlpacaFetchResult(
            bars=bars,
            pages=pages,
            cache_status=cached.status,
            cache_written=cached.written,
            cache_key=cached.key,
            retry_count=retry_count,
            retrieved_at=normalized_retrieved_at,
        )


def create_alpaca_price_provider(
    config: Mapping[str, Any],
) -> AlpacaPriceProvider:
    provider_config = AlpacaProviderConfig.from_mapping(config)
    credentials = resolve_credentials(provider_config)
    client = AlpacaBarsClient(provider_config, credentials)
    return AlpacaPriceProvider(provider_config, client)

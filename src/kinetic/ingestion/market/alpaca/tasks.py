"""Thin pipeline task for Alpaca historical stock bars."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from kinetic.core.errors import ConfigError
from kinetic.data.serialization import record_to_dict
from kinetic.data.storage import (
    FINANCIAL_DATA_SCHEMA_VERSION,
    FinancialDataStore,
    JsonlFinancialDataStore,
)
from kinetic.ingestion.caching import CachePolicy
from kinetic.ingestion.market.alpaca.client import validate_alpaca_request
from kinetic.ingestion.market.alpaca.config import (
    AlpacaProviderConfig,
    parse_strict_int,
)
from kinetic.ingestion.market.alpaca.errors import AlpacaConfigError
from kinetic.ingestion.market.alpaca.provider import (
    ALPACA_CACHE_SCHEMA_VERSION,
    AlpacaPriceProvider,
    create_alpaca_price_provider,
    safe_request_summary,
)
from kinetic.ingestion.registry import ProviderRegistry
from kinetic.ingestion.requests import BarsRequest

JsonMapping = Mapping[str, Any]
logger = logging.getLogger(__name__)


def create_price_provider_registry() -> ProviderRegistry:
    """Create a fresh registry so provider registration is not global mutable state."""
    registry = ProviderRegistry()
    registry.register_price_provider("alpaca", create_alpaca_price_provider)
    return registry


def _mapping(value: object, location: str) -> JsonMapping:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{location} must be a mapping")
    return cast(JsonMapping, value)


def _datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ConfigError(f"Alpaca request {field_name} must be an RFC 3339 string")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ConfigError(
            f"Alpaca request {field_name} must be a valid RFC 3339 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConfigError(f"Alpaca request {field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _date(value: object, field_name: str) -> date:
    if not isinstance(value, str):
        raise ConfigError(f"Alpaca request {field_name} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"Alpaca request {field_name} must be a valid ISO date") from exc


def resolve_bars_request(
    params: JsonMapping,
    *,
    resolved_now: datetime,
) -> BarsRequest:
    request_config = _mapping(params.get("request"), "pipeline.ingest.request")
    symbols = request_config.get("symbols")
    if not isinstance(symbols, list) or not all(isinstance(item, str) for item in symbols):
        raise ConfigError("Alpaca request symbols must be a list of strings")

    explicit_start = request_config.get("start")
    explicit_end = request_config.get("end")
    lookback_days = request_config.get("lookback_days")
    if explicit_start is not None or explicit_end is not None:
        if explicit_start is None or explicit_end is None or lookback_days is not None:
            raise ConfigError(
                "Alpaca request requires start and end together, without lookback_days"
            )
        start = _datetime(explicit_start, "start")
        end = _datetime(explicit_end, "end")
    elif lookback_days is not None:
        if (
            isinstance(lookback_days, bool)
            or not isinstance(lookback_days, int)
            or lookback_days < 1
        ):
            raise ConfigError("Alpaca request lookback_days must be a positive integer")
        if resolved_now.tzinfo is None or resolved_now.utcoffset() is None:
            raise ValueError("resolved_now must be timezone-aware")
        end = resolved_now.astimezone(timezone.utc)
        start = end - timedelta(days=lookback_days)
    else:
        raise ConfigError("Alpaca request requires start/end or lookback_days")

    required_text = ("timeframe", "feed", "adjustment")
    missing = [field for field in required_text if not request_config.get(field)]
    if missing:
        raise ConfigError("Alpaca request is missing required field(s): " + ", ".join(missing))
    try:
        limit = parse_strict_int(
            request_config.get("limit", 1000),
            "pipeline.ingest.request.limit",
            minimum=1,
            maximum=10_000,
        )
    except AlpacaConfigError as exc:
        raise ConfigError(str(exc)) from exc
    currency_raw = request_config.get("currency")
    if currency_raw is not None and not isinstance(currency_raw, str):
        raise ConfigError("Alpaca request currency must be a string or null")
    return BarsRequest(
        symbols=tuple(symbols),
        timeframe=str(request_config["timeframe"]),
        start=start,
        end=end,
        feed=str(request_config["feed"]),
        adjustment=str(request_config["adjustment"]),
        limit=limit,
        sort=str(request_config.get("sort", "asc")),
        asof=(
            _date(request_config["asof"], "asof")
            if request_config.get("asof") is not None
            else None
        ),
        currency=currency_raw,
    )


def _provider_config(ctx_config: JsonMapping, provider_name: str) -> JsonMapping:
    providers = _mapping(ctx_config.get("providers"), "providers")
    market = _mapping(providers.get("market"), "providers.market")
    provider = market.get(provider_name)
    if provider is None:
        raise ConfigError(f"missing provider configuration: providers.market.{provider_name}")
    return _mapping(provider, f"providers.market.{provider_name}")


def _cache_policy(ctx_config: JsonMapping, params: JsonMapping) -> CachePolicy:
    raw = params.get("cache", ctx_config.get("cache", {}))
    config = _mapping(raw, "cache")
    ttl = config.get("ttl_seconds")
    if ttl is not None and (isinstance(ttl, bool) or not isinstance(ttl, int)):
        raise ConfigError("cache.ttl_seconds must be an integer or null")
    force_refresh = config.get("force_refresh", False)
    if not isinstance(force_refresh, bool):
        raise ConfigError("cache.force_refresh must be a boolean")
    return CachePolicy(
        ttl_seconds=ttl,
        force_refresh=force_refresh,
        schema_version=str(config.get("schema_version", ALPACA_CACHE_SCHEMA_VERSION)),
    )


def _storage_root(ctx_config: JsonMapping, params: JsonMapping) -> Path:
    raw = params.get("storage", ctx_config.get("storage", {}))
    config = _mapping(raw, "storage")
    storage_type = str(config.get("type", "jsonl"))
    if storage_type != "jsonl":
        raise ConfigError(f"unsupported financial storage type {storage_type!r}")
    root = str(config.get("root", "warehouse/normalized/financial_data"))
    if not root.strip():
        raise ConfigError("storage.root must not be empty")
    return Path(root)


def alpaca_historical_bars_task(
    ctx: Any,
    params: dict[str, Any],
    *,
    registry: ProviderRegistry | None = None,
    store_factory: Callable[[Path], FinancialDataStore] = JsonlFinancialDataStore,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:
    """Fetch, normalize, persist, and publish provider-independent price bars."""
    started_at = time.perf_counter()
    resolved_now = clock()
    request = resolve_bars_request(params, resolved_now=resolved_now)
    validate_alpaca_request(request)
    provider_name = str(params.get("provider", "alpaca")).strip().lower()
    if not provider_name:
        raise ConfigError("pipeline.ingest.provider must not be empty")
    provider_mapping = _provider_config(ctx.cfg, provider_name)
    provider_config = AlpacaProviderConfig.from_mapping(provider_mapping)
    cache_policy = _cache_policy(ctx.cfg, params)
    storage_root = _storage_root(ctx.cfg, params)

    provider_registry = registry or create_price_provider_registry()
    provider = provider_registry.create_price_provider(provider_name, provider_mapping)
    if not isinstance(provider, AlpacaPriceProvider):
        raise ConfigError(
            f"price provider {provider_name!r} does not support Alpaca page diagnostics"
        )

    try:
        fetched = provider.fetch_bars(
            request,
            cache_policy=cache_policy,
            retrieved_at=resolved_now,
        )
    finally:
        provider.close()

    requested_symbols = set(request.symbols)
    returned_symbols = {bar.symbol for bar in fetched.bars}
    raw_record_count = sum(fetched.page_record_counts)
    page_summary = {
        "page_count": len(fetched.pages),
        "records_per_page": list(fetched.page_record_counts),
        "symbols_per_page": [list(page.symbols) for page in fetched.pages],
        "pagination_completed": True,
        "tokens_repeated": False,
        "requested_symbols": sorted(requested_symbols),
        "returned_symbols": sorted(returned_symbols),
        "symbols_with_no_returned_bars": sorted(requested_symbols - returned_symbols),
    }
    request_artifact = safe_request_summary(request, provider_config)
    cache_summary = {
        "status": fetched.cache_status,
        "written": fetched.cache_written,
        "schema_version": cache_policy.schema_version,
        "cache_key_sha256": fetched.cache_key,
    }
    validation_summary = {
        "raw_record_count": raw_record_count,
        "normalized_record_count": len(fetched.bars),
        "duplicates_collapsed": raw_record_count - len(fetched.bars),
        "validation_failures": 0,
    }

    ctx.write_json("alpaca_request.json", request_artifact)
    ctx.write_json("alpaca_cache_summary.json", cache_summary)
    ctx.write_json("alpaca_pages_summary.json", page_summary)
    ctx.write_jsonl(
        "normalized_price_bars.jsonl",
        (
            {
                "schema_version": FINANCIAL_DATA_SCHEMA_VERSION,
                **record_to_dict(bar),
            }
            for bar in fetched.bars
        ),
    )
    ctx.write_json("alpaca_validation_summary.json", validation_summary)

    store = store_factory(storage_root)
    store_result = store.upsert_price_bars(fetched.bars)
    store_summary = {
        "input_record_count": raw_record_count,
        "deduplicated_record_count": len(fetched.bars),
        "inserted": store_result.inserted,
        "updated": store_result.updated,
        "skipped": store_result.skipped,
        "total_records": store_result.total_records,
        "dataset_path": str(store_result.path),
        "metadata_path": str(store_result.metadata_path),
        "schema_version": store_result.schema_version,
    }
    ctx.write_json("alpaca_store_summary.json", store_summary)
    ctx.write_json(
        "alpaca_summary.json",
        {
            "provider": "alpaca",
            "operation": "historical_stock_bars",
            "symbol_count": len(request.symbols),
            "record_count": len(fetched.bars),
            "page_count": len(fetched.pages),
            "cache_status": fetched.cache_status,
            "retry_count": fetched.retry_count,
            "inserted": store_result.inserted,
            "updated": store_result.updated,
            "skipped": store_result.skipped,
        },
    )
    ctx.state["price_bars"] = fetched.bars
    logger.info(
        "provider=alpaca operation=historical_stock_bars symbols=%d timeframe=%s "
        "feed=%s adjustment=%s pages=%d records=%d cache_status=%s retries=%d "
        "inserted=%d updated=%d skipped=%d duration_seconds=%.3f",
        len(request.symbols),
        request.timeframe,
        request.feed,
        request.adjustment,
        len(fetched.pages),
        len(fetched.bars),
        fetched.cache_status,
        fetched.retry_count,
        store_result.inserted,
        store_result.updated,
        store_result.skipped,
        time.perf_counter() - started_at,
    )

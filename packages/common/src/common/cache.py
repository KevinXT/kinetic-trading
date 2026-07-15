from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]


CACHE_ROOT = Path(".cache")


@dataclass(frozen=True)
class CachePolicy:
    """Controls freshness and schema invalidation for a cached request."""

    ttl_seconds: int | None = None
    force_refresh: bool = False
    schema_version: str = "1"

    def __post_init__(self) -> None:
        if self.ttl_seconds is not None and self.ttl_seconds < 0:
            raise ValueError("ttl_seconds must be non-negative or None")
        if not self.schema_version.strip():
            raise ValueError("schema_version must not be empty")


@dataclass(frozen=True)
class CacheResult:
    """Cached payload plus safe observability metadata."""

    data: JsonDict
    status: str
    key: str


def make_cache_key(payload: JsonDict) -> str:
    """
    Create a deterministic SHA256 cache key from a JSON-serializable payload.

    The payload is sorted before hashing so equivalent dictionaries produce
    stable keys.
    """
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cache_file_path(namespace: str, key: str) -> Path:
    """Return the cache file path for a namespace + key."""
    return CACHE_ROOT / namespace / f"{key}.json"


def _load_json_cache(
    namespace: str, key: str, *, ttl_seconds: int | None = None
) -> Optional[JsonDict]:
    """
    Load cached JSON data.

    Returns None if the cache file does not exist or contains invalid JSON.
    """
    path = _cache_file_path(namespace, key)

    if not path.exists():
        return None

    if ttl_seconds is not None and time.time() - path.stat().st_mtime > ttl_seconds:
        return None

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        logger.warning("corrupt cache file, ignoring: %s", path)
        return None


def load_json_cache(namespace: str, key: str) -> Optional[JsonDict]:
    """Load cached JSON while preserving the original public API."""
    return _load_json_cache(namespace, key)


def save_json_cache(namespace: str, key: str, data: JsonDict) -> Path:
    """Atomically save JSON data into the cache directory."""
    path = _cache_file_path(namespace, key)

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise

    return path


def _policy_payload(payload: JsonDict, policy: CachePolicy | None) -> JsonDict:
    if policy is None:
        return payload
    return {"schema_version": policy.schema_version, "request": payload}


def get_or_fetch_json_result(
    *,
    namespace: str,
    payload: JsonDict,
    fetch_fn: Callable[[], JsonDict],
    force_refresh: bool = False,
    policy: CachePolicy | None = None,
) -> CacheResult:
    """Cache-aside lookup that also returns hit/miss metadata."""
    key = make_cache_key(_policy_payload(payload, policy))
    refresh = force_refresh or (policy.force_refresh if policy is not None else False)
    ttl_seconds = policy.ttl_seconds if policy is not None else None

    if not refresh:
        cached = _load_json_cache(namespace, key, ttl_seconds=ttl_seconds)
        if cached is not None:
            logger.info("cache HIT namespace=%s key=%s", namespace, key[:12])
            return CacheResult(data=cached, status="hit", key=key)

    logger.info("cache MISS namespace=%s key=%s", namespace, key[:12])
    fresh_data = fetch_fn()
    save_json_cache(namespace, key, fresh_data)
    return CacheResult(data=fresh_data, status="miss", key=key)


def get_or_fetch_json(
    *,
    namespace: str,
    payload: JsonDict,
    fetch_fn: Callable[[], JsonDict],
    force_refresh: bool = False,
) -> JsonDict:
    """
    Cache-aside helper.

    Flow:
        make cache key
        -> check local cache
        -> if found: return cached data
        -> if missing: call fetch_fn()
        -> save response
        -> return fresh data

    Parameters
    ----------
    namespace:
        Logical cache grouping, for example:
            gdelt_doc
            market_prices
            sentiment_scores

    payload:
        JSON-serializable data used to generate a deterministic cache key.

    fetch_fn:
        Function called only when the cache is missing.

    force_refresh:
        If True, bypass cache and fetch fresh data.
    """
    return get_or_fetch_json_result(
        namespace=namespace,
        payload=payload,
        fetch_fn=fetch_fn,
        force_refresh=force_refresh,
    ).data

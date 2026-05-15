from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional


logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]


CACHE_ROOT = Path(".cache")


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


def load_json_cache(namespace: str, key: str) -> Optional[JsonDict]:
    """
    Load cached JSON data.

    Returns None if the cache file does not exist or contains invalid JSON.
    """
    path = _cache_file_path(namespace, key)

    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        logger.warning("corrupt cache file, ignoring: %s", path)
        return None


def save_json_cache(namespace: str, key: str, data: JsonDict) -> Path:
    """Save JSON data into the cache directory."""
    path = _cache_file_path(namespace, key)

    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return path


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
    key = make_cache_key(payload)

    if not force_refresh:
        cached = load_json_cache(namespace, key)
        if cached is not None:
            logger.info("cache HIT namespace=%s key=%s", namespace, key[:12])
            return cached

    logger.info("cache MISS namespace=%s key=%s", namespace, key[:12])

    fresh_data = fetch_fn()

    save_json_cache(namespace, key, fresh_data)

    return fresh_data

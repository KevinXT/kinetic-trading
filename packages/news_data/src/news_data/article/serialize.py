"""Deterministic JSON helpers for news_data article records.

Mirrors ``market_data.domain.serialization`` without importing market_data
(news_data must not depend on market_data).
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping


def _datetime_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("cannot serialize a naive datetime")
    utc_value = value.astimezone(timezone.utc)
    return utc_value.isoformat().replace("+00:00", "Z")


def to_json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _datetime_text(value)
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return to_json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported JSON value type: {type(value).__name__}")


def stable_json_dumps(value: object) -> str:
    return json.dumps(
        to_json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )

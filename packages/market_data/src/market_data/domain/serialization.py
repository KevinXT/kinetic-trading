"""Deterministic serialization for normalized domain records."""

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
    """Convert supported domain values into deterministic JSON-ready values."""
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


def record_to_dict(record: object) -> dict[str, Any]:
    """Serialize a domain dataclass to a JSON-ready dictionary."""
    value = to_json_value(record)
    if not isinstance(value, dict):
        raise TypeError("record must serialize to a dictionary")
    return value


def stable_json_dumps(value: object) -> str:
    """Return canonical compact JSON suitable for JSONL comparisons."""
    return json.dumps(
        to_json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )

"""Shared validation helpers for canonical record schemas.

Every canonical dataclass is frozen and validates in ``__post_init__``: an
invalid record cannot be constructed, so nothing downstream has to re-check it.
Timestamps are timezone-aware UTC; identity fields are stripped and non-empty;
floats are finite.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import TypeAlias

LogicalKey: TypeAlias = tuple[object, ...]


def normalize_cik(value: str) -> str:
    """Return the SEC's canonical ten-digit CIK representation."""
    normalized = value.strip()
    if normalized.lower().startswith("cik"):
        normalized = normalized[3:]
    if not normalized.isdigit() or len(normalized) > 10:
        raise ValueError(f"CIK must contain at most ten digits, got {value!r}")
    return normalized.zfill(10)


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _identity_text(value: str, field_name: str) -> str:
    """Validate identity text without changing the provider's representation."""
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _symbol(value: str) -> str:
    return _required_text(value, "symbol").upper()


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _date(value: date, field_name: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{field_name} must be a date")
    return value


def _finite(value: float, field_name: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{field_name} must be finite")
    return normalized


def _non_negative_finite(value: float, field_name: str) -> float:
    normalized = _finite(value, field_name)
    if normalized < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return normalized


def normalize_currency(value: str, field_name: str = "currency") -> str:
    """Return an uppercase ISO-4217 currency code."""
    normalized = _required_text(value, field_name).upper()
    if len(normalized) != 3 or not normalized.isalpha():
        raise ValueError(f"{field_name} must be a three-letter ISO-4217 code")
    return normalized

"""Validated Alpaca response types."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from kinetic.ingestion.market.alpaca.errors import AlpacaPayloadError


def _timestamp(value: object, location: str) -> datetime:
    if not isinstance(value, str):
        raise AlpacaPayloadError(f"{location}.t must be an RFC 3339 timestamp string")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AlpacaPayloadError(f"{location}.t is not a valid RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AlpacaPayloadError(f"{location}.t must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _number(value: object, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AlpacaPayloadError(f"{location} must be a finite number")
    converted = float(value)
    if not math.isfinite(converted):
        raise AlpacaPayloadError(f"{location} must be a finite number")
    return converted


def _integer(value: object, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AlpacaPayloadError(f"{location} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class AlpacaRawBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float | None
    trade_count: int | None


@dataclass(frozen=True, slots=True)
class AlpacaBarsPage:
    bars: Mapping[str, tuple[AlpacaRawBar, ...]]
    next_page_token: str | None
    raw_payload: dict[str, Any]

    @property
    def record_count(self) -> int:
        return sum(len(records) for records in self.bars.values())

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(symbol for symbol, records in self.bars.items() if records))


def parse_alpaca_bars_page(payload: object) -> AlpacaBarsPage:
    """Validate one raw response page without silently dropping malformed bars."""
    if not isinstance(payload, dict):
        raise AlpacaPayloadError("Alpaca bars response must be a JSON object")

    raw_bars = payload.get("bars", {})
    if not isinstance(raw_bars, dict):
        raise AlpacaPayloadError("Alpaca bars response field 'bars' must be an object")

    parsed_bars: dict[str, tuple[AlpacaRawBar, ...]] = {}
    for raw_symbol, raw_records in raw_bars.items():
        if not isinstance(raw_symbol, str) or not raw_symbol.strip():
            raise AlpacaPayloadError("Alpaca bars response contains an invalid symbol key")
        if not isinstance(raw_records, list):
            raise AlpacaPayloadError(f"Alpaca bars for {raw_symbol!r} must be a list")

        records: list[AlpacaRawBar] = []
        for index, raw_record in enumerate(raw_records):
            location = f"bars.{raw_symbol}[{index}]"
            if not isinstance(raw_record, dict):
                raise AlpacaPayloadError(f"{location} must be an object")
            missing = [field for field in ("t", "o", "h", "l", "c", "v") if field not in raw_record]
            if missing:
                raise AlpacaPayloadError(
                    f"{location} is missing required field(s): {', '.join(missing)}"
                )
            records.append(
                AlpacaRawBar(
                    timestamp=_timestamp(raw_record["t"], location),
                    open=_number(raw_record["o"], f"{location}.o"),
                    high=_number(raw_record["h"], f"{location}.h"),
                    low=_number(raw_record["l"], f"{location}.l"),
                    close=_number(raw_record["c"], f"{location}.c"),
                    volume=_integer(raw_record["v"], f"{location}.v"),
                    vwap=(
                        None
                        if raw_record.get("vw") is None
                        else _number(raw_record["vw"], f"{location}.vw")
                    ),
                    trade_count=(
                        None
                        if raw_record.get("n") is None
                        else _integer(raw_record["n"], f"{location}.n")
                    ),
                )
            )
        parsed_bars[raw_symbol.upper()] = tuple(records)

    token = payload.get("next_page_token")
    if token is not None and not isinstance(token, str):
        raise AlpacaPayloadError("Alpaca next_page_token must be null or a string")
    return AlpacaBarsPage(
        bars=parsed_bars,
        next_page_token=token or None,
        raw_payload=payload,
    )

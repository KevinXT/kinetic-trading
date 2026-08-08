"""Pure normalization tests for Alpaca historical bars."""

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kinetic.data.schemas.market import PriceBar
from kinetic.data.serialization import stable_json_dumps
from kinetic.ingestion.market.alpaca.errors import (
    AlpacaNormalizationConflictError,
    AlpacaPayloadError,
)
from kinetic.ingestion.market.alpaca.normalize import normalize_alpaca_bars
from kinetic.ingestion.market.alpaca.raw_models import parse_alpaca_bars_page
from kinetic.ingestion.requests import BarsRequest

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "alpaca"
RETRIEVED_AT = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)


def _payload(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _request() -> BarsRequest:
    return BarsRequest(
        symbols=("MSFT", "AAPL"),
        timeframe="1Day",
        start=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end=datetime(2026, 6, 30, tzinfo=timezone.utc),
        feed="iex",
        adjustment="all",
    )


def test_exact_field_mapping_and_provider_metadata() -> None:
    page = parse_alpaca_bars_page(_payload("one_page_multi_symbol.json"))
    bars = normalize_alpaca_bars([page], request=_request(), retrieved_at=RETRIEVED_AT)
    assert all(isinstance(bar, PriceBar) for bar in bars)
    assert [bar.symbol for bar in bars] == ["AAPL", "MSFT"]
    apple = bars[0]
    assert apple.timestamp == datetime(2026, 6, 2, 4, tzinfo=timezone.utc)
    assert (apple.open, apple.high, apple.low, apple.close) == (200.0, 205.0, 199.0, 204.0)
    assert (apple.volume, apple.trade_count, apple.vwap) == (1000, 10, 202.5)
    assert (apple.provider, apple.feed, apple.adjustment, apple.timeframe) == (
        "alpaca",
        "iex",
        "all",
        "1Day",
    )
    assert apple.currency == "USD"
    assert apple.retrieved_at == RETRIEVED_AT


def test_requested_currency_is_stamped_on_normalized_bars() -> None:
    page = parse_alpaca_bars_page(_payload("one_page_multi_symbol.json"))
    request = BarsRequest(
        symbols=("MSFT", "AAPL"),
        timeframe="1Day",
        start=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end=datetime(2026, 6, 30, tzinfo=timezone.utc),
        feed="iex",
        adjustment="all",
        currency="CAD",
    )
    bars = normalize_alpaca_bars([page], request=request, retrieved_at=RETRIEVED_AT)
    assert {bar.currency for bar in bars} == {"CAD"}
    assert bars[0].logical_key[-1] == "CAD"


def test_multiple_pages_and_symbols_are_sorted_deterministically() -> None:
    pages = [
        parse_alpaca_bars_page(_payload("two_page_second.json")),
        parse_alpaca_bars_page(_payload("two_page_first.json")),
    ]
    bars = normalize_alpaca_bars(pages, request=_request(), retrieved_at=RETRIEVED_AT)
    assert [bar.symbol for bar in bars] == ["AAPL", "MSFT"]


def test_empty_response_normalizes_to_empty_tuple() -> None:
    page = parse_alpaca_bars_page(_payload("empty.json"))
    assert normalize_alpaca_bars([page], request=_request(), retrieved_at=RETRIEVED_AT) == ()


def test_missing_optional_fields_remain_none() -> None:
    page = parse_alpaca_bars_page(_payload("missing_optional_fields.json"))
    bar = normalize_alpaca_bars([page], request=_request(), retrieved_at=RETRIEVED_AT)[0]
    assert bar.vwap is None
    assert bar.trade_count is None


def test_identical_duplicate_across_pages_is_collapsed() -> None:
    page = parse_alpaca_bars_page(_payload("two_page_first.json"))
    bars = normalize_alpaca_bars([page, page], request=_request(), retrieved_at=RETRIEVED_AT)
    assert len(bars) == 1


def test_conflicting_duplicate_across_pages_raises() -> None:
    first_payload = _payload("two_page_first.json")
    second_payload = copy.deepcopy(first_payload)
    second_payload["bars"]["AAPL"][0]["c"] = 203.0  # type: ignore[index]
    pages = [
        parse_alpaca_bars_page(first_payload),
        parse_alpaca_bars_page(second_payload),
    ]
    with pytest.raises(AlpacaNormalizationConflictError, match="logical key"):
        normalize_alpaca_bars(pages, request=_request(), retrieved_at=RETRIEVED_AT)


def test_normalizer_does_not_mutate_raw_input() -> None:
    payload = _payload("one_page_multi_symbol.json")
    original = copy.deepcopy(payload)
    page = parse_alpaca_bars_page(payload)
    normalize_alpaca_bars([page], request=_request(), retrieved_at=RETRIEVED_AT)
    assert payload == original


def test_stable_serialization_contains_no_raw_provider_fields() -> None:
    page = parse_alpaca_bars_page(_payload("one_page_multi_symbol.json"))
    bar = normalize_alpaca_bars([page], request=_request(), retrieved_at=RETRIEVED_AT)[0]
    serialized = stable_json_dumps(bar)
    assert '"timestamp":"2026-06-02T04:00:00Z"' in serialized
    for raw_field in ('"t":', '"o":', '"h":', '"l":', '"c":', '"v":', '"vw":', '"n":'):
        assert raw_field not in serialized


def test_malformed_bar_fails_instead_of_being_discarded() -> None:
    with pytest.raises(AlpacaPayloadError, match="missing required"):
        parse_alpaca_bars_page(_payload("malformed_bar.json"))

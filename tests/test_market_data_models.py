"""Tests for provider-independent financial data contracts."""

from datetime import date, datetime, timedelta, timezone

import pytest
from market_data.domain.models import (
    FilingEvent,
    FinancialFact,
    MacroObservation,
    MarketEvent,
    PriceBar,
    normalize_cik,
)
from market_data.domain.requests import BarsRequest, FilingRequest, MacroSeriesRequest
from market_data.domain.serialization import record_to_dict, stable_json_dumps

UTC = timezone.utc
NOW = datetime(2026, 7, 14, 20, 0, tzinfo=UTC)


def _bar(**overrides: object) -> PriceBar:
    values = {
        "symbol": "aapl",
        "timestamp": NOW,
        "timeframe": "1Day",
        "open": 200.0,
        "high": 205.0,
        "low": 199.0,
        "close": 204.0,
        "volume": 1000,
        "vwap": None,
        "trade_count": None,
        "provider": "alpaca",
        "feed": "iex",
        "adjustment": "all",
        "retrieved_at": NOW,
    }
    values.update(overrides)
    return PriceBar(**values)  # type: ignore[arg-type]


def test_price_bar_normalizes_symbol_and_utc() -> None:
    eastern = timezone(timedelta(hours=-4))
    bar = _bar(timestamp=datetime(2026, 7, 14, 16, 0, tzinfo=eastern))
    assert bar.symbol == "AAPL"
    assert bar.timestamp == NOW
    assert record_to_dict(bar)["timestamp"] == "2026-07-14T20:00:00Z"


def test_price_bar_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _bar(timestamp=datetime(2026, 7, 14, 20, 0))


def test_price_bar_key_keeps_feed_and_adjustment_distinct() -> None:
    assert _bar(feed="iex").logical_key != _bar(feed="sip").logical_key
    assert _bar(adjustment="raw").logical_key != _bar(adjustment="all").logical_key


def test_filing_preserves_accession_identity_and_missing_acceptance() -> None:
    filing = FilingEvent(
        accession_number="0000320193-26-000001",
        cik="320193",
        symbol="aapl",
        form="8-k",
        accepted_at=None,
        filed_date=date(2026, 7, 14),
        report_date=None,
        primary_document=None,
        filing_url="https://www.sec.gov/Archives/example",
        items=("2.02", "2.02", ""),
        provider="sec_edgar",
        retrieved_at=NOW,
    )
    assert filing.cik == "0000320193"
    assert filing.form == "8-k"
    assert filing.items == ("2.02",)
    assert filing.logical_key == ("0000320193-26-000001",)


def test_financial_fact_key_preserves_filing_and_unit() -> None:
    base = {
        "cik": "320193",
        "symbol": "AAPL",
        "taxonomy": "us-gaap",
        "concept": "Assets",
        "unit": "USD",
        "period_start": None,
        "period_end": date(2026, 6, 30),
        "value": 1.0,
        "fiscal_year": 2026,
        "fiscal_period": "Q3",
        "form": "10-Q",
        "filed_date": date(2026, 7, 14),
        "accession_number": "first",
        "provider": "sec_edgar",
        "retrieved_at": NOW,
    }
    original = FinancialFact(**base)  # type: ignore[arg-type]
    amended = FinancialFact(**{**base, "accession_number": "amended"})  # type: ignore[arg-type]
    other_unit = FinancialFact(**{**base, "unit": "shares"})  # type: ignore[arg-type]
    assert original.logical_key != amended.logical_key
    assert original.logical_key != other_unit.logical_key


def test_sec_identity_strings_are_preserved_verbatim() -> None:
    fact = FinancialFact(
        cik="320193",
        symbol="aapl",
        taxonomy="custom-Taxonomy",
        concept="CustomConcept",
        unit="USD/shares",
        period_start=None,
        period_end=date(2026, 6, 30),
        value=1.0,
        fiscal_year=None,
        fiscal_period=None,
        form="10-q/A",
        filed_date=date(2026, 7, 14),
        accession_number="Accession-Original",
        provider="SEC-EDGAR",
        retrieved_at=NOW,
    )
    assert fact.taxonomy == "custom-Taxonomy"
    assert fact.concept == "CustomConcept"
    assert fact.unit == "USD/shares"
    assert fact.form == "10-q/A"
    assert fact.accession_number == "Accession-Original"
    assert fact.provider == "SEC-EDGAR"


def test_macro_observation_preserves_missing_value_and_vintage() -> None:
    observation = MacroObservation(
        series_id="dff",
        observation_date=date(2026, 7, 1),
        value=None,
        realtime_start=date(2026, 7, 2),
        realtime_end=date(2026, 7, 3),
        vintage_date=date(2026, 7, 2),
        provider="fred",
        retrieved_at=NOW,
    )
    assert observation.value is None
    assert observation.series_id == "dff"
    assert record_to_dict(observation)["vintage_date"] == "2026-07-02"


def test_market_event_keeps_effective_and_observed_times_distinct() -> None:
    event = MarketEvent(
        event_id="sec:first",
        event_type="sec_filing",
        effective_at=NOW,
        observed_at=NOW + timedelta(minutes=5),
        instrument_id="US-0000320193",
        symbol="aapl",
        macro_series_id=None,
        source="sec_edgar",
        source_id="first",
        title=None,
        payload_path=None,
    )
    assert event.effective_at != event.observed_at
    assert event.symbol == "AAPL"


def test_request_models_are_canonical_and_validate_ranges() -> None:
    bars = BarsRequest(
        symbols=("msft", "AAPL", "aapl"),
        timeframe="1Day",
        start=NOW - timedelta(days=1),
        end=NOW,
        feed="iex",
        adjustment="all",
    )
    assert bars.symbols == ("AAPL", "MSFT")
    filings = FilingRequest(symbols=("aapl",), forms=("10-k", "8-K"))
    assert filings.forms == ("10-K", "8-K")
    vintages = MacroSeriesRequest(
        series_id="dff",
        vintage_dates=(date(2026, 7, 2), date(2026, 7, 2)),
    )
    assert vintages.vintage_dates == (date(2026, 7, 2),)


def test_stable_serialization_and_cik_validation() -> None:
    assert stable_json_dumps({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert normalize_cik("CIK320193") == "0000320193"
    with pytest.raises(ValueError, match="ten digits"):
        normalize_cik("not-a-cik")

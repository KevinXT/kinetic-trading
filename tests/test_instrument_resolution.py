"""Tests for stable instrument identity and curated alias resolution."""

from datetime import date

import pytest
from market_data.domain.models import Instrument
from market_data.instruments import AmbiguousInstrumentError, InMemoryInstrumentResolver


def _instrument(
    *,
    instrument_id: str = "US-0000320193",
    symbol: str = "AAPL",
    cik: str = "320193",
    aliases: tuple[str, ...] = ("Apple", "Apple Computer"),
    valid_from: date | None = None,
    valid_to: date | None = None,
) -> Instrument:
    return Instrument(
        instrument_id=instrument_id,
        symbol=symbol,
        cik=cik,
        company_name="Apple Inc.",
        aliases=aliases,
        exchange="nasdaq",
        sector="Technology",
        sector_etf="xlk",
        valid_from=valid_from,
        valid_to=valid_to,
    )


def test_resolves_by_internal_id_symbol_cik_and_alias() -> None:
    instrument = _instrument()
    resolver = InMemoryInstrumentResolver([instrument])
    assert resolver.resolve("US-0000320193") == instrument
    assert resolver.resolve("aapl") == instrument
    assert resolver.resolve("CIK320193") == instrument
    assert resolver.resolve("apple computer") == instrument


def test_instrument_normalizes_identity_fields() -> None:
    instrument = _instrument(symbol="aapl", cik="320193")
    assert instrument.symbol == "AAPL"
    assert instrument.cik == "0000320193"
    assert instrument.exchange == "nasdaq"
    assert instrument.sector_etf == "XLK"


def test_validity_dates_support_historical_ticker_resolution() -> None:
    old = _instrument(
        instrument_id="US-OLD",
        symbol="OLD",
        aliases=("Company",),
        valid_to=date(2020, 12, 31),
    )
    current = _instrument(
        instrument_id="US-CURRENT",
        symbol="NEW",
        aliases=("Company",),
        valid_from=date(2021, 1, 1),
    )
    resolver = InMemoryInstrumentResolver([old, current])
    assert resolver.resolve("Company") == current
    assert resolver.resolve("Company", as_of=date(2020, 6, 1)) == old


def test_ambiguous_alias_raises_instead_of_guessing() -> None:
    first = _instrument(instrument_id="US-FIRST", aliases=("Shared",))
    second = _instrument(
        instrument_id="US-SECOND", symbol="MSFT", cik="789019", aliases=("Shared",)
    )
    resolver = InMemoryInstrumentResolver([first, second])
    with pytest.raises(AmbiguousInstrumentError, match="ambiguous") as error:
        resolver.resolve("shared")
    assert error.value.instrument_ids == ("US-FIRST", "US-SECOND")


def test_exact_symbol_takes_precedence_over_another_instruments_alias() -> None:
    exact = _instrument(instrument_id="US-EXACT", symbol="AAPL", aliases=())
    alias_only = _instrument(
        instrument_id="US-ALIAS",
        symbol="OTHER",
        cik="789019",
        aliases=("AAPL",),
    )
    assert InMemoryInstrumentResolver([exact, alias_only]).resolve("AAPL") == exact


def test_unknown_identifier_returns_none() -> None:
    assert InMemoryInstrumentResolver([_instrument()]).resolve("UNKNOWN") is None

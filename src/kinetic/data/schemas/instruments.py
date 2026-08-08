"""Canonical instrument identity.

Today this models what the equities-and-news code needs: a provider-independent
``instrument_id``, a symbol, a CIK, aliases and a validity range. Extending it to
futures, options, forex and crypto is an additive change; the fields that will be
required are listed in ``docs/architecture/data-lifecycle.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from kinetic.data.schemas._validation import (
    LogicalKey,
    _date,
    _identity_text,
    _symbol,
    normalize_cik,
)


@dataclass(frozen=True, slots=True)
class Instrument:
    instrument_id: str
    symbol: str
    cik: str
    company_name: str
    aliases: tuple[str, ...]
    exchange: str
    sector: str | None
    sector_etf: str | None
    valid_from: date | None
    valid_to: date | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "instrument_id", _identity_text(self.instrument_id, "instrument_id")
        )
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        object.__setattr__(self, "company_name", _identity_text(self.company_name, "company_name"))
        object.__setattr__(
            self,
            "aliases",
            tuple(dict.fromkeys(alias.strip() for alias in self.aliases if alias.strip())),
        )
        object.__setattr__(self, "exchange", _identity_text(self.exchange, "exchange"))
        if self.sector is not None:
            object.__setattr__(self, "sector", _identity_text(self.sector, "sector"))
        if self.sector_etf is not None:
            object.__setattr__(self, "sector_etf", _symbol(self.sector_etf))
        if self.valid_from is not None:
            object.__setattr__(self, "valid_from", _date(self.valid_from, "valid_from"))
        if self.valid_to is not None:
            object.__setattr__(self, "valid_to", _date(self.valid_to, "valid_to"))
        if self.valid_from is not None and self.valid_to is not None:
            if self.valid_from > self.valid_to:
                raise ValueError("valid_from must not be after valid_to")

    @property
    def logical_key(self) -> LogicalKey:
        return (self.instrument_id,)

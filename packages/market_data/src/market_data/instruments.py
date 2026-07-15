"""Instrument identity and curated resolution."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Protocol, Sequence

from market_data.domain.models import Instrument, normalize_cik


class AmbiguousInstrumentError(ValueError):
    """Raised when one identifier matches multiple valid instruments."""

    def __init__(self, identifier: str, instrument_ids: Sequence[str]) -> None:
        self.identifier = identifier
        self.instrument_ids = tuple(sorted(instrument_ids))
        candidates = ", ".join(self.instrument_ids)
        super().__init__(f"identifier {identifier!r} is ambiguous across: {candidates}")


class InstrumentResolver(Protocol):
    def resolve(self, identifier: str, *, as_of: date | None = None) -> Instrument | None:
        """Resolve an ID, symbol, CIK, company name, or alias."""
        ...


class InMemoryInstrumentResolver:
    """Resolve a curated instrument master while respecting validity dates."""

    def __init__(self, instruments: Sequence[Instrument]) -> None:
        self._internal_ids: dict[str, list[Instrument]] = defaultdict(list)
        self._symbols: dict[str, list[Instrument]] = defaultdict(list)
        self._ciks: dict[str, list[Instrument]] = defaultdict(list)
        self._aliases: dict[str, list[Instrument]] = defaultdict(list)
        for instrument in instruments:
            self._internal_ids[instrument.instrument_id.casefold()].append(instrument)
            self._symbols[instrument.symbol.casefold()].append(instrument)
            self._ciks[instrument.cik].append(instrument)
            aliases = {
                instrument.company_name.casefold(),
                *(alias.casefold() for alias in instrument.aliases),
            }
            for alias in aliases:
                self._aliases[alias].append(instrument)

    @staticmethod
    def _valid(instrument: Instrument, as_of: date | None) -> bool:
        if as_of is None:
            return instrument.valid_to is None
        if instrument.valid_from is not None and as_of < instrument.valid_from:
            return False
        if instrument.valid_to is not None and as_of > instrument.valid_to:
            return False
        return True

    def resolve(self, identifier: str, *, as_of: date | None = None) -> Instrument | None:
        normalized = identifier.strip()
        if not normalized:
            raise ValueError("identifier must not be empty")

        if normalized.lower().startswith("cik") or normalized.isdigit():
            try:
                matches = self._ciks.get(normalize_cik(normalized), ())
            except ValueError:
                matches = ()
        else:
            folded = normalized.casefold()
            matches = self._internal_ids.get(folded, ())
            if not matches:
                matches = self._symbols.get(folded, ())
            if not matches:
                matches = self._aliases.get(folded, ())

        valid_matches = [instrument for instrument in matches if self._valid(instrument, as_of)]
        unique = {instrument.instrument_id: instrument for instrument in valid_matches}
        if not unique:
            return None
        if len(unique) > 1:
            raise AmbiguousInstrumentError(identifier, tuple(unique))
        return next(iter(unique.values()))

"""Storage contracts for normalized financial records."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from kinetic.data.schemas.fundamentals import FilingEvent, FinancialFact
from kinetic.data.schemas.instruments import Instrument
from kinetic.data.schemas.macro import MacroObservation
from kinetic.data.schemas.market import PriceBar


@dataclass(frozen=True, slots=True)
class StoreResult:
    inserted: int
    updated: int
    skipped: int
    total_records: int
    path: Path
    metadata_path: Path
    schema_version: str


class FinancialDataStore(Protocol):
    def upsert_price_bars(self, records: Sequence[PriceBar]) -> StoreResult: ...

    def upsert_filing_events(self, records: Sequence[FilingEvent]) -> StoreResult: ...

    def upsert_financial_facts(self, records: Sequence[FinancialFact]) -> StoreResult: ...

    def upsert_macro_observations(self, records: Sequence[MacroObservation]) -> StoreResult: ...

    def upsert_instruments(self, records: Sequence[Instrument]) -> StoreResult: ...

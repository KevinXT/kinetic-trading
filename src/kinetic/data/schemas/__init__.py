"""Canonical record schemas, grouped by what they describe.

Every schema here is provider-independent: it says what a price bar *is*, not
what Alpaca's JSON looks like. Provider payload shapes live next to their
adapter, e.g. ``kinetic.ingestion.market.alpaca.raw_models``.
"""

from kinetic.data.schemas.entities import ENTITY_TYPES, EntityReferenceV1
from kinetic.data.schemas.fundamentals import FilingEvent, FinancialFact
from kinetic.data.schemas.instruments import Instrument
from kinetic.data.schemas.macro import MacroObservation
from kinetic.data.schemas.market import (
    DEFAULT_PRICE_BAR_CURRENCY,
    MarketEvent,
    PriceBar,
)
from kinetic.data.schemas.news import ArticleTextRecordV1

__all__ = [
    "DEFAULT_PRICE_BAR_CURRENCY",
    "ENTITY_TYPES",
    "ArticleTextRecordV1",
    "EntityReferenceV1",
    "FilingEvent",
    "FinancialFact",
    "Instrument",
    "MacroObservation",
    "MarketEvent",
    "PriceBar",
]

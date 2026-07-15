"""Provider-agnostic financial data contracts, providers, and persistence."""

from market_data.domain import (
    BarsRequest,
    CompanyFactsRequest,
    FilingEvent,
    FilingRequest,
    FinancialFact,
    Instrument,
    MacroObservation,
    MacroSeriesRequest,
    MarketEvent,
    PriceBar,
)
from market_data.instruments import (
    AmbiguousInstrumentError,
    InMemoryInstrumentResolver,
    InstrumentResolver,
)
from market_data.providers import (
    CompanyDataProvider,
    MacroDataProvider,
    PriceDataProvider,
    ProviderRegistry,
)
from market_data.storage import (
    FINANCIAL_DATA_SCHEMA_VERSION,
    ConcurrentDatasetWriteError,
    ConflictingBatchRecordsError,
    DatasetSchemaError,
    FinancialDataStore,
    JsonlFinancialDataStore,
    StoreResult,
)

__all__ = [
    "BarsRequest",
    "AmbiguousInstrumentError",
    "CompanyDataProvider",
    "CompanyFactsRequest",
    "ConcurrentDatasetWriteError",
    "ConflictingBatchRecordsError",
    "DatasetSchemaError",
    "FINANCIAL_DATA_SCHEMA_VERSION",
    "FilingEvent",
    "FilingRequest",
    "FinancialFact",
    "FinancialDataStore",
    "InMemoryInstrumentResolver",
    "Instrument",
    "InstrumentResolver",
    "JsonlFinancialDataStore",
    "MacroDataProvider",
    "MacroObservation",
    "MacroSeriesRequest",
    "MarketEvent",
    "PriceBar",
    "PriceDataProvider",
    "ProviderRegistry",
    "StoreResult",
]

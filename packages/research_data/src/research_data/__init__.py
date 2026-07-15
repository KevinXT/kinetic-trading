"""Normalized, leakage-aware news×market research-data layer.

This package is a *derived product*. It consumes normalized ``market_data`` and
``news_data`` records and produces reproducible research observations, a dataset
manifest, and an offline event-study report. It never executes trades, optimizes
portfolios, trains predictive models, or claims profitability.
"""

from research_data.alignment import (
    POLICY_CONSERVATIVE,
    POLICY_SESSION_WINDOW,
    build_alignment_policy,
)
from research_data.builder import DatasetResult, build_dataset
from research_data.calendar import (
    DEFAULT_CALENDAR,
    MarketCalendar,
    USEquitySessionCalendarV1,
)
from research_data.catalog import FeatureCatalog, load_feature_catalog
from research_data.event_study import EventStudyConfig, run_event_study
from research_data.mappings import load_topic_instrument_mappings
from research_data.models import (
    DatasetManifest,
    MarketSessionFeature,
    NewsMarketObservation,
    NewsTopicDailyFeature,
    SessionNewsFeature,
    TopicInstrumentMapping,
)

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_CALENDAR",
    "DatasetManifest",
    "DatasetResult",
    "EventStudyConfig",
    "FeatureCatalog",
    "MarketCalendar",
    "MarketSessionFeature",
    "NewsMarketObservation",
    "NewsTopicDailyFeature",
    "POLICY_CONSERVATIVE",
    "POLICY_SESSION_WINDOW",
    "SessionNewsFeature",
    "TopicInstrumentMapping",
    "USEquitySessionCalendarV1",
    "build_alignment_policy",
    "build_dataset",
    "load_feature_catalog",
    "load_topic_instrument_mappings",
    "run_event_study",
]

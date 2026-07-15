"""Normalized research-layer records.

These are the derived-product schemas that align GDELT news measurements with
Alpaca market sessions. They are frozen dataclasses in the style of
``market_data.domain`` (explicit logical identity, timezone-aware UTC datetimes,
deterministic serialization). They never replace the normalized source datasets;
they reference them by identity and lineage.

Field categories (identity / lineage / availability / input / contemporaneous /
forward-target / quality / diagnostic) are defined by the feature catalog. To keep
input and target fields unmistakably distinct on a joined row,
``NewsMarketObservation`` stores them in *separate named sub-objects* rather than
one flat bag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Mapping

from market_data.domain.serialization import to_json_value

# --- Controlled vocabularies -------------------------------------------------

# News measurement methods: the two conceptually different GDELT paths.
METHOD_DOC = "gdelt_doc_artlist"
METHOD_BIGQUERY = "bigquery_gdelt_counts"
NEWS_MEASUREMENT_METHODS = frozenset({METHOD_DOC, METHOD_BIGQUERY})

# Historical-availability assumptions are explicit, controlled, and versioned.
AVAILABILITY_PUBLICATION_PROXY = "publication_timestamp_proxy_v1"
AVAILABILITY_DAILY_PERIOD_END_PROXY = "daily_period_end_proxy_v1"
AVAILABILITY_COLLECTION_TIME_ONLY = "collection_time_not_historical_v1"
AVAILABILITY_ASSUMPTIONS = frozenset(
    {
        AVAILABILITY_PUBLICATION_PROXY,
        AVAILABILITY_DAILY_PERIOD_END_PROXY,
        AVAILABILITY_COLLECTION_TIME_ONLY,
    }
)

# Coverage / missingness statuses. A measured zero is NOT an unavailable value.
COVERAGE_OK = "measured"
COVERAGE_ZERO = "measured_zero"
COVERAGE_NOT_COLLECTED = "not_collected"
COVERAGE_NO_RECORDS = "provider_no_records"
COVERAGE_TRUNCATED = "provider_truncated"
COVERAGE_UNSUPPORTED = "feature_unsupported"
COVERAGE_INSUFFICIENT_HISTORY = "insufficient_history"
COVERAGE_MISSING_BAR = "market_bar_missing"
NEWS_COVERAGE_STATUSES = frozenset(
    {
        COVERAGE_OK,
        COVERAGE_ZERO,
        COVERAGE_NOT_COLLECTED,
        COVERAGE_NO_RECORDS,
        COVERAGE_TRUNCATED,
    }
)
MARKET_COVERAGE_STATUSES = frozenset({COVERAGE_OK, COVERAGE_MISSING_BAR})

# Topic→instrument relationship vocabulary (research assumptions, versioned).
RELATIONSHIPS = frozenset(
    {"sector_etf", "constituent", "broad_market", "thematic", "benchmark", "related"}
)
EXPOSURE_TYPES = frozenset(
    {"direct_sector", "company", "broad_index", "thematic_basket", "benchmark"}
)


def _req_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _the_date(value: date, field_name: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{field_name} must be a date")
    return value


def _json_map(value: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministically JSON-ready a mapping (sorted keys applied at dump time)."""
    return {str(k): to_json_value(v) for k, v in value.items()}


# --- News layers -------------------------------------------------------------


@dataclass(frozen=True)
class NewsTopicDailyFeature:
    """One topic measured over one news-feature calendar date.

    Grain: (feature_date, topic, provider, news_measurement_method, feature_version,
    topic_taxonomy_version, query_specification_hash). Contains only news
    information; no forward market data lives here.
    """

    feature_date: date
    topic: str
    provider: str
    news_measurement_method: str
    feature_version: str
    topic_taxonomy_version: str
    query_specification_hash: str
    feature_timezone: str
    # observation fields (None => not measurable on this method; see capabilities)
    title_deduplicated_article_count: int | None
    observed_article_count: int
    observed_copy_count: int | None
    unique_domains: int | None
    unique_source_countries: int | None
    primary_topic_count: int | None
    multi_topic_article_count: int | None
    query_count: int
    collection_run_count: int
    earliest_published_at: datetime | None
    latest_published_at: datetime | None
    observed_copy_domain_counts: Mapping[str, int]
    observed_copy_country_counts: Mapping[str, int]
    duplicate_group_count: int | None
    largest_duplicate_group: int | None
    # availability / quality
    feature_available_at: datetime
    source_observed_at: datetime | None
    ingested_at: datetime
    availability_assumption: str
    coverage_status: str
    response_truncated: bool
    feature_capabilities: Mapping[str, bool]

    def __post_init__(self) -> None:
        object.__setattr__(self, "feature_date", _the_date(self.feature_date, "feature_date"))
        object.__setattr__(self, "topic", _req_text(self.topic, "topic"))
        object.__setattr__(self, "provider", _req_text(self.provider, "provider"))
        if self.news_measurement_method not in NEWS_MEASUREMENT_METHODS:
            raise ValueError(f"unknown news_measurement_method: {self.news_measurement_method!r}")
        if self.coverage_status not in NEWS_COVERAGE_STATUSES:
            raise ValueError(f"unknown coverage_status: {self.coverage_status!r}")
        if (
            self.title_deduplicated_article_count is not None
            and self.title_deduplicated_article_count < 0
        ):
            raise ValueError("title_deduplicated_article_count must be non-negative")
        if self.observed_article_count < 0:
            raise ValueError("observed_article_count must be non-negative")
        if self.availability_assumption not in AVAILABILITY_ASSUMPTIONS:
            raise ValueError(f"unknown availability_assumption: {self.availability_assumption!r}")
        object.__setattr__(
            self, "feature_available_at", _utc(self.feature_available_at, "feature_available_at")
        )
        if self.source_observed_at is not None:
            object.__setattr__(
                self, "source_observed_at", _utc(self.source_observed_at, "source_observed_at")
            )
        object.__setattr__(self, "ingested_at", _utc(self.ingested_at, "ingested_at"))
        if self.earliest_published_at is not None:
            object.__setattr__(
                self, "earliest_published_at", _utc(self.earliest_published_at, "earliest")
            )
        if self.latest_published_at is not None:
            object.__setattr__(
                self, "latest_published_at", _utc(self.latest_published_at, "latest")
            )
        object.__setattr__(
            self, "observed_copy_domain_counts", dict(self.observed_copy_domain_counts)
        )
        object.__setattr__(
            self, "observed_copy_country_counts", dict(self.observed_copy_country_counts)
        )
        object.__setattr__(self, "feature_capabilities", dict(self.feature_capabilities))

    @property
    def logical_key(self) -> tuple[object, ...]:
        return (
            self.feature_date.isoformat(),
            self.topic,
            self.provider,
            self.news_measurement_method,
            self.feature_version,
            self.topic_taxonomy_version,
            self.query_specification_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_date": self.feature_date.isoformat(),
            "topic": self.topic,
            "provider": self.provider,
            "news_measurement_method": self.news_measurement_method,
            "feature_version": self.feature_version,
            "topic_taxonomy_version": self.topic_taxonomy_version,
            "query_specification_hash": self.query_specification_hash,
            "feature_timezone": self.feature_timezone,
            "title_deduplicated_article_count": self.title_deduplicated_article_count,
            "observed_article_count": self.observed_article_count,
            "observed_copy_count": self.observed_copy_count,
            "unique_domains": self.unique_domains,
            "unique_source_countries": self.unique_source_countries,
            "primary_topic_count": self.primary_topic_count,
            "multi_topic_article_count": self.multi_topic_article_count,
            "query_count": self.query_count,
            "collection_run_count": self.collection_run_count,
            "earliest_published_at": (
                to_json_value(self.earliest_published_at) if self.earliest_published_at else None
            ),
            "latest_published_at": (
                to_json_value(self.latest_published_at) if self.latest_published_at else None
            ),
            "observed_copy_domain_counts": dict(sorted(self.observed_copy_domain_counts.items())),
            "observed_copy_country_counts": dict(sorted(self.observed_copy_country_counts.items())),
            "duplicate_group_count": self.duplicate_group_count,
            "largest_duplicate_group": self.largest_duplicate_group,
            "feature_available_at": to_json_value(self.feature_available_at),
            "source_observed_at": (
                to_json_value(self.source_observed_at) if self.source_observed_at else None
            ),
            "ingested_at": to_json_value(self.ingested_at),
            "availability_assumption": self.availability_assumption,
            "coverage_status": self.coverage_status,
            "response_truncated": self.response_truncated,
            "feature_capabilities": dict(sorted(self.feature_capabilities.items())),
        }


@dataclass(frozen=True)
class SessionNewsFeature:
    """News assigned to one market session under one alignment policy.

    Several calendar feature dates (e.g. Fri/Sat/Sun) can map to one session; the
    contributing dates are recorded rather than silently duplicated.
    """

    session_date: date
    topic: str
    provider: str
    news_measurement_method: str
    feature_version: str
    alignment_policy: str
    feature_window_start: datetime
    feature_window_end: datetime
    feature_available_at: datetime
    feature_cutoff: datetime
    target_session_open: datetime
    cutoff_buffer_seconds: int
    availability_assumption: str
    alignment_precision: str
    alignment_downgraded: bool
    alignment_warning: str | None
    market_timezone: str
    source_feature_dates: tuple[str, ...]
    source_date_count: int
    calendar_days_aggregated: int
    inputs: Mapping[str, Any]
    coverage_status: str
    feature_capabilities: Mapping[str, bool]
    quality_flags: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_date", _the_date(self.session_date, "session_date"))
        object.__setattr__(self, "topic", _req_text(self.topic, "topic"))
        object.__setattr__(
            self, "feature_window_start", _utc(self.feature_window_start, "feature_window_start")
        )
        object.__setattr__(
            self, "feature_window_end", _utc(self.feature_window_end, "feature_window_end")
        )
        object.__setattr__(
            self, "feature_available_at", _utc(self.feature_available_at, "feature_available_at")
        )
        object.__setattr__(self, "feature_cutoff", _utc(self.feature_cutoff, "feature_cutoff"))
        object.__setattr__(
            self, "target_session_open", _utc(self.target_session_open, "target_session_open")
        )
        if self.feature_window_start > self.feature_window_end:
            raise ValueError("feature_window_start must not be after feature_window_end")
        if self.feature_available_at < self.feature_window_end:
            raise ValueError("feature_available_at must be at or after feature_window_end")
        if self.cutoff_buffer_seconds < 0:
            raise ValueError("cutoff_buffer_seconds must be non-negative")
        if self.feature_cutoff > self.target_session_open:
            raise ValueError("feature_cutoff must not be after target_session_open")
        # Equality is deliberately allowed: the buffer separates the completed
        # feature cutoff from the target-session open.
        if self.feature_available_at > self.feature_cutoff:
            raise ValueError("feature_available_at must be at or before feature_cutoff")
        if self.availability_assumption not in AVAILABILITY_ASSUMPTIONS:
            raise ValueError(f"unknown availability_assumption: {self.availability_assumption!r}")
        object.__setattr__(self, "source_feature_dates", tuple(self.source_feature_dates))
        object.__setattr__(self, "inputs", dict(self.inputs))
        object.__setattr__(self, "feature_capabilities", dict(self.feature_capabilities))
        object.__setattr__(self, "quality_flags", dict(self.quality_flags))

    @property
    def logical_key(self) -> tuple[object, ...]:
        return (
            self.session_date.isoformat(),
            self.topic,
            self.provider,
            self.news_measurement_method,
            self.feature_version,
            self.alignment_policy,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_date": self.session_date.isoformat(),
            "topic": self.topic,
            "provider": self.provider,
            "news_measurement_method": self.news_measurement_method,
            "feature_version": self.feature_version,
            "alignment_policy": self.alignment_policy,
            "feature_window_start": to_json_value(self.feature_window_start),
            "feature_window_end": to_json_value(self.feature_window_end),
            "feature_available_at": to_json_value(self.feature_available_at),
            "feature_cutoff": to_json_value(self.feature_cutoff),
            "target_session_open": to_json_value(self.target_session_open),
            "cutoff_buffer_seconds": self.cutoff_buffer_seconds,
            "availability_assumption": self.availability_assumption,
            "alignment_precision": self.alignment_precision,
            "alignment_downgraded": self.alignment_downgraded,
            "alignment_warning": self.alignment_warning,
            "market_timezone": self.market_timezone,
            "source_feature_dates": list(self.source_feature_dates),
            "source_date_count": self.source_date_count,
            "calendar_days_aggregated": self.calendar_days_aggregated,
            "inputs": _json_map(self.inputs),
            "coverage_status": self.coverage_status,
            "feature_capabilities": dict(sorted(self.feature_capabilities.items())),
            "quality_flags": _json_map(self.quality_flags),
        }


# --- Market layer ------------------------------------------------------------


@dataclass(frozen=True)
class MarketSessionFeature:
    """One instrument during one market session under one homogeneous bar identity."""

    instrument_id: str
    symbol: str
    session_date: date
    provider: str
    feed: str
    adjustment: str
    currency: str
    timeframe: str
    feature_version: str
    session_open: datetime
    session_close: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    trade_count: int | None
    vwap: float | None
    inputs: Mapping[str, Any]
    coverage_status: str
    quality_flags: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument_id", _req_text(self.instrument_id, "instrument_id"))
        object.__setattr__(self, "symbol", _req_text(self.symbol, "symbol").upper())
        object.__setattr__(self, "session_date", _the_date(self.session_date, "session_date"))
        object.__setattr__(self, "session_open", _utc(self.session_open, "session_open"))
        object.__setattr__(self, "session_close", _utc(self.session_close, "session_close"))
        if self.coverage_status not in MARKET_COVERAGE_STATUSES:
            raise ValueError(f"unknown market coverage_status: {self.coverage_status!r}")
        object.__setattr__(self, "inputs", dict(self.inputs))
        object.__setattr__(self, "quality_flags", dict(self.quality_flags))

    @property
    def logical_key(self) -> tuple[object, ...]:
        return (
            self.instrument_id,
            self.symbol,
            self.session_date.isoformat(),
            self.provider,
            self.feed,
            self.adjustment,
            self.currency,
            self.timeframe,
            self.feature_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "symbol": self.symbol,
            "session_date": self.session_date.isoformat(),
            "provider": self.provider,
            "feed": self.feed,
            "adjustment": self.adjustment,
            "currency": self.currency,
            "timeframe": self.timeframe,
            "feature_version": self.feature_version,
            "session_open": to_json_value(self.session_open),
            "session_close": to_json_value(self.session_close),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "trade_count": self.trade_count,
            "vwap": self.vwap,
            "inputs": _json_map(self.inputs),
            "coverage_status": self.coverage_status,
            "quality_flags": _json_map(self.quality_flags),
        }


# --- Reference data ----------------------------------------------------------


@dataclass(frozen=True)
class TopicInstrumentMapping:
    """One versioned research relationship between a topic and an instrument.

    These are researcher-defined assumptions, not objective truth, and are
    versioned so a later revision does not silently rewrite history.

    ``instrument_id`` is the durable internal identity. ``symbol`` is only a
    date-valid market identifier; ``valid_from`` / ``valid_to`` jointly bound the
    relationship and symbol assignment. V1 is not a security-master replacement.
    """

    mapping_version: str
    topic: str
    instrument_id: str
    symbol: str
    relationship: str
    exposure_type: str
    weight: float
    benchmark_symbol: str
    sector_benchmark_symbol: str | None
    valid_from: date | None
    valid_to: date | None
    research_rationale: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "mapping_version", _req_text(self.mapping_version, "mapping_ver"))
        object.__setattr__(self, "topic", _req_text(self.topic, "topic"))
        object.__setattr__(self, "instrument_id", _req_text(self.instrument_id, "instrument_id"))
        object.__setattr__(self, "symbol", _req_text(self.symbol, "symbol").upper())
        if self.relationship not in RELATIONSHIPS:
            raise ValueError(f"unknown relationship: {self.relationship!r}")
        if self.exposure_type not in EXPOSURE_TYPES:
            raise ValueError(f"unknown exposure_type: {self.exposure_type!r}")
        if not isinstance(self.weight, (int, float)) or isinstance(self.weight, bool):
            raise ValueError("weight must be numeric")
        if not 0.0 < float(self.weight) <= 1.0:
            raise ValueError("weight must be in (0, 1]")
        object.__setattr__(self, "weight", float(self.weight))
        object.__setattr__(
            self, "benchmark_symbol", _req_text(self.benchmark_symbol, "benchmark_symbol").upper()
        )
        if self.sector_benchmark_symbol is not None:
            object.__setattr__(
                self, "sector_benchmark_symbol", self.sector_benchmark_symbol.strip().upper()
            )
        if self.valid_from is not None:
            object.__setattr__(self, "valid_from", _the_date(self.valid_from, "valid_from"))
        if self.valid_to is not None:
            object.__setattr__(self, "valid_to", _the_date(self.valid_to, "valid_to"))
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from must not be after valid_to")

    def is_active_on(self, day: date) -> bool:
        if self.valid_from is not None and day < self.valid_from:
            return False
        if self.valid_to is not None and day > self.valid_to:
            return False
        return True

    @property
    def logical_key(self) -> tuple[object, ...]:
        return (self.mapping_version, self.topic, self.instrument_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mapping_version": self.mapping_version,
            "topic": self.topic,
            "instrument_id": self.instrument_id,
            "symbol": self.symbol,
            "relationship": self.relationship,
            "exposure_type": self.exposure_type,
            "weight": self.weight,
            "benchmark_symbol": self.benchmark_symbol,
            "sector_benchmark_symbol": self.sector_benchmark_symbol,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "research_rationale": self.research_rationale,
        }


# --- Joined research observation ---------------------------------------------


@dataclass(frozen=True)
class NewsMarketObservation:
    """One topic × instrument × aligned session × cutoff × dataset version.

    Field groups are kept in separate named sub-objects so an input can never be
    confused with a target:

    - ``inputs``: legally usable strictly before the target period.
    - ``contemporaneous``: describes the target session; NOT a predictor for it.
    - ``targets``: computed from the target session and later sessions only.
    - ``quality`` / ``lineage``: provenance and missingness.
    """

    dataset_version: str
    observation_id: str
    topic: str
    instrument_id: str
    symbol: str
    session_date: date
    feature_available_at: datetime
    target_session_open: datetime
    feature_cutoff: datetime
    cutoff_buffer_seconds: int
    availability_assumption: str
    alignment_precision: str
    alignment_policy: str
    mapping_version: str
    news_measurement_method: str
    market_provider: str
    feed: str
    adjustment: str
    currency: str
    timeframe: str
    inputs: Mapping[str, Any]
    contemporaneous: Mapping[str, Any]
    targets: Mapping[str, Any]
    quality: Mapping[str, Any]
    lineage: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_date", _the_date(self.session_date, "session_date"))
        object.__setattr__(
            self, "feature_available_at", _utc(self.feature_available_at, "feature_available_at")
        )
        object.__setattr__(
            self, "target_session_open", _utc(self.target_session_open, "target_session_open")
        )
        object.__setattr__(self, "feature_cutoff", _utc(self.feature_cutoff, "feature_cutoff"))
        if self.cutoff_buffer_seconds < 0:
            raise ValueError("cutoff_buffer_seconds must be non-negative")
        if self.feature_cutoff > self.target_session_open:
            raise ValueError("feature_cutoff must not be after target_session_open")
        if self.availability_assumption not in AVAILABILITY_ASSUMPTIONS:
            raise ValueError(f"unknown availability_assumption: {self.availability_assumption!r}")
        # Equality is allowed: the feature may be complete exactly at the
        # conservative cutoff, which is itself before the target open by default.
        if self.feature_available_at > self.feature_cutoff:
            raise ValueError(
                "leakage: feature_available_at is after feature_cutoff "
                f"({self.feature_available_at} > {self.feature_cutoff})"
            )
        for name in ("inputs", "contemporaneous", "targets", "quality", "lineage"):
            object.__setattr__(self, name, dict(getattr(self, name)))
        forbidden_input_prefixes = ("target_", "forward_", "fwd_", "next_session")
        for key in self.inputs:
            if key.startswith(forbidden_input_prefixes):
                raise ValueError(f"target-like field {key!r} cannot appear in inputs")
        overlap = set(self.inputs).intersection(self.contemporaneous)
        if overlap:
            raise ValueError(
                f"fields cannot be both input and contemporaneous: {sorted(overlap)!r}"
            )
        for key in self.targets:
            if not key.startswith("target_"):
                raise ValueError(f"target field {key!r} must use the 'target_' namespace")
        identity_lineage = {
            "mapping_version": self.mapping_version,
            "alignment_policy": self.alignment_policy,
            "news_measurement_method": self.news_measurement_method,
        }
        for key, expected in identity_lineage.items():
            actual = self.lineage.get(key)
            if actual is not None and actual != expected:
                raise ValueError(
                    f"lineage {key!r} conflicts with row identity: {actual!r} != {expected!r}"
                )

    @property
    def logical_key(self) -> tuple[object, ...]:
        return (
            self.dataset_version,
            self.topic,
            self.instrument_id,
            self.session_date.isoformat(),
            self.alignment_policy,
            self.mapping_version,
            self.news_measurement_method,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_version": self.dataset_version,
            "observation_id": self.observation_id,
            "topic": self.topic,
            "instrument_id": self.instrument_id,
            "symbol": self.symbol,
            "session_date": self.session_date.isoformat(),
            "feature_available_at": to_json_value(self.feature_available_at),
            "target_session_open": to_json_value(self.target_session_open),
            "feature_cutoff": to_json_value(self.feature_cutoff),
            "cutoff_buffer_seconds": self.cutoff_buffer_seconds,
            "availability_assumption": self.availability_assumption,
            "alignment_precision": self.alignment_precision,
            "alignment_policy": self.alignment_policy,
            "mapping_version": self.mapping_version,
            "news_measurement_method": self.news_measurement_method,
            "market_provider": self.market_provider,
            "feed": self.feed,
            "adjustment": self.adjustment,
            "currency": self.currency,
            "timeframe": self.timeframe,
            "inputs": _json_map(self.inputs),
            "contemporaneous": _json_map(self.contemporaneous),
            "targets": _json_map(self.targets),
            "quality": _json_map(self.quality),
            "lineage": _json_map(self.lineage),
        }


@dataclass(frozen=True)
class DatasetManifest:
    """Reproducibility metadata for one built dataset.

    Contains no secrets and no machine-specific absolute paths.
    """

    dataset_name: str
    dataset_version: str
    builder_version: str
    config_fingerprint: str
    generated_at: datetime
    alignment_policy: str
    alignment_policies: tuple[str, ...]
    market_timezone: str
    market_calendar_name: str
    cutoff_buffer_seconds: int
    availability_assumptions: tuple[str, ...]
    alignment_precision_counts: Mapping[str, int]
    feature_catalog_version: str
    topic_taxonomy_version: str
    mapping_version: str
    news_measurement_methods: tuple[str, ...]
    source_schema_versions: Mapping[str, str]
    included_topics: tuple[str, ...]
    included_instruments: tuple[str, ...]
    benchmark_symbols: tuple[str, ...]
    session_date_start: str | None
    session_date_end: str | None
    row_counts: Mapping[str, int]
    missingness_counts: Mapping[str, int]
    exclusion_counts: Mapping[str, int]
    coverage_warnings: tuple[str, ...]
    bootstrap_unit: str
    bootstrap_block_length: int
    bootstrap_seed: int
    bootstrap_iterations: int
    hypothesis_registry_version: str
    splits: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", _utc(self.generated_at, "generated_at"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_name": self.dataset_name,
            "dataset_version": self.dataset_version,
            "builder_version": self.builder_version,
            "config_fingerprint": self.config_fingerprint,
            "generated_at": to_json_value(self.generated_at),
            "alignment_policy": self.alignment_policy,
            "alignment_policies": list(self.alignment_policies),
            "market_timezone": self.market_timezone,
            "market_calendar_name": self.market_calendar_name,
            "cutoff_buffer_seconds": self.cutoff_buffer_seconds,
            "availability_assumptions": list(self.availability_assumptions),
            "alignment_precision_counts": dict(sorted(self.alignment_precision_counts.items())),
            "feature_catalog_version": self.feature_catalog_version,
            "topic_taxonomy_version": self.topic_taxonomy_version,
            "mapping_version": self.mapping_version,
            "news_measurement_methods": list(self.news_measurement_methods),
            "source_schema_versions": dict(sorted(self.source_schema_versions.items())),
            "included_topics": list(self.included_topics),
            "included_instruments": list(self.included_instruments),
            "benchmark_symbols": list(self.benchmark_symbols),
            "session_date_start": self.session_date_start,
            "session_date_end": self.session_date_end,
            "row_counts": dict(sorted(self.row_counts.items())),
            "missingness_counts": dict(sorted(self.missingness_counts.items())),
            "exclusion_counts": dict(sorted(self.exclusion_counts.items())),
            "coverage_warnings": list(self.coverage_warnings),
            "bootstrap_unit": self.bootstrap_unit,
            "bootstrap_block_length": self.bootstrap_block_length,
            "bootstrap_seed": self.bootstrap_seed,
            "bootstrap_iterations": self.bootstrap_iterations,
            "hypothesis_registry_version": self.hypothesis_registry_version,
            "splits": _json_map(self.splits),
        }

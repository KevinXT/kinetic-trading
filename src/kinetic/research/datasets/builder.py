"""End-to-end research-dataset builder (pure, offline, deterministic).

Given committed article/count/bar inputs, reference mappings, a feature catalog,
and a clock, this produces every normalized layer plus the joined observations,
manifest, chronological splits, and event study — without any network access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Sequence

from kinetic.data.catalog.features import FeatureCatalog
from kinetic.data.catalog.manifest import build_manifest
from kinetic.data.schemas.market import PriceBar
from kinetic.data.schemas.research import (
    DatasetManifest,
    MarketSessionFeature,
    NewsMarketObservation,
    NewsTopicDailyFeature,
    SessionNewsFeature,
    TopicInstrumentMapping,
)
from kinetic.processing.cross_asset.alignment import (
    DEFAULT_CUTOFF_BUFFER_SECONDS,
    POLICY_CONSERVATIVE,
    POLICY_SESSION_WINDOW,
    build_alignment_policy,
)
from kinetic.processing.cross_asset.join import build_observations
from kinetic.processing.cross_asset.validation import assert_catalog_categories, assert_no_leakage
from kinetic.processing.market.calendar import CALENDAR_NAME, MarketCalendar
from kinetic.processing.market.features import build_market_series
from kinetic.processing.news.features import build_news_features
from kinetic.research.event_studies.event_study import (
    HYPOTHESIS_REGISTRY_VERSION,
    EventStudyConfig,
    run_event_study,
)

MARKET_SCHEMA_VERSION = "financial-data.v1"


@dataclass(frozen=True)
class DatasetResult:
    news_topic_daily_features: list[NewsTopicDailyFeature]
    session_news_features: list[SessionNewsFeature]
    market_session_features: list[MarketSessionFeature]
    observations: list[NewsMarketObservation]
    manifest: DatasetManifest
    event_study_summary: dict[str, Any]
    event_study_events: list[dict[str, Any]]
    splits: dict[str, Any]
    split_of: dict[str, str]


def assign_chronological_splits(
    session_dates: Sequence[date],
    *,
    embargo: int,
    ratios: tuple[float, float, float] = (0.6, 0.2, 0.2),
) -> tuple[dict[str, Any], dict[str, str]]:
    """Assign development/validation/holdout partitions chronologically.

    Time series are never split randomly. An embargo of ``embargo`` sessions at
    each internal boundary is labelled separately so overlapping forward labels do
    not straddle a partition boundary.
    """
    unique = sorted(set(session_dates))
    n = len(unique)
    split_of: dict[str, str] = {}
    if n == 0:
        return (
            {
                "policy": "chronological_60_20_20_with_embargo",
                "embargo_sessions": embargo,
                "development": None,
                "validation": None,
                "holdout": None,
                "counts": {},
            },
            split_of,
        )
    dev_end = int(n * ratios[0])
    val_end = int(n * (ratios[0] + ratios[1]))
    labels: dict[str, str] = {}
    for i, day in enumerate(unique):
        if i < dev_end:
            labels[day.isoformat()] = "development"
        elif i < val_end:
            labels[day.isoformat()] = "validation"
        else:
            labels[day.isoformat()] = "holdout"
    # Embargo the last `embargo` sessions of development and validation.
    for boundary in (dev_end, val_end):
        for j in range(max(0, boundary - embargo), boundary):
            if j < n:
                labels[unique[j].isoformat()] = "embargo"
    split_of = labels

    def _range(name: str) -> dict[str, str] | None:
        days = sorted(d for d, lab in labels.items() if lab == name)
        if not days:
            return None
        return {"start": days[0], "end": days[-1]}

    counts: dict[str, int] = {}
    for lab in labels.values():
        counts[lab] = counts.get(lab, 0) + 1

    return (
        {
            "policy": "chronological_60_20_20_with_embargo",
            "embargo_sessions": embargo,
            "development": _range("development"),
            "validation": _range("validation"),
            "holdout": _range("holdout"),
            "embargo": _range("embargo"),
            "counts": counts,
            "note": (
                "Holdout must not be inspected while selecting features or "
                "thresholds. No model is trained in this milestone."
            ),
        },
        split_of,
    )


def _to_price_bar(record: Any) -> PriceBar:
    if isinstance(record, PriceBar):
        return record
    if not isinstance(record, dict):
        raise TypeError("bar records must be PriceBar or dict")

    def _ts(value: Any) -> datetime:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)

    return PriceBar(
        symbol=str(record["symbol"]),
        timestamp=_ts(record["timestamp"]),
        timeframe=str(record["timeframe"]),
        open=float(record["open"]),
        high=float(record["high"]),
        low=float(record["low"]),
        close=float(record["close"]),
        volume=int(record["volume"]),
        vwap=(float(record["vwap"]) if record.get("vwap") is not None else None),
        trade_count=(int(record["trade_count"]) if record.get("trade_count") is not None else None),
        provider=str(record.get("provider", "alpaca")),
        feed=str(record.get("feed", "iex")),
        adjustment=str(record.get("adjustment", "all")),
        currency=str(record.get("currency", "USD")),
        retrieved_at=_ts(record.get("retrieved_at", record["timestamp"])),
    )


def build_dataset(
    *,
    dataset_version: str,
    feature_version: str,
    topic_taxonomy_version: str,
    calendar: MarketCalendar,
    catalog: FeatureCatalog,
    mapping_version: str,
    mappings: tuple[TopicInstrumentMapping, ...],
    bars: Sequence[Any],
    articles: Sequence[dict[str, Any]] | None = None,
    count_rows: Sequence[dict[str, Any]] | None = None,
    alignment_policy_name: str,
    cutoff_buffer_seconds: int = DEFAULT_CUTOFF_BUFFER_SECONDS,
    allow_alignment_downgrade: bool = False,
    observed_at: datetime,
    generated_at: datetime,
    forward_horizon: int = 5,
    max_records: int | None = None,
    event_config: EventStudyConfig | None = None,
    config_snapshot: dict[str, Any] | None = None,
) -> DatasetResult:
    """Build the full dataset deterministically from offline inputs."""
    if forward_horizon != 5:
        raise ValueError("Core V2 supports forward_horizon=5 only")
    policy = build_alignment_policy(
        alignment_policy_name,
        calendar,
        cutoff_buffer_seconds=cutoff_buffer_seconds,
    )
    restrict_topics = frozenset(m.topic for m in mappings) or None

    price_bars = [_to_price_bar(b) for b in bars]
    market_series = build_market_series(
        price_bars,
        feature_version=feature_version,
        session_open_fn=calendar.session_open,
        session_close_fn=calendar.session_close,
    )

    daily: list[NewsTopicDailyFeature] = []
    sessions: list[SessionNewsFeature] = []
    methods: list[str] = []

    if articles:
        d_doc, s_doc = build_news_features(
            method="gdelt_doc_artlist",
            policy=policy,
            calendar=calendar,
            provider="gdelt",
            feature_version=feature_version,
            topic_taxonomy_version=topic_taxonomy_version,
            observed_at=observed_at,
            articles=articles,
            restrict_topics=restrict_topics,
            max_records=max_records,
        )
        daily.extend(d_doc)
        sessions.extend(s_doc)
        methods.append("gdelt_doc_artlist")

    if count_rows:
        bq_policy = policy
        alignment_downgraded = False
        alignment_warning: str | None = None
        if policy.name == POLICY_SESSION_WINDOW:
            if not allow_alignment_downgrade:
                raise ValueError(
                    "BigQuery daily counts cannot use session_information_window_v2; "
                    "set alignment_policy='conservative_calendar_day_v2' or explicitly "
                    "set allow_alignment_downgrade=true"
                )
            bq_policy = build_alignment_policy(
                POLICY_CONSERVATIVE,
                calendar,
                cutoff_buffer_seconds=cutoff_buffer_seconds,
            )
            alignment_downgraded = True
            alignment_warning = (
                "BigQuery daily counts were downgraded from exact article-timestamp "
                "alignment to a conservative daily approximation."
            )
        d_bq, s_bq = build_news_features(
            method="bigquery_gdelt_counts",
            policy=bq_policy,
            calendar=calendar,
            provider="bigquery_gdelt",
            feature_version=feature_version,
            topic_taxonomy_version=topic_taxonomy_version,
            observed_at=observed_at,
            count_rows=count_rows,
            restrict_topics=restrict_topics,
            alignment_downgraded=alignment_downgraded,
            alignment_warning=alignment_warning,
        )
        daily.extend(d_bq)
        sessions.extend(s_bq)
        methods.append("bigquery_gdelt_counts")

    join_result = build_observations(
        session_news=sessions,
        market_series=market_series,
        benchmark_series=market_series,
        mappings=mappings,
        calendar=calendar,
        dataset_version=dataset_version,
        forward_horizon=forward_horizon,
    )
    observations = join_result.observations
    assert_no_leakage(observations)
    assert_catalog_categories(observations, catalog)

    session_dates = [o.session_date for o in observations]
    splits, split_of = assign_chronological_splits(session_dates, embargo=forward_horizon)

    ev_config = event_config or EventStudyConfig(forward_horizon=forward_horizon)
    event_summary, events = run_event_study(observations, ev_config)

    benchmark_symbols = tuple(sorted({m.benchmark_symbol for m in mappings}))
    config_for_fingerprint = dict(config_snapshot or {})
    config_for_fingerprint.update(
        {
            "dataset_version": dataset_version,
            "feature_version": feature_version,
            "topic_taxonomy_version": topic_taxonomy_version,
            "mapping_version": mapping_version,
            "alignment_policy": policy.name,
            "cutoff_buffer_seconds": cutoff_buffer_seconds,
            "allow_alignment_downgrade": allow_alignment_downgrade,
            "forward_horizon": forward_horizon,
            "catalog_version": catalog.catalog_version,
            "event_threshold": ev_config.threshold,
            "event_attention_field": ev_config.attention_field,
        }
    )

    manifest = build_manifest(
        dataset_version=dataset_version,
        config=config_for_fingerprint,
        generated_at=generated_at,
        alignment_policy=policy.name,
        market_calendar_name=CALENDAR_NAME,
        cutoff_buffer_seconds=cutoff_buffer_seconds,
        market_timezone=str(calendar.tz),
        feature_catalog_version=catalog.catalog_version,
        topic_taxonomy_version=topic_taxonomy_version,
        mapping_version=mapping_version,
        news_measurement_methods=tuple(methods),
        source_schema_versions={
            "market": MARKET_SCHEMA_VERSION,
            "news_feature": feature_version,
        },
        observations=observations,
        benchmark_symbols=benchmark_symbols,
        exclusion_counts=join_result.exclusions,
        splits=splits,
        bootstrap_unit=ev_config.bootstrap_unit,
        bootstrap_block_length=ev_config.bootstrap_block_length,
        bootstrap_seed=ev_config.seed,
        bootstrap_iterations=ev_config.bootstrap_iterations,
        hypothesis_registry_version=HYPOTHESIS_REGISTRY_VERSION,
        extra_warnings=tuple(
            sorted(
                {
                    str(session.alignment_warning)
                    for session in sessions
                    if session.alignment_warning
                }
            )
        ),
    )

    daily.sort(key=lambda d: (d.feature_date.isoformat(), d.topic, d.news_measurement_method))
    sessions.sort(key=lambda s: (s.session_date.isoformat(), s.topic, s.news_measurement_method))
    market_features = market_series.features()
    market_features.sort(key=lambda m: (m.symbol, m.session_date.isoformat()))

    return DatasetResult(
        news_topic_daily_features=daily,
        session_news_features=sessions,
        market_session_features=market_features,
        observations=observations,
        manifest=manifest,
        event_study_summary=event_summary,
        event_study_events=events,
        splits=splits,
        split_of=split_of,
    )

"""Dataset manifest construction and reproducibility fingerprinting."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from research_data.models import DatasetManifest, NewsMarketObservation

BUILDER_VERSION = "news-market-builder-v2"
DATASET_NAME = "news_market_observations"


def config_fingerprint(config: dict[str, Any]) -> str:
    """Stable fingerprint of the builder configuration.

    Secrets and machine-specific absolute paths must never be part of the config
    passed here; the caller is responsible for excluding them.
    """
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)
    return "cfg-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def build_manifest(
    *,
    dataset_version: str,
    config: dict[str, Any],
    generated_at: datetime,
    alignment_policy: str,
    market_calendar_name: str,
    cutoff_buffer_seconds: int,
    market_timezone: str,
    feature_catalog_version: str,
    topic_taxonomy_version: str,
    mapping_version: str,
    news_measurement_methods: tuple[str, ...],
    source_schema_versions: dict[str, str],
    observations: list[NewsMarketObservation],
    benchmark_symbols: tuple[str, ...],
    exclusion_counts: dict[str, int],
    splits: dict[str, Any],
    bootstrap_unit: str,
    bootstrap_block_length: int,
    bootstrap_seed: int,
    bootstrap_iterations: int,
    hypothesis_registry_version: str,
    extra_warnings: tuple[str, ...] = (),
) -> DatasetManifest:
    topics = sorted({o.topic for o in observations})
    instruments = sorted({o.instrument_id for o in observations})
    session_dates = sorted({o.session_date for o in observations})

    missingness: dict[str, int] = {}
    for obs in observations:
        market_status = str(obs.quality.get("market_coverage_status"))
        missingness[f"market::{market_status}"] = missingness.get(f"market::{market_status}", 0) + 1
        news_status = str(obs.quality.get("news_coverage_status"))
        missingness[f"news::{news_status}"] = missingness.get(f"news::{news_status}", 0) + 1
        if not obs.quality.get("target_through_plus_4_complete", False):
            missingness["target::through_plus_4_incomplete"] = (
                missingness.get("target::through_plus_4_incomplete", 0) + 1
            )
        if not obs.quality.get("history_sufficient", False):
            missingness["input::market_history_insufficient"] = (
                missingness.get("input::market_history_insufficient", 0) + 1
            )

    warnings: list[str] = list(extra_warnings)
    if not observations:
        warnings.append("no observations were produced; check coverage and mappings")
    truncated = sum(1 for o in observations if o.quality.get("response_truncated"))
    if truncated:
        warnings.append(
            f"{truncated} observation(s) derive from a truncated provider response; "
            "counts may be censored by the DOC record limit"
        )
    approximate = sum(
        1
        for observation in observations
        if observation.alignment_precision == "daily_approximation"
    )
    if approximate:
        warnings.append(
            f"{approximate} observation(s) use conservative daily alignment rather than "
            "article-timestamp alignment"
        )

    row_counts = {
        "news_market_observations": len(observations),
        "topics": len(topics),
        "instruments": len(instruments),
        "session_dates": len(session_dates),
    }

    def iso(d: date | None) -> str | None:
        return d.isoformat() if d is not None else None

    return DatasetManifest(
        dataset_name=DATASET_NAME,
        dataset_version=dataset_version,
        builder_version=BUILDER_VERSION,
        config_fingerprint=config_fingerprint(config),
        generated_at=generated_at,
        alignment_policy=alignment_policy,
        alignment_policies=tuple(sorted({o.alignment_policy for o in observations})),
        market_timezone=market_timezone,
        market_calendar_name=market_calendar_name,
        cutoff_buffer_seconds=cutoff_buffer_seconds,
        availability_assumptions=tuple(sorted({o.availability_assumption for o in observations})),
        alignment_precision_counts={
            precision: sum(
                1 for observation in observations if observation.alignment_precision == precision
            )
            for precision in sorted({o.alignment_precision for o in observations})
        },
        feature_catalog_version=feature_catalog_version,
        topic_taxonomy_version=topic_taxonomy_version,
        mapping_version=mapping_version,
        news_measurement_methods=news_measurement_methods,
        source_schema_versions=source_schema_versions,
        included_topics=tuple(topics),
        included_instruments=tuple(instruments),
        benchmark_symbols=benchmark_symbols,
        session_date_start=iso(session_dates[0]) if session_dates else None,
        session_date_end=iso(session_dates[-1]) if session_dates else None,
        row_counts=row_counts,
        missingness_counts=missingness,
        exclusion_counts=dict(sorted(exclusion_counts.items())),
        coverage_warnings=tuple(warnings),
        bootstrap_unit=bootstrap_unit,
        bootstrap_block_length=bootstrap_block_length,
        bootstrap_seed=bootstrap_seed,
        bootstrap_iterations=bootstrap_iterations,
        hypothesis_registry_version=hypothesis_registry_version,
        splits=splits,
    )

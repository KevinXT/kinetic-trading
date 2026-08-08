"""The composition root: where the application is assembled.

This is the only module that knows about every subsystem at once, and the only
place where a task identifier is bound to an implementation. Nothing registers
itself; importing ``kinetic`` — or any module inside it — leaves the world
exactly as it found it.

Reading this file top to bottom tells you the complete set of things this
platform can do::

    registry = build_default_registry()
    config = load_runtime_config("configs/pipelines/demo.yaml")
    run_pipeline(config, registry=registry)

Task identifiers are namespaced ``<subsystem>[.<provider>].<verb>``:

- ``market.alpaca.fetch_bars`` — a market provider call
- ``news.gdelt.fetch_articles`` — a news provider call
- ``news.gdelt.bigquery.*`` — the GDELT-over-BigQuery measurement path
- ``news.*`` without a provider — deterministic processing of news already fetched
- ``research.*`` — dataset construction and evaluation
- ``ml.relevance.*`` — relevance benchmark and annotation pilot

Provider imports are deferred into :func:`build_default_registry` rather than
done at module scope. That is not laziness for its own sake: ``import kinetic``
must stay cheap and must not require ``google-cloud-bigquery``, network access or
credentials to be present.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from kinetic.core.pipeline.context import RunContext
from kinetic.core.pipeline.registry import TaskRegistry

JsonDict = Dict[str, Any]

# ---------------------------------------------------------------------------
# Deprecated task identifiers.
#
# The single, authoritative mapping from pre-0.2 task names to current ones.
# There is no other alias table in the codebase. Configs using these names still
# run and emit a DeprecationWarning; the names are removed in 0.4.0.
# ---------------------------------------------------------------------------
LEGACY_TASK_ALIASES: Mapping[str, str] = {
    "alpaca_historical_bars": "market.alpaca.fetch_bars",
    "gdelt_docs": "news.gdelt.fetch_articles",
    "bigquery_gdelt_counts": "news.gdelt.bigquery.fetch_daily_counts",
    "bigquery_gdelt_theme_discovery": "news.gdelt.bigquery.discover_themes",
    "bigquery_gdelt_seeded_theme_discovery": "news.gdelt.bigquery.discover_seeded_themes",
    "bigquery_gdelt_seeded_theme_scoring": "news.gdelt.bigquery.score_seeded_themes",
    "filter_articles": "news.filter_articles",
    "dedupe_articles": "news.dedupe_articles",
    "tag_articles": "news.tag_articles",
    "aggregate_article_features": "news.aggregate_features",
    "store_articles": "news.store_articles",
    "store_features": "news.store_features",
    "build_news_market_dataset": "research.build_news_market_dataset",
    "build_semiconductor_relevance_benchmark": "ml.relevance.build_benchmark",
    "run_semiconductor_relevance_real_corpus_pilot": "ml.relevance.run_real_corpus_pilot",
}

ALIAS_REMOVAL_VERSION = "0.4.0"


def build_default_registry(*, include_legacy_aliases: bool = True) -> TaskRegistry:
    """Construct the task registry for the standard application.

    Args:
        include_legacy_aliases: Also accept the pre-0.2 task names listed in
            :data:`LEGACY_TASK_ALIASES`. Pass ``False`` to prove a config uses
            only current identifiers.
    """
    from kinetic.ingestion.market.alpaca.tasks import (
        alpaca_historical_bars_task,
        create_price_provider_registry,
    )
    from kinetic.ingestion.news.gdelt.bigquery.tasks.counts import bigquery_gdelt_counts_task
    from kinetic.ingestion.news.gdelt.bigquery.tasks.seeded_theme_discovery import (
        bigquery_gdelt_seeded_theme_discovery_task,
    )
    from kinetic.ingestion.news.gdelt.bigquery.tasks.seeded_theme_scoring import (
        bigquery_gdelt_seeded_theme_scoring_task,
    )
    from kinetic.ingestion.news.gdelt.bigquery.tasks.theme_discovery import (
        bigquery_gdelt_theme_discovery_task,
    )
    from kinetic.ingestion.news.gdelt.tasks import gdelt_docs_task
    from kinetic.ml.relevance.benchmark_task import (
        build_semiconductor_relevance_benchmark_task,
    )
    from kinetic.ml.relevance.pilot_task import (
        run_semiconductor_relevance_real_corpus_pilot_task,
    )
    from kinetic.processing.news.tasks.aggregate_article_features import (
        aggregate_article_features_task,
    )
    from kinetic.processing.news.tasks.dedupe_articles import dedupe_articles_task
    from kinetic.processing.news.tasks.filter_articles import filter_articles_task
    from kinetic.processing.news.tasks.store_articles import store_articles_task
    from kinetic.processing.news.tasks.store_features import store_features_task
    from kinetic.processing.news.tasks.tag_articles import tag_articles_task
    from kinetic.research.tasks import build_news_market_dataset_task

    # The Alpaca task needs a configured provider registry. Binding it here keeps
    # provider construction out of the task's signature at the call site.
    price_providers = create_price_provider_registry()

    def fetch_alpaca_bars(ctx: RunContext, params: JsonDict) -> None:
        alpaca_historical_bars_task(ctx, params, registry=price_providers)

    registry = TaskRegistry()

    # --- market ingestion -------------------------------------------------
    registry.register("market.alpaca.fetch_bars", fetch_alpaca_bars)

    # --- news ingestion ---------------------------------------------------
    registry.register("news.gdelt.fetch_articles", gdelt_docs_task)
    registry.register("news.gdelt.bigquery.fetch_daily_counts", bigquery_gdelt_counts_task)
    registry.register("news.gdelt.bigquery.discover_themes", bigquery_gdelt_theme_discovery_task)
    registry.register(
        "news.gdelt.bigquery.discover_seeded_themes",
        bigquery_gdelt_seeded_theme_discovery_task,
    )
    registry.register(
        "news.gdelt.bigquery.score_seeded_themes",
        bigquery_gdelt_seeded_theme_scoring_task,
    )

    # --- deterministic news processing ------------------------------------
    registry.register("news.filter_articles", filter_articles_task)
    registry.register("news.dedupe_articles", dedupe_articles_task)
    registry.register("news.tag_articles", tag_articles_task)
    registry.register("news.aggregate_features", aggregate_article_features_task)
    registry.register("news.store_articles", store_articles_task)
    registry.register("news.store_features", store_features_task)

    # --- research -----------------------------------------------------------
    # Also writes the event-vs-control contrast artifacts and, when configured,
    # the deterministic human-readable study report.
    registry.register("research.build_news_market_dataset", build_news_market_dataset_task)

    # --- ml ---------------------------------------------------------------
    registry.register("ml.relevance.build_benchmark", build_semiconductor_relevance_benchmark_task)
    registry.register(
        "ml.relevance.run_real_corpus_pilot",
        run_semiconductor_relevance_real_corpus_pilot_task,
    )

    if include_legacy_aliases:
        for old, current in LEGACY_TASK_ALIASES.items():
            registry.register_alias(old, current, removal_version=ALIAS_REMOVAL_VERSION)

    return registry

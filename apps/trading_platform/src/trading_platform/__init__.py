"""
Trading platform application: CLI entrypoints, task registration, and
product-specific orchestration.

This is the top-level app layer — it depends on internal packages
(``pipeline_core``, ``common``, ``news_data``, etc.) and no package should
depend on it.
"""

__version__ = "0.1.0"


def _register_tasks() -> None:
    """Populate the pipeline task registry with provider-specific tasks."""
    from market_data.providers.alpaca.task import (
        alpaca_historical_bars_task,
        create_price_provider_registry,
    )
    from news_data.task import (
        aggregate_article_features_task,
        bigquery_gdelt_counts_task,
        bigquery_gdelt_theme_discovery_task,
        dedupe_articles_task,
        filter_articles_task,
        gdelt_docs_task,
        store_articles_task,
        store_features_task,
        tag_articles_task,
    )
    from pipeline_core.tasks.registry import TASK_REGISTRY
    from research_data.task import build_news_market_dataset_task

    def registered_alpaca_historical_bars_task(ctx, params):
        return alpaca_historical_bars_task(
            ctx,
            params,
            registry=create_price_provider_registry(),
        )

    TASK_REGISTRY.setdefault("alpaca_historical_bars", registered_alpaca_historical_bars_task)
    TASK_REGISTRY.setdefault("gdelt_docs", gdelt_docs_task)
    TASK_REGISTRY.setdefault("bigquery_gdelt_counts", bigquery_gdelt_counts_task)
    TASK_REGISTRY.setdefault("bigquery_gdelt_theme_discovery", bigquery_gdelt_theme_discovery_task)
    TASK_REGISTRY.setdefault("filter_articles", filter_articles_task)
    TASK_REGISTRY.setdefault("dedupe_articles", dedupe_articles_task)
    TASK_REGISTRY.setdefault("tag_articles", tag_articles_task)
    TASK_REGISTRY.setdefault("aggregate_article_features", aggregate_article_features_task)
    TASK_REGISTRY.setdefault("store_features", store_features_task)
    TASK_REGISTRY.setdefault("store_articles", store_articles_task)
    TASK_REGISTRY.setdefault("build_news_market_dataset", build_news_market_dataset_task)


_register_tasks()

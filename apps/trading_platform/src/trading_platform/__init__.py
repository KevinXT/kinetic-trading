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
    from news_data.task import (
        aggregate_article_features_task,
        dedupe_articles_task,
        filter_articles_task,
        gdelt_docs_task,
        store_articles_task,
        store_features_task,
        tag_articles_task,
    )
    from pipeline_core.tasks.registry import TASK_REGISTRY

    TASK_REGISTRY.setdefault("gdelt_docs", gdelt_docs_task)
    TASK_REGISTRY.setdefault("filter_articles", filter_articles_task)
    TASK_REGISTRY.setdefault("dedupe_articles", dedupe_articles_task)
    TASK_REGISTRY.setdefault("tag_articles", tag_articles_task)
    TASK_REGISTRY.setdefault(
        "aggregate_article_features", aggregate_article_features_task
    )
    TASK_REGISTRY.setdefault("store_features", store_features_task)
    TASK_REGISTRY.setdefault("store_articles", store_articles_task)


_register_tasks()

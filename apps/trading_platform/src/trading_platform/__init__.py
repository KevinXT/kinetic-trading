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
    from pipeline_core.tasks.registry import TASK_REGISTRY
    from news_data.task import (
        dedupe_articles_task,
        filter_articles_task,
        gdelt_docs_task,
    )

    TASK_REGISTRY.setdefault("gdelt_docs", gdelt_docs_task)
    TASK_REGISTRY.setdefault("filter_articles", filter_articles_task)
    TASK_REGISTRY.setdefault("dedupe_articles", dedupe_articles_task)


_register_tasks()

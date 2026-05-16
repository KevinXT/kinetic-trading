from .dedupe_articles import dedupe_articles_task
from .filter_articles import filter_articles_task
from .gdelt_docs import gdelt_docs_task
from .store_articles import store_articles_task

__all__ = [
    "dedupe_articles_task",
    "filter_articles_task",
    "gdelt_docs_task",
    "store_articles_task",
]
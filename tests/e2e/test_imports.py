"""Import smoke tests for the single ``kinetic`` distribution.

The most important assertion in this file is the one that proves importing
``kinetic`` does nothing: the pre-0.2 application registered every task as a side
effect of importing the app package, which made the set of available tasks depend
on import order.
"""

from __future__ import annotations

import subprocess
import sys


def test_import_kinetic_is_side_effect_free() -> None:
    """Importing the package must not build a registry or import providers.

    Run in a subprocess so an earlier test that already imported the world cannot
    make this pass by accident.
    """
    code = (
        "import sys; import kinetic; "
        "loaded = sorted(m for m in sys.modules if m.startswith('kinetic.')); "
        "print(repr(loaded))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    loaded = eval(result.stdout.strip())  # noqa: S307 - our own repr, in-process
    assert loaded == [], f"importing kinetic pulled in submodules: {loaded}"


def test_registry_is_empty_until_bootstrap_is_called() -> None:
    from kinetic.core.pipeline.registry import TaskRegistry

    assert len(TaskRegistry()) == 0


def test_import_core() -> None:
    from kinetic.core.config import load_config
    from kinetic.core.errors import PipelineError
    from kinetic.core.pipeline.plan import parse_plan
    from kinetic.core.pipeline.runner import run_pipeline, run_pipeline_from_file

    assert callable(load_config)
    assert callable(parse_plan)
    assert callable(run_pipeline)
    assert callable(run_pipeline_from_file)
    assert issubclass(PipelineError, Exception)


def test_import_data() -> None:
    from kinetic.data.schemas import ArticleTextRecordV1, Instrument, PriceBar
    from kinetic.data.storage import JsonlFinancialDataStore

    assert PriceBar is not None
    assert Instrument is not None
    assert ArticleTextRecordV1 is not None
    assert JsonlFinancialDataStore is not None


def test_import_ingestion() -> None:
    from kinetic.ingestion.market.alpaca import AlpacaPriceProvider
    from kinetic.ingestion.news.gdelt import GdeltDocClient
    from kinetic.ingestion.protocols import PriceDataProvider
    from kinetic.ingestion.registry import ProviderRegistry
    from kinetic.ingestion.warehouse.bigquery import SafeBigQueryClient

    assert AlpacaPriceProvider is not None
    assert GdeltDocClient is not None
    assert PriceDataProvider is not None
    assert ProviderRegistry is not None
    assert SafeBigQueryClient is not None


def test_import_processing() -> None:
    from kinetic.processing.cross_asset.join import build_observations
    from kinetic.processing.news.dedupe import cluster_exact_duplicates
    from kinetic.processing.news.entity_linking import match_entities

    assert callable(build_observations)
    assert callable(cluster_exact_duplicates)
    assert callable(match_entities)


def test_import_ml() -> None:
    from kinetic.ml.relevance import SemiconductorRelevanceAnnotationV1, binary_relevant

    assert SemiconductorRelevanceAnnotationV1 is not None
    assert callable(binary_relevant)


def test_import_research() -> None:
    from kinetic.research.datasets import build_dataset
    from kinetic.research.event_studies import run_event_study

    assert callable(build_dataset)
    assert callable(run_event_study)


def test_import_interface() -> None:
    from kinetic.interface.cli import app, main

    assert app is not None
    assert callable(main)


def test_bootstrap_registers_every_expected_task() -> None:
    from kinetic.bootstrap import build_default_registry

    registry = build_default_registry()
    assert registry.task_ids() == [
        "market.alpaca.fetch_bars",
        "ml.relevance.build_benchmark",
        "ml.relevance.run_real_corpus_pilot",
        "news.aggregate_features",
        "news.dedupe_articles",
        "news.filter_articles",
        "news.gdelt.bigquery.discover_seeded_themes",
        "news.gdelt.bigquery.discover_themes",
        "news.gdelt.bigquery.fetch_daily_counts",
        "news.gdelt.bigquery.score_seeded_themes",
        "news.gdelt.fetch_articles",
        "news.store_articles",
        "news.store_features",
        "news.tag_articles",
        "research.build_news_market_dataset",
    ]


def test_legacy_task_aliases_all_point_at_real_tasks() -> None:
    from kinetic.bootstrap import LEGACY_TASK_ALIASES, build_default_registry

    registry = build_default_registry()
    current = set(registry.task_ids())
    for old, new in LEGACY_TASK_ALIASES.items():
        assert new in current, f"alias {old!r} points at unknown task {new!r}"
    assert registry.deprecated_aliases() == dict(LEGACY_TASK_ALIASES)


def test_aliases_can_be_switched_off() -> None:
    from kinetic.bootstrap import build_default_registry

    registry = build_default_registry(include_legacy_aliases=False)
    assert registry.deprecated_aliases() == {}
    assert "gdelt_docs" not in registry

"""
Import smoke tests: verify installable packages import cleanly.

Implemented packages assert key public symbols exist.
"""


def test_import_common():
    import common

    assert common.load_config is not None
    assert common.PipelineError is not None


def test_import_pipeline_core():
    from pipeline_core.engine.runner import run_plan_from_file
    from pipeline_core.tasks.registry import TASK_REGISTRY

    assert callable(run_plan_from_file)
    assert isinstance(TASK_REGISTRY, dict)


def test_import_news_data():
    import news_data

    assert news_data.GdeltClient is not None


def test_import_market_data():
    import market_data.providers as providers
    from market_data.domain import PriceBar
    from market_data.providers import PriceDataProvider, ProviderRegistry
    from market_data.providers.alpaca import AlpacaPriceProvider
    from market_data.storage import JsonlFinancialDataStore

    assert PriceBar is not None
    assert AlpacaPriceProvider is not None
    assert JsonlFinancialDataStore is not None
    assert PriceDataProvider is not None
    assert ProviderRegistry is not None
    assert providers.__all__ == ["PriceDataProvider", "ProviderRegistry"]
    assert not hasattr(providers, "CompanyDataProvider")
    assert not hasattr(providers, "MacroDataProvider")


def test_import_research_data():
    from research_data import build_dataset, run_event_study

    assert callable(build_dataset)
    assert callable(run_event_study)


def test_import_trading_platform():
    import trading_platform
    from pipeline_core.tasks.registry import TASK_REGISTRY

    assert trading_platform.__version__
    assert "alpaca_historical_bars" in TASK_REGISTRY
    assert "bigquery_gdelt_seeded_theme_scoring" in TASK_REGISTRY

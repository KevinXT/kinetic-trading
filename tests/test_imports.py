"""
Import smoke tests: verify all installable packages import cleanly.

Implemented packages assert key public symbols exist. The strategy_sdk
boundary only verifies that it imports and has a module docstring.
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
    from market_data.domain import PriceBar
    from market_data.providers.alpaca import AlpacaPriceProvider
    from market_data.storage import JsonlFinancialDataStore

    assert PriceBar is not None
    assert AlpacaPriceProvider is not None
    assert JsonlFinancialDataStore is not None


def test_import_strategy_sdk():
    """Reserved package boundary — verify it imports and has a docstring."""
    import strategy_sdk

    assert strategy_sdk.__doc__


def test_import_trading_platform():
    import trading_platform
    from pipeline_core.tasks.registry import TASK_REGISTRY

    assert trading_platform.__version__
    assert "alpaca_historical_bars" in TASK_REGISTRY

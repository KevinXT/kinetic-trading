"""
Import smoke tests: verify all installable packages import cleanly.

Implemented packages assert key public symbols exist. Placeholder packages
only verify that they import and have a module docstring — they are not
expected to export real functionality yet.
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
    """Placeholder package — just verify it imports and has a docstring."""
    import market_data

    assert market_data.__doc__


def test_import_strategy_sdk():
    """Placeholder package — just verify it imports and has a docstring."""
    import strategy_sdk

    assert strategy_sdk.__doc__


def test_import_trading_platform():
    """Stub app — just verify it imports and exposes a version."""
    import trading_platform

    assert trading_platform.__version__

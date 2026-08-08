"""Kinetic Trading — a point-in-time market-intelligence and trading-research platform.

Importing this package has **no side effects**. In particular it does not build,
populate, or mutate a task registry. Assembling the application is the explicit
job of :mod:`kinetic.bootstrap`::

    from kinetic.bootstrap import build_default_registry
    from kinetic.core.config import load_runtime_config
    from kinetic.core.pipeline.runner import run_pipeline

    registry = build_default_registry()
    config = load_runtime_config("configs/pipelines/demo.yaml")
    run_pipeline(config, registry=registry, runs_root="warehouse/runs")

See ``docs/architecture/platform-overview.md`` for the subsystem map.
"""

__version__ = "0.2.0"

__all__ = ["__version__"]

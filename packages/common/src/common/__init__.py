"""
Shared foundations for kinetic-trading: config loading and error types.

This is the lowest-level package in the monorepo. It has no dependencies on
other internal packages and should contain only code that is genuinely shared,
low-level, domain-neutral, and stable.

Current contents:
  - config_builder: YAML loading with recursive includes and deep merge
  - errors: project-wide exception hierarchy
"""

from .config_builder import (
    build_runtime_config,
    deep_merge,
    load_config,
    load_yaml,
)
from .errors import (
    ConfigError,
    DataSourceError,
    DuplicateTaskError,
    ExecutionError,
    PipelineError,
    TradingSystemError,
)

__all__ = [
    "load_yaml",
    "load_config",
    "build_runtime_config",
    "deep_merge",
    "TradingSystemError",
    "ConfigError",
    "PipelineError",
    "DataSourceError",
    "ExecutionError",
    "DuplicateTaskError",
]

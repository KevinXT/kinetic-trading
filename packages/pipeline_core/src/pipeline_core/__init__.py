"""
Pipeline engine for kinetic-trading.

Owns the core execution model: YAML plan parsing, linear step runner, lifecycle
hooks, run context / artifact management, and the task registry. This is the
most substantial implemented package in the monorepo.

Depends on ``common`` for config loading and error types. Does not contain
trading-domain logic — that belongs in ``strategy_sdk`` or app-level code.
Tasks are registered by consumer code and looked up by name at runtime.
"""

from pipeline_core.engine.context import RunContext
from pipeline_core.engine.hooks import (
    ErrorHook,
    Hook,
    LoggingHook,
    MetadataHook,
    TimingHook,
    default_pipeline_hooks,
)
from pipeline_core.engine.parser import Step, parse_plan
from pipeline_core.engine.runner import run_plan, run_plan_from_file
from pipeline_core.tasks.base import TaskFn
from pipeline_core.tasks.registry import TASK_REGISTRY, get_task, register

__all__ = [
    "ErrorHook",
    "Hook",
    "LoggingHook",
    "MetadataHook",
    "RunContext",
    "Step",
    "TaskFn",
    "TASK_REGISTRY",
    "TimingHook",
    "default_pipeline_hooks",
    "get_task",
    "parse_plan",
    "register",
    "run_plan",
    "run_plan_from_file",
]

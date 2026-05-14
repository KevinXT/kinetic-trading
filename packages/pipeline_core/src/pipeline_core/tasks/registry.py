"""
Maps pipeline step task names to callables.

Tasks register here; the runner looks up step.task in this registry.
Consumer code (e.g. apps/trading_platform) is responsible for populating
the registry at startup — the engine itself stays provider-agnostic.
"""

from __future__ import annotations

from typing import Dict

from common.errors import DuplicateTaskError
from pipeline_core.tasks.base import TaskFn

TASK_REGISTRY: Dict[str, TaskFn] = {}


def register(name: str, *, allow_override: bool = False):
    """
    Decorator: register a task under a string name.

    By default duplicate names raise :class:`DuplicateTaskError` so accidental
    overwrites are visible. Set ``allow_override=True`` for intentional replacement
    (e.g. tests or hot-patching).
    """

    def decorator(fn: TaskFn) -> TaskFn:
        if name in TASK_REGISTRY and not allow_override:
            raise DuplicateTaskError(
                f"Task {name!r} is already registered (duplicate registration). "
                "Use allow_override=True if replacing is intentional."
            )
        TASK_REGISTRY[name] = fn
        return fn

    return decorator


def get_task(name: str) -> TaskFn | None:
    return TASK_REGISTRY.get(name)

"""
Task contract for the linear pipeline runner.

Each registered task is a callable: run(ctx, params).
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from kinetic.core.pipeline.context import RunContext

JsonDict = Dict[str, Any]

TaskFn = Callable[[RunContext, JsonDict], None]

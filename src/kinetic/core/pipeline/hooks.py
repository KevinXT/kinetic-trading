"""
Lifecycle hooks for pipeline execution.

Cross-cutting concerns (timing, error capture, run metadata) live in Hook
subclasses. ``runner.run_pipeline`` orchestrates the loop and invokes hooks.

State keys under ``ctx.state`` use the ``_pipeline_`` prefix to avoid clashes
with task code.
"""

from __future__ import annotations

import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List

from kinetic.core.artifacts import write_run_metadata
from kinetic.core.pipeline.context import RunContext
from kinetic.core.pipeline.plan import Step
from kinetic.core.provenance import git_head_sha_near, utc_now_iso

JsonDict = Dict[str, Any]

logger = logging.getLogger(__name__)

# --- ctx.state keys (internal contract between hooks and runner) ---
STEPS_EXECUTED = "_pipeline_steps_executed"
TIMING_START = "_pipeline_timing_start"
RUN_STARTED_AT = "_pipeline_run_started_at"
GIT_COMMIT = "_pipeline_git_commit"
FINAL_STATUS = "_pipeline_final_status"
FAILED_STEP = "_pipeline_failed_step"
ERROR_INFO = "_pipeline_error"
CURRENT_STEP_INDEX = "_pipeline_current_step_index"


class Hook:
    """Optional lifecycle callbacks for a single pipeline run."""

    def before_run(self, ctx: RunContext) -> None:
        pass

    def after_run(self, ctx: RunContext) -> None:
        pass

    def before_step(self, ctx: RunContext, step: Step) -> None:
        pass

    def after_step(self, ctx: RunContext, step: Step) -> None:
        pass

    def on_error(self, ctx: RunContext, step: Step, error: BaseException) -> None:
        pass


class TimingHook(Hook):
    """Records per-step wall timing and ``duration_seconds`` (monotonic)."""

    def before_run(self, ctx: RunContext) -> None:
        ctx.state[STEPS_EXECUTED] = []

    def before_step(self, ctx: RunContext, step: Step) -> None:
        ctx.state[TIMING_START] = (time.monotonic(), utc_now_iso())

    def after_step(self, ctx: RunContext, step: Step) -> None:
        start = ctx.state.pop(TIMING_START, None)
        if start is None:
            return
        t0, started_at = start
        t1 = time.monotonic()
        completed_at = utc_now_iso()
        duration_seconds = round(t1 - t0, 6)
        rec: JsonDict = {
            "task": step.task,
            "param_keys": sorted(step.params.keys()),
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_seconds": duration_seconds,
        }
        ctx.state[STEPS_EXECUTED].append(rec)

    def on_error(self, ctx: RunContext, step: Step, error: BaseException) -> None:
        ctx.state.pop(TIMING_START, None)


class ErrorHook(Hook):
    """Captures failure fields for metadata and writes ``traceback.txt`` when applicable."""

    def on_error(self, ctx: RunContext, step: Step, error: BaseException) -> None:
        idx = ctx.state.get(CURRENT_STEP_INDEX, 1)
        ctx.state[FAILED_STEP] = {"index": idx, "task": step.task}
        ctx.state[ERROR_INFO] = {
            "type": type(error).__name__,
            "message": str(error),
        }
        ctx.state[FINAL_STATUS] = "failed"
        if isinstance(error, Exception) and sys.exc_info()[0] is not None:
            (ctx.run_dir / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")


class MetadataHook(Hook):
    """
    Run-level timestamps, git SHA, and final ``run_metadata.json`` write.

    Must run ``before_run`` early and ``after_run`` last among hooks (list order).
    """

    def __init__(self, *, git_anchor: Path) -> None:
        self._git_anchor = git_anchor

    def before_run(self, ctx: RunContext) -> None:
        ctx.state[RUN_STARTED_AT] = utc_now_iso()
        ctx.state[GIT_COMMIT] = git_head_sha_near(self._git_anchor)
        ctx.state[FINAL_STATUS] = "completed"
        ctx.state[FAILED_STEP] = None
        ctx.state[ERROR_INFO] = None

    def after_run(self, ctx: RunContext) -> None:
        completed_at = utc_now_iso()
        meta: JsonDict = {
            "run_name": ctx.run_name,
            "run_id": ctx.run_id,
            "started_at": ctx.state[RUN_STARTED_AT],
            "completed_at": completed_at,
            "git_commit": ctx.state.get(GIT_COMMIT),
            "steps_executed": list(ctx.state.get(STEPS_EXECUTED, [])),
            "status": ctx.state.get(FINAL_STATUS, "completed"),
            "failed_step": ctx.state.get(FAILED_STEP),
            "error": ctx.state.get(ERROR_INFO),
        }
        write_run_metadata(ctx.run_dir, meta)


class LoggingHook(Hook):
    """Optional debug logging; disabled by default (no handler on the logger)."""

    def before_step(self, ctx: RunContext, step: Step) -> None:
        logger.debug("step task=%r run_id=%s", step.task, ctx.run_id)

    def after_step(self, ctx: RunContext, step: Step) -> None:
        logger.debug("finished task=%r run_id=%s", step.task, ctx.run_id)

    def on_error(self, ctx: RunContext, step: Step, error: BaseException) -> None:
        logger.error("step failed task=%r: %s", step.task, error)


def default_pipeline_hooks(*, git_anchor: Path) -> List[Hook]:
    """
    Default hook chain: timing → error capture → run metadata write.

    ``MetadataHook`` is last so ``before_run`` sets run-level fields after the
    steps list is initialized, and ``after_run`` writes ``run_metadata.json`` after
    any other hook's ``after_run``.
    """
    return [
        TimingHook(),
        ErrorHook(),
        MetadataHook(git_anchor=git_anchor),
    ]

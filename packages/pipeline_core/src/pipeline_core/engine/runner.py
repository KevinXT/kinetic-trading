"""
runner.py

Linear pipeline runner: resolved config → parse_plan → execute steps in order.

Writes run_dir/config_resolved.yaml, run_dir/run_metadata.json, and delegates
per-step artifacts to tasks via RunContext.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from common.config_builder import load_config
from common.errors import PipelineError
from pipeline_core.engine.context import RunContext
from pipeline_core.engine.hooks import (
    CURRENT_STEP_INDEX,
    Hook,
    default_pipeline_hooks,
)
from pipeline_core.engine.parser import parse_plan
from pipeline_core.engine.run_paths import allocate_group_slug, slug_run_name
from pipeline_core.engine.run_metadata import write_resolved_config
from pipeline_core.tasks.base import TaskFn
from pipeline_core.tasks.registry import TASK_REGISTRY

JsonDict = Dict[str, Any]


def run_plan(
    cfg: JsonDict,
    *,
    run_dir: str | Path,
    run_name: str,
    run_id: str,
    registry: Optional[Mapping[str, TaskFn]] = None,
    hooks: Optional[Sequence[Hook]] = None,
) -> RunContext:
    """
    Execute a pipeline from an already-loaded config dict.

    Args:
        cfg: Resolved plan config (e.g. from load_config).
        run_dir: Root directory for this run (created if missing).
        run_name: Human-readable run name (often from cfg["name"]).
        run_id: Unique id for this run (folder suffix).
        registry: Task name → callable(ctx, params). Defaults to TASK_REGISTRY.
        hooks: Lifecycle hooks (timing, errors, metadata). Defaults to
            ``default_pipeline_hooks(git_anchor=...)``.

    Returns:
        RunContext after all steps complete.

    Raises:
        PipelineError: No steps, or an unregistered task name.

    On failure after steps begin, writes ``run_metadata.json`` with ``status: failed`` and
    re-raises the exception. May write ``traceback.txt`` when a task raises.
    """
    reg: Mapping[str, TaskFn] = registry if registry is not None else TASK_REGISTRY
    steps = parse_plan(cfg)
    if not steps:
        raise PipelineError("Plan has no steps (missing or empty pipeline).")

    rd = Path(run_dir)
    ctx = RunContext(cfg=cfg, run_name=run_name, run_id=run_id, run_dir=rd)
    write_resolved_config(rd, cfg)

    hook_list: list[Hook] = list(
        hooks
        if hooks is not None
        else default_pipeline_hooks(git_anchor=Path(__file__).resolve())
    )

    exc_to_reraise: BaseException | None = None

    for hook in hook_list:
        hook.before_run(ctx)

    try:
        for i, step in enumerate(steps):
            handler = reg.get(step.task)
            if handler is None:
                msg = (
                    f"No task registered for {step.task!r} (step {i + 1} of {len(steps)})."
                )
                ctx.state[CURRENT_STEP_INDEX] = i + 1
                err = PipelineError(msg)
                for hook in hook_list:
                    hook.on_error(ctx, step, err)
                exc_to_reraise = err
                break

            ctx.state[CURRENT_STEP_INDEX] = i + 1
            for hook in hook_list:
                hook.before_step(ctx, step)
            try:
                handler(ctx, step.params)
            except Exception as e:
                # Catch Exception only: BaseException (KeyboardInterrupt, SystemExit)
                # propagates without mapping to a failed step — finally still runs.
                for hook in hook_list:
                    hook.on_error(ctx, step, e)
                exc_to_reraise = e
                break
            for hook in hook_list:
                hook.after_step(ctx, step)
    finally:
        for hook in hook_list:
            hook.after_run(ctx)

    if exc_to_reraise is not None:
        raise exc_to_reraise
    return ctx


def run_plan_from_file(
    config_path: str | Path,
    *,
    runs_root: str | Path,
    registry: Optional[Mapping[str, TaskFn]] = None,
    run_id: Optional[str] = None,
    hooks: Optional[Sequence[Hook]] = None,
) -> RunContext:
    """
    Load a YAML plan with load_config, then run under runs_root / <slug(name)> / <run_id>.

    run_id defaults to a short uuid hex string.
    The group folder is ``slug(name)`` unless that path already exists, then
    ``slug_2``, ``slug_3``, ... (same slug from different names or reruns).
    """
    path = Path(config_path).resolve()
    cfg = load_config(path)
    name_raw = cfg.get("name", "unnamed")
    run_name = str(name_raw).strip() if name_raw is not None else "unnamed"
    if not run_name:
        run_name = "unnamed"
    rid = run_id or uuid.uuid4().hex[:12]
    rr = Path(runs_root)
    slug = allocate_group_slug(rr, slug_run_name(run_name))
    run_dir = rr / slug / rid
    return run_plan(
        cfg,
        run_dir=run_dir,
        run_name=run_name,
        run_id=rid,
        registry=registry,
        hooks=hooks,
    )

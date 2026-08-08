"""The execution loop: resolved config → plan → steps, in order.

The runner is deliberately linear and deliberately dumb. It resolves each step's
task against a registry it was *given*, hands the task a :class:`RunContext` and
its parameters, and lets lifecycle hooks record what happened. It does not
schedule, retry, parallelize, or infer dependencies — a research pipeline that
cannot be read top to bottom is a research pipeline nobody trusts.

There is no default registry. A caller that does not pass one gets an error, not
a silently empty run. Build one with :func:`kinetic.bootstrap.build_default_registry`.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from kinetic.core.artifacts import allocate_run_dir, write_resolved_config
from kinetic.core.config import load_runtime_config
from kinetic.core.pipeline.context import RunContext
from kinetic.core.pipeline.hooks import CURRENT_STEP_INDEX, Hook, default_pipeline_hooks
from kinetic.core.pipeline.plan import Plan, parse_plan
from kinetic.core.pipeline.registry import TaskRegistry

JsonDict = Dict[str, Any]

DEFAULT_RUNS_ROOT = "warehouse/runs"


def run_pipeline(
    cfg: JsonDict,
    *,
    registry: TaskRegistry,
    runs_root: str | Path = DEFAULT_RUNS_ROOT,
    run_id: Optional[str] = None,
    run_dir: str | Path | None = None,
    hooks: Optional[Sequence[Hook]] = None,
    source: str | Path | None = None,
) -> RunContext:
    """Execute a pipeline from an already-resolved config mapping.

    Args:
        cfg: Fully resolved config (see :func:`kinetic.core.config.load_runtime_config`).
        registry: Task id → callable. Required; see module docstring.
        runs_root: Root under which the run directory is allocated. Ignored when
            ``run_dir`` is given.
        run_id: Fixed run id. Defaults to a short uuid. Pass a fixed value for
            byte-stable reruns of deterministic pipelines.
        run_dir: Exact run directory, bypassing allocation under ``runs_root``.
        hooks: Lifecycle hooks. Defaults to timing → error capture → metadata.
        source: Config path, used only to make validation errors locatable.

    Returns:
        The :class:`RunContext` after the last step.

    Raises:
        ConfigError: The config does not describe a valid plan.
        PipelineError: A step names a task the registry does not have.
        Exception: Whatever a task raised, after ``run_metadata.json`` has been
            written with ``status: failed`` and ``traceback.txt`` captured.
    """
    plan = parse_plan(cfg, source=source)
    rid = run_id or uuid.uuid4().hex[:12]
    directory = (
        Path(run_dir) if run_dir is not None else allocate_run_dir(runs_root, plan.name, rid)
    )
    return _execute(plan, cfg, registry=registry, run_dir=directory, run_id=rid, hooks=hooks)


def run_pipeline_from_file(
    config_path: str | Path,
    *,
    registry: TaskRegistry,
    runs_root: str | Path = DEFAULT_RUNS_ROOT,
    run_id: Optional[str] = None,
    hooks: Optional[Sequence[Hook]] = None,
) -> RunContext:
    """Load a YAML config (including ``configs/local.yaml`` overrides) and run it."""
    path = Path(config_path).resolve()
    cfg = load_runtime_config(path)
    return run_pipeline(
        cfg,
        registry=registry,
        runs_root=runs_root,
        run_id=run_id,
        hooks=hooks,
        source=config_path,
    )


def _execute(
    plan: Plan,
    cfg: JsonDict,
    *,
    registry: TaskRegistry,
    run_dir: Path,
    run_id: str,
    hooks: Optional[Sequence[Hook]],
) -> RunContext:
    ctx = RunContext(cfg=cfg, run_name=plan.name, run_id=run_id, run_dir=run_dir)
    write_resolved_config(run_dir, cfg)

    hook_list: list[Hook] = list(
        hooks if hooks is not None else default_pipeline_hooks(git_anchor=Path(__file__).resolve())
    )

    exc_to_reraise: BaseException | None = None

    for hook in hook_list:
        hook.before_run(ctx)

    try:
        for i, step in enumerate(plan.steps):
            ctx.state[CURRENT_STEP_INDEX] = i + 1
            try:
                handler = registry.resolve(step.task)
            except Exception as e:  # unknown task: report as a step failure
                for hook in hook_list:
                    hook.on_error(ctx, step, e)
                exc_to_reraise = e
                break

            for hook in hook_list:
                hook.before_step(ctx, step)
            try:
                handler(ctx, step.params)
            except Exception as e:
                # Exception only: KeyboardInterrupt / SystemExit propagate without
                # being recorded as a failed step. The finally block still runs.
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

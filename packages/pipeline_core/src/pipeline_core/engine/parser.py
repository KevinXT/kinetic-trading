from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from common.errors import PipelineError

JsonDict = Dict[str, Any]


def _non_empty_task_name(value: Any, *, field: str, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PipelineError(
            f"Invalid pipeline config: {location} requires a non-empty string {field}, "
            f"got {value!r}."
        )
    return value.strip()


@dataclass
class Step:
    """A single pipeline step: task name and its parameters."""

    task: str
    params: JsonDict

    def __post_init__(self) -> None:
        if not self.task or not isinstance(self.task, str):
            raise PipelineError("step task must be a non-empty string")
        if not isinstance(self.params, dict):
            raise PipelineError("step params must be a dict")


def parse_plan(cfg: JsonDict) -> List[Step]:
    """
    Convert a pipeline config into an ordered list of steps.

    Expects cfg to have a ``"pipeline"`` key with optional sections:

    - **ingest**: task name from ``"source"``; remaining keys become params
    - **transform**: one step (dict) or many (list of dicts)
    - **strategy**: task name from ``"type"``; remaining keys become params

    Order: ingest -> transform(s) -> strategy.

    **Preferred config style** (explicit ``type`` / ``source`` keys)::

        pipeline:
          ingest:
            source: gdelt_docs
            limit: 50
          transform:
            - type: dedupe
            - type: normalize
              threshold: 0.8
          strategy:
            type: gdelt_return

    Convenience forms (single-key shorthand, ``dedupe: true``) are supported
    for backward compatibility but the explicit style above is recommended
    for clarity in new configs.

    Raises:
        PipelineError: Invalid pipeline shape or missing required fields.
    """
    pipeline = cfg.get("pipeline")
    if pipeline is None:
        return []
    if not isinstance(pipeline, dict):
        raise PipelineError(
            f"Invalid pipeline config: 'pipeline' must be a mapping, got {type(pipeline).__name__}."
        )
    if not pipeline:
        return []

    steps: List[Step] = []

    if "ingest" in pipeline:
        ingest_block = pipeline["ingest"]
        if ingest_block is None:
            raise PipelineError(
                "Invalid pipeline config: 'pipeline.ingest' cannot be null when the key is present."
            )
        if not isinstance(ingest_block, dict):
            raise PipelineError(
                "Invalid pipeline config: 'pipeline.ingest' must be a mapping when present."
            )
        source = ingest_block.get("source")
        task = _non_empty_task_name(source, field="source", location="pipeline.ingest")
        params = dict(ingest_block)
        params.pop("source", None)
        steps.append(Step(task=task, params=params))

    if "transform" in pipeline:
        transform_block = pipeline["transform"]
        if transform_block is None:
            raise PipelineError(
                "Invalid pipeline config: 'pipeline.transform' cannot be null when the key is present."
            )
        steps.extend(_parse_transform_steps(transform_block))

    if "strategy" in pipeline:
        strategy_block = pipeline["strategy"]
        if strategy_block is None:
            raise PipelineError(
                "Invalid pipeline config: 'pipeline.strategy' cannot be null when the key is present."
            )
        if not isinstance(strategy_block, dict):
            raise PipelineError(
                "Invalid pipeline config: 'pipeline.strategy' must be a mapping when present."
            )
        strategy_type = strategy_block.get("type")
        task = _non_empty_task_name(
            strategy_type, field="type", location="pipeline.strategy"
        )
        params = dict(strategy_block)
        params.pop("type", None)
        steps.append(Step(task=task, params=params))

    return steps


def _parse_transform_steps(transform_block: Any) -> List[Step]:
    """
    Parse transform into zero or more steps.

    - dict: one legacy transform block
    - list: one step per item (no silent skipping of invalid entries)
    """
    if isinstance(transform_block, dict):
        task = _transform_task_name(transform_block)
        params = dict(transform_block)
        params.pop("type", None)
        return [Step(task=task, params=params)]
    if isinstance(transform_block, list):
        out: List[Step] = []
        for i, item in enumerate(transform_block):
            if item is None:
                raise PipelineError(
                    f"Invalid pipeline config: pipeline.transform[{i}] is null."
                )
            if not isinstance(item, dict):
                raise PipelineError(
                    f"Invalid pipeline config: pipeline.transform[{i}] must be a mapping, "
                    f"got {type(item).__name__}."
                )
            if not item:
                raise PipelineError(
                    f"Invalid pipeline config: pipeline.transform[{i}] is an empty mapping."
                )
            task, params = _normalize_transform_item(item, index=i)
            if task == "transform" and not params:
                raise PipelineError(
                    "Invalid pipeline config: transform step resolved to task 'transform' "
                    f"with no parameters (transform item index {i}). Use an explicit task name "
                    '(e.g. { "type": "dedupe", ... }).'
                )
            out.append(Step(task=task, params=params))
        return out
    raise PipelineError(
        "Invalid pipeline config: 'pipeline.transform' must be a mapping or a list, "
        f"got {type(transform_block).__name__}."
    )


def _normalize_transform_item(item: JsonDict, *, index: int) -> tuple[str, JsonDict]:
    """Turn one transform list item into (task_name, params).

    Preferred: ``{ "type": "task_name", ...params }``
    Convenience (kept for compatibility): ``{ "task_name": params }`` (single-key)
    """
    # Preferred: { type: "task", ...params }
    t = item.get("type")
    if isinstance(t, str) and t.strip():
        params = dict(item)
        params.pop("type", None)
        return (t.strip(), params)

    # Convenience: { task_name: params } (single-key dict)
    if len(item) == 1:
        key, value = next(iter(item.items()))
        if isinstance(key, str) and key.strip():
            if value is True or value is None:
                return (key, {})
            if isinstance(value, dict):
                return (key, dict(value))
            return (key, {"value": value})

    # Fallback: legacy single-block shape
    task = _transform_task_name(item)
    params = dict(item)
    params.pop("type", None)
    return (task, params)


def _transform_task_name(block: JsonDict) -> str:
    """
    Infer task name from a legacy single-dict transform block.

    Resolution order: explicit ``"type"`` > ``dedupe: true`` > first key with
    value ``True``. Prefer explicit ``"type"`` in new configs.
    """
    t = block.get("type")
    if isinstance(t, str) and t.strip():
        return t.strip()
    if block.get("dedupe") is True:
        return "dedupe"
    for k, v in block.items():
        if v is True and isinstance(k, str):
            return k
    if not block:
        raise PipelineError(
            "Invalid pipeline config: empty transform mapping; specify a task "
            '(e.g. { "type": "dedupe", ... } or { "dedupe": true }).'
        )
    raise PipelineError(
        "Invalid pipeline config: cannot infer transform task name from this mapping; "
        'set "type" or use a one-key item like { "dedupe": true }.'
    )

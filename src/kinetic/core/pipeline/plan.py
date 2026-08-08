"""Turn a resolved config mapping into a validated, ordered execution plan.

The authoritative shape
-----------------------

::

    name: news_market_dataset_demo

    pipeline:
      steps:
        - task: research.build_news_market_dataset
          params:
            articles_path: tests/fixtures/research/articles.json
            forward_horizon: 5

One shape, one ordering rule (top to bottom), one place to put parameters. Every
checked-in config in this repository uses it.

The legacy shape
----------------

Configs written before the ``kinetic`` consolidation used three named sections —
``ingest`` (task name in ``source``), ``transform`` (task name in ``type``, one
mapping or a list) and ``strategy`` (task name in ``type``) — executed in that
fixed order, plus single-key shorthands such as ``{dedupe: true}``.

That shape is still accepted so external configs do not break on upgrade. It
emits a :class:`DeprecationWarning`, it is confined to
:mod:`kinetic.core.pipeline.legacy_plan`, and it is scheduled for removal in
0.4.0. ``kinetic config migrate`` rewrites a legacy config into the current
shape.

Validation errors
-----------------

Every error names the config file, the step, the field, and what was expected.
Pydantic was considered and not adopted: the plan schema is three fields deep,
the rest of the platform is deliberately dependency-light, and a hand-written
message can say "step 2 (news.gdelt.fetch_articles): 'params' must be a mapping,
got list" more precisely than a generic validator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from kinetic.core.errors import ConfigError

JsonDict = Dict[str, Any]


def _location(source: str | Path | None) -> str:
    return f"{source}: " if source else ""


@dataclass(frozen=True)
class Step:
    """One pipeline step: a task identifier and its parameters."""

    task: str
    params: JsonDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.task, str) or not self.task.strip():
            raise ConfigError("step 'task' must be a non-empty string")
        if not isinstance(self.params, dict):
            raise ConfigError("step 'params' must be a mapping")


@dataclass(frozen=True)
class Plan:
    """An ordered, validated sequence of steps plus the run name."""

    name: str
    steps: tuple[Step, ...]
    source: str | None = None

    def __len__(self) -> int:
        return len(self.steps)

    def __iter__(self) -> Any:
        return iter(self.steps)


def parse_plan(cfg: Mapping[str, Any], *, source: str | Path | None = None) -> Plan:
    """Validate ``cfg`` and return the plan it describes.

    Raises:
        ConfigError: with the file, step index, task, field and expectation.
    """
    where = _location(source)

    if not isinstance(cfg, Mapping):
        raise ConfigError(f"{where}config root must be a mapping, got {type(cfg).__name__}.")

    name = _parse_name(cfg, where=where)

    pipeline = cfg.get("pipeline")
    if pipeline is None:
        raise ConfigError(
            f"{where}config has no 'pipeline' section. Expected a mapping with a " "'steps' list."
        )
    if not isinstance(pipeline, Mapping):
        raise ConfigError(f"{where}'pipeline' must be a mapping, got {type(pipeline).__name__}.")

    if "steps" in pipeline:
        steps = _parse_steps(pipeline["steps"], where=where)
    else:
        # Legacy ingest/transform/strategy shape. Imported here so the current
        # shape never depends on the compatibility path.
        from kinetic.core.pipeline.legacy_plan import parse_legacy_pipeline

        steps = parse_legacy_pipeline(pipeline, where=where, source=source)

    if not steps:
        raise ConfigError(f"{where}'pipeline.steps' is empty. A plan needs at least one step.")

    return Plan(name=name, steps=tuple(steps), source=str(source) if source else None)


def _parse_name(cfg: Mapping[str, Any], *, where: str) -> str:
    raw = cfg.get("name", "unnamed")
    if raw is None:
        return "unnamed"
    if not isinstance(raw, str):
        raise ConfigError(f"{where}'name' must be a string, got {type(raw).__name__}.")
    return raw.strip() or "unnamed"


def _parse_steps(raw_steps: Any, *, where: str) -> List[Step]:
    if not isinstance(raw_steps, Sequence) or isinstance(raw_steps, (str, bytes)):
        raise ConfigError(
            f"{where}'pipeline.steps' must be a list, got {type(raw_steps).__name__}."
        )

    steps: List[Step] = []
    for index, item in enumerate(raw_steps):
        position = f"pipeline.steps[{index}]"
        if not isinstance(item, Mapping):
            raise ConfigError(
                f"{where}{position} must be a mapping with a 'task' key, got "
                f"{type(item).__name__}."
            )

        task = item.get("task")
        if not isinstance(task, str) or not task.strip():
            raise ConfigError(
                f"{where}{position} requires a non-empty string 'task', got {task!r}."
            )
        task = task.strip()

        params = item.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, Mapping):
            raise ConfigError(
                f"{where}{position} (task {task!r}): 'params' must be a mapping, got "
                f"{type(params).__name__}."
            )

        unknown = sorted(set(item) - {"task", "params"})
        if unknown:
            raise ConfigError(
                f"{where}{position} (task {task!r}): unknown key(s) "
                f"{', '.join(repr(k) for k in unknown)}. A step accepts only 'task' "
                "and 'params' — put step arguments inside 'params'."
            )

        steps.append(Step(task=task, params=dict(params)))

    return steps

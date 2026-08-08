"""Compatibility: the pre-0.2 ``ingest`` / ``transform`` / ``strategy`` config shape.

This module is the **only** place that understands the old shape. It exists so
that configs written outside this repository keep working through one release
cycle; every config checked in here uses the current ``pipeline.steps`` shape.

Scheduled for removal in **0.4.0**. Migrate with::

    kinetic config migrate old.yaml --output new.yaml

The old shape had three named sections executed in a fixed order:

- ``ingest`` — a mapping; task name in ``source``, remaining keys are parameters
- ``transform`` — one mapping, or a list of mappings; task name in ``type``
- ``strategy`` — a mapping; task name in ``type``, remaining keys are parameters

plus two shorthands inside ``transform`` list items: a single-key mapping
``{dedupe_articles: true}`` naming a task with no parameters, and a bare
``{dedupe: true}``-style flag inside a single transform mapping.

Old task names (``gdelt_docs``, ``build_news_market_dataset``, …) are a separate
concern and are handled by the alias table in :mod:`kinetic.bootstrap`.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from kinetic.core.errors import ConfigError

JsonDict = Dict[str, Any]

REMOVAL_VERSION = "0.4.0"

_DEPRECATION_MESSAGE = (
    "The 'ingest'/'transform'/'strategy' pipeline config shape is deprecated and "
    f"will be removed in kinetic {REMOVAL_VERSION}. Use 'pipeline.steps' with "
    "explicit 'task' and 'params' keys. Run 'kinetic config migrate <config>' to "
    "convert this file."
)


def parse_legacy_pipeline(
    pipeline: Mapping[str, Any],
    *,
    where: str = "",
    source: str | Path | None = None,
) -> List[Any]:
    """Parse the legacy pipeline block into current :class:`Step` objects."""
    from kinetic.core.pipeline.plan import Step

    warnings.warn(
        f"{source or 'pipeline config'}: {_DEPRECATION_MESSAGE}",
        DeprecationWarning,
        stacklevel=3,
    )

    if not pipeline:
        raise ConfigError(f"{where}'pipeline' is empty. Expected a 'steps' list.")

    unknown = sorted(set(pipeline) - {"ingest", "transform", "strategy"})
    if unknown:
        raise ConfigError(
            f"{where}'pipeline' has no 'steps' key, and the legacy sections present "
            f"are unrecognized: {', '.join(repr(k) for k in unknown)}. Expected "
            "'steps', or the legacy 'ingest'/'transform'/'strategy'."
        )

    steps: List[Any] = []

    if "ingest" in pipeline:
        block = _require_mapping(pipeline["ingest"], name="pipeline.ingest", where=where)
        task = _require_task_name(
            block.get("source"), field="source", location="pipeline.ingest", where=where
        )
        params = dict(block)
        params.pop("source", None)
        steps.append(Step(task=task, params=params))

    if "transform" in pipeline:
        steps.extend(_parse_transform(pipeline["transform"], where=where))

    if "strategy" in pipeline:
        block = _require_mapping(pipeline["strategy"], name="pipeline.strategy", where=where)
        task = _require_task_name(
            block.get("type"), field="type", location="pipeline.strategy", where=where
        )
        params = dict(block)
        params.pop("type", None)
        steps.append(Step(task=task, params=params))

    return steps


def to_current_shape(cfg: Mapping[str, Any], *, source: str | Path | None = None) -> JsonDict:
    """Rewrite a legacy config mapping into the current ``pipeline.steps`` shape.

    Non-pipeline sections (``providers``, ``storage``, ``cache``, ...) are copied
    through unchanged. Used by ``kinetic config migrate``.
    """
    pipeline = cfg.get("pipeline")
    if not isinstance(pipeline, Mapping):
        raise ConfigError("config has no 'pipeline' mapping to migrate.")
    if "steps" in pipeline:
        raise ConfigError("config already uses the current 'pipeline.steps' shape.")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        steps = parse_legacy_pipeline(pipeline, source=source)

    migrated: JsonDict = {k: v for k, v in cfg.items() if k != "pipeline"}
    migrated["pipeline"] = {
        "steps": [
            {"task": step.task, "params": dict(step.params)} if step.params else {"task": step.task}
            for step in steps
        ]
    }
    return migrated


def _require_mapping(value: Any, *, name: str, where: str) -> Mapping[str, Any]:
    if value is None:
        raise ConfigError(f"{where}'{name}' cannot be null when the key is present.")
    if not isinstance(value, Mapping):
        raise ConfigError(f"{where}'{name}' must be a mapping, got {type(value).__name__}.")
    return value


def _require_task_name(value: Any, *, field: str, location: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(
            f"{where}{location} requires a non-empty string '{field}', got {value!r}."
        )
    return value.strip()


def _parse_transform(block: Any, *, where: str) -> List[Any]:
    from kinetic.core.pipeline.plan import Step

    if block is None:
        raise ConfigError(f"{where}'pipeline.transform' cannot be null when the key is present.")
    if isinstance(block, Mapping):
        task = _infer_transform_task(block, where=where)
        params = dict(block)
        params.pop("type", None)
        return [Step(task=task, params=params)]
    if isinstance(block, list):
        out: List[Any] = []
        for i, item in enumerate(block):
            position = f"pipeline.transform[{i}]"
            if item is None:
                raise ConfigError(f"{where}{position} is null.")
            if not isinstance(item, Mapping):
                raise ConfigError(
                    f"{where}{position} must be a mapping, got {type(item).__name__}."
                )
            if not item:
                raise ConfigError(f"{where}{position} is an empty mapping.")
            task, params = _normalize_transform_item(item, position=position, where=where)
            if task == "transform" and not params:
                raise ConfigError(
                    f"{where}{position} resolved to the task name 'transform' with no "
                    'parameters. Name the task explicitly, e.g. { "type": "dedupe_articles" }.'
                )
            out.append(Step(task=task, params=params))
        return out
    raise ConfigError(
        f"{where}'pipeline.transform' must be a mapping or a list, got " f"{type(block).__name__}."
    )


def _normalize_transform_item(
    item: Mapping[str, Any], *, position: str, where: str
) -> Tuple[str, JsonDict]:
    declared = item.get("type")
    if isinstance(declared, str) and declared.strip():
        params = dict(item)
        params.pop("type", None)
        return declared.strip(), params

    if len(item) == 1:
        key, value = next(iter(item.items()))
        if isinstance(key, str) and key.strip():
            if value is True or value is None:
                return key, {}
            if isinstance(value, Mapping):
                return key, dict(value)
            return key, {"value": value}

    task = _infer_transform_task(item, where=where, position=position)
    params = dict(item)
    params.pop("type", None)
    return task, params


def _infer_transform_task(
    block: Mapping[str, Any], *, where: str, position: str = "pipeline.transform"
) -> str:
    declared = block.get("type")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    if block.get("dedupe") is True:
        return "dedupe"
    for key, value in block.items():
        if value is True and isinstance(key, str):
            return key
    if not block:
        raise ConfigError(f"{where}{position} is an empty mapping; name a task with 'type'.")
    raise ConfigError(
        f"{where}{position}: cannot infer a task name from this mapping. Set 'type', "
        'or use a one-key item like { "dedupe_articles": true }.'
    )

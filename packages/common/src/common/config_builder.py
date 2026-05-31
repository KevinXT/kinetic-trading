"""
config_builder.py

Lightweight YAML configuration loader with support for:

- Recursive `include:` chains
- Deep merging of config layers into one runtime dictionary

This allows configs to stay modular (split across multiple files),
while producing a single fully-resolved config object for execution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

import yaml

from common.errors import ConfigError

JsonDict = Dict[str, Any]

# Optional, git-ignored local override file deep-merged over the selected config
# at runtime (e.g. to supply a real providers.bigquery.project_id without putting
# it in public configs). Absent file => no change.
DEFAULT_LOCAL_CONFIG_PATH = "configs/local.yaml"


def load_yaml(path: str | Path) -> JsonDict:
    """
    Load a single YAML file and return its contents as a dictionary.

    Rules:
    - Empty YAML returns {}
    - YAML root must be a mapping/dict
    """
    p = Path(path)

    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise ConfigError(
            f"Config file {p} must contain key/value pairs, not a {type(data).__name__}."
        )

    return data


def deep_merge_dicts(base: JsonDict, override: JsonDict) -> JsonDict:
    """
    Recursively merge two dictionaries without mutating inputs.

    Merge behavior:
    - dict + dict -> merge recursively
    - otherwise   -> override replaces base
    - override wins only where keys are present; keys absent from ``override``
      keep their ``base`` values (missing keys never delete base keys).
    """
    out: JsonDict = dict(base)

    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge_dicts(out[k], v)
        else:
            out[k] = v

    return out


# Backwards-compatible alias (original public name).
deep_merge = deep_merge_dicts


def _format_include_chain(stack: Tuple[Path, ...], current: Path) -> str:
    return " -> ".join(str(p) for p in (*stack, current))


def load_config(path: str | Path, *, _stack: Tuple[Path, ...] = ()) -> JsonDict:
    """
    Load a YAML config file with optional recursive includes.

    Supports::

        include:
          - base.yaml
          - risk.yaml

    Merge order:
    1. Included files (in list order)
    2. Current file overwrites on top

    Include paths resolve relative to the current file.

    Raises:
        ConfigError: Missing file, circular includes, or invalid ``include`` shape.
    """
    return _load_config_impl(Path(path), _stack)


def _load_config_impl(p: Path, stack: Tuple[Path, ...]) -> JsonDict:
    p = p.resolve()
    if p in stack:
        raise ConfigError(
            f"Circular YAML include chain detected:\n  {_format_include_chain(stack, p)}"
        )

    if not stack and not p.is_file():
        raise ConfigError(f"Config file not found or not a regular file: {p}")

    data = load_yaml(p)

    raw = data.pop("include", [])
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ConfigError(
            f"'include' must be a YAML list of file path strings in {p}, "
            f"got {type(raw).__name__}."
        )

    merged: JsonDict = {}
    next_stack = stack + (p,)

    for inc in raw:
        if not isinstance(inc, str) or not inc.strip():
            raise ConfigError(
                f"Invalid 'include' entry in {p}: expected a non-empty string path, "
                f"got {inc!r}."
            )
        inc_rel = inc.strip()
        inc_path = (p.parent / inc_rel).resolve()
        if not inc_path.is_file():
            raise ConfigError(
                "Included YAML not found or not a regular file.\n"
                f"  parent config: {p}\n"
                f"  include path: {inc_rel!r}\n"
                f"  resolved path: {inc_path}"
            )
        merged = deep_merge(merged, _load_config_impl(inc_path, next_stack))

    return deep_merge(merged, data)


def apply_local_overrides(
    cfg: JsonDict, *, local_path: str | Path = DEFAULT_LOCAL_CONFIG_PATH
) -> JsonDict:
    """
    Deep-merge a git-ignored local override file over ``cfg`` if it exists.

    Returns ``cfg`` unchanged when ``local_path`` is absent. When present, its
    values win field-by-field (e.g. ``providers.bigquery.project_id`` overrides
    only that field while ``providers.bigquery.table`` from the base is kept).
    Inputs are not mutated.
    """
    p = Path(local_path)
    if not p.is_file():
        return cfg
    local = load_yaml(p)
    return deep_merge_dicts(cfg, local)


def load_runtime_config(
    path: str | Path, *, local_path: str | Path = DEFAULT_LOCAL_CONFIG_PATH
) -> JsonDict:
    """
    Load ``path`` (with recursive includes) and apply optional local overrides.

    This is the runtime entry point used by the pipeline runner so every config
    passed to the CLI picks up ``configs/local.yaml`` (when present) *before*
    validation/execution. Absent local file => identical to :func:`load_config`.
    """
    cfg = load_config(path)
    return apply_local_overrides(cfg, local_path=local_path)


def build_runtime_config(
    *,
    presets_path: str | Path,
    env_path: str | Path | None = None,
    strategy_path: str | Path | None = None,
) -> JsonDict:
    """
    Build the final runtime configuration dictionary.

    Resolution order:

    1) Presets layer:
           cfg = load_config(presets_path)

    2) Environment overrides (optional):
           env file may contain:
               overrides: {...}

    3) Strategy config attachment (optional):
           merged under:
               cfg["strategy_config"]

    Returns:
        Fully-resolved runtime config dict.
    """
    cfg = load_config(presets_path)

    if env_path:
        env_cfg = load_config(env_path)
        overrides = env_cfg.get("overrides", {})

        if overrides and not isinstance(overrides, dict):
            raise ConfigError(
                f"Invalid config in {env_path}: 'overrides' must be a dict"
            )

        cfg = deep_merge(cfg, overrides)

    if strategy_path:
        strat_cfg = load_config(strategy_path)
        cfg = deep_merge(cfg, {"strategy_config": strat_cfg})

    return cfg

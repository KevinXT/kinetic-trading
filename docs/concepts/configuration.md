# Config builder (`common.config_builder`)

Lightweight YAML configuration with recursive `include:` and deep merging into one runtime Python dictionary. Keeps config files modular across multiple YAML layers.

**Source code:** `src/kinetic/core/config.py`

---

## Features

- Safe YAML parsing via `yaml.safe_load`
- Enforces dictionary-shaped YAML roots
- Recursive `include:` chains
- Deep-merge of layers (override wins)
- Optional environment override layer
- Optional strategy config attachment under `strategy_config`

---

## Types

`JsonDict` — nested `dict` representing config.

---

## Errors

`ConfigError` (from `common.errors`) when:

- YAML root is not a mapping/dict
- `include` is not a list
- Include entries are invalid or empty paths

---

## Functions

### `load_yaml(path) -> JsonDict`

Loads a single YAML file; root must be a dict. Empty file → `{}`.

### `deep_merge(base, override) -> JsonDict`

Recursively merges two dicts without mutating inputs. Dict + dict merges recursively; otherwise override replaces base.

### `load_config(path) -> JsonDict`

Loads YAML with optional includes:

```yaml
include:
  - base.yaml
  - risk.yaml
```

Merge order:

1. Included files (in list order), merged recursively
2. Current file merged on top

`include` is removed from the final output. Paths resolve relative to the current file. Includes may nest.

### `build_runtime_config(...) -> JsonDict`

Builds the final runtime config:

1. Load presets via `load_config(presets_path)`
2. Optional env file: merge `overrides` on top
3. Optional strategy file: merge under `cfg["strategy_config"]`

---

*Adapted from the former `DOCS/common/CONFIG_BUILDER.md`.*

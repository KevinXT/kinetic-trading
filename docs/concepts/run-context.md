# Run context (`kinetic.core.pipeline.context`)

`RunContext` is the workspace manager for a single experiment or strategy run: resolved config, run directory, artifact layout, and helpers for JSON / JSONL output.

**Source code:** `src/kinetic/core/pipeline/context.py`

---

## Purpose

Every run should have:

- A unique folder on disk
- A config snapshot
- A standardized `artifacts/` directory
- Helpers for writing outputs instead of ad hoc paths

Typical usage:

```python
ctx.write_json("metrics.json", {"sharpe": 1.4})
ctx.write_jsonl("events.jsonl", rows, subdir="logs")
```

---

## Fields

| Field | Meaning |
|-------|---------|
| `cfg` | Fully resolved runtime config (often from `common.config_builder`) |
| `run_name` | Human-readable name, e.g. `gdelt_volume_confirm` |
| `run_id` | Unique instance id |
| `run_dir` | Root folder for this run |
| `state` | Mutable shared dict for passing data between tasks and hooks |

---

## State conventions

`ctx.state` is an open dict shared across tasks and hooks within a single run.

- **Engine-managed keys** use a `_pipeline_` prefix (e.g. `_pipeline_steps_executed`, `_pipeline_final_status`). Tasks should not read or write these.
- **Task-managed keys** use unprefixed, descriptive names (e.g. `"docs"`, `"result"`). Tasks own their keys and should document what they produce/consume.
- No schema enforcement exists yet. Collision avoidance is the caller's responsibility. This is intentional — the contract may evolve later.

---

## Folder layout

On init, `run_dir` and `run_dir/artifacts` are created.

- **`artifacts_dir`** — `run_dir / "artifacts"` (default output root)

### `subdir(*parts) -> Path`

Creates nested folders under `artifacts/`, e.g. `ctx.subdir("charts", "signals")`.

---

## Writers

### `write_json(name, obj, *, subdir=None) -> Path`

One JSON object per file — metrics, summaries, snapshots.

### `write_jsonl(name, rows, *, subdir=None) -> Path`

JSON Lines — event streams, logs, large row sets.

---

## Design note

Centralizing writes avoids scattered paths and missing artifacts. Similar in spirit to local experiment trackers, but implemented explicitly in-repo.

---

*Adapted from the former `DOCS/core/pipeline_engine/engine/CONTEXT.md`; paths updated for the monorepo.*

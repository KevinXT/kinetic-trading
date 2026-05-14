"""
Defines RunContext: the shared execution context for a single pipeline run.

RunContext owns:
- the resolved runtime config
- the run directory + artifacts folder
- lightweight helpers for writing outputs (JSON/JSONL)
- a mutable ``state`` dict for passing data between tasks and hooks

All pipeline modules receive a RunContext to ensure consistent
state and artifact management across the system.

State contract
--------------
``ctx.state`` is an open dict shared across tasks and hooks within a run.

- **Engine-managed keys** use a ``_pipeline_`` prefix (e.g.
  ``_pipeline_steps_executed``, ``_pipeline_final_status``). These are
  reserved — tasks should not read or write them.
- **Task-managed keys** should use unprefixed, descriptive names (e.g.
  ``"docs"``, ``"result"``). Tasks own their keys and should document
  what they produce/consume.
- There is no schema enforcement yet. Collisions between tasks are the
  caller's responsibility. This may evolve toward a more structured
  contract later, but the current lightweight approach is intentional.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable

JsonDict = Dict[str, Any]


@dataclass
class RunContext:
    # Fully resolved runtime config for this run
    cfg: JsonDict

    # Metadata used for naming/logging runs
    run_name: str
    run_id: str

    # Root folder where this run writes outputs
    run_dir: Path

    # Mutable shared state for tasks and hooks. See module docstring for conventions.
    state: JsonDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Guarantee run folder + artifacts folder exist immediately
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    @property
    def artifacts_dir(self) -> Path:
        # Canonical location for all run outputs
        return self.run_dir / "artifacts"

    def subdir(self, *parts: str) -> Path:
        # Create nested artifact folders (e.g. logs/, charts/)
        p = self.artifacts_dir.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def write_json(self, name: str, obj: Any, *, subdir: str | None = None) -> Path:
        # Save one structured object (metrics, config snapshot, summary)
        base = self.subdir(subdir) if subdir else self.artifacts_dir
        path = base / name
        try:
            text = json.dumps(obj, indent=2, ensure_ascii=False)
        except TypeError as e:
            raise TypeError(
                f"RunContext.write_json: object is not JSON-serializable "
                f"(artifact {name!r}): {e}"
            ) from e
        path.write_text(text, encoding="utf-8")
        return path

    def write_jsonl(self, name: str, rows: Iterable[dict], *, subdir: str | None = None) -> Path:
        # Save streaming row-based logs (trades, events) as JSON Lines
        base = self.subdir(subdir) if subdir else self.artifacts_dir
        path = base / name
        line = 0
        with path.open("w", encoding="utf-8") as f:
            for r in rows:
                line += 1
                if not isinstance(r, dict):
                    raise TypeError(
                        f"RunContext.write_jsonl: row {line} must be a dict, got {type(r).__name__}"
                    )
                try:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                except TypeError as e:
                    raise TypeError(
                        f"RunContext.write_jsonl: row {line} is not JSON-serializable: {e}"
                    ) from e
        return path

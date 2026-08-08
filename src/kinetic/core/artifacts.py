"""Where a run writes, and the two files every run leaves behind.

A run directory is ``<runs_root>/<slug(run_name)>/<run_id>`` and always contains:

- ``config_resolved.yaml`` — the fully merged config the run actually executed,
  written before the first step, so a failed run is still reproducible
- ``run_metadata.json`` — status, timings, git revision and failure details,
  written after the last hook

Per-step outputs go under ``artifacts/`` and are the task's business; see
:class:`kinetic.core.pipeline.context.RunContext`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict

import yaml

JsonDict = Dict[str, Any]

RESOLVED_CONFIG_FILENAME = "config_resolved.yaml"
RUN_METADATA_FILENAME = "run_metadata.json"


def slug_run_name(name: str) -> str:
    """Turn a human run name into a filesystem-safe directory slug."""
    s = name.strip().lower()
    safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in s)
    safe = re.sub(r"-+", "-", safe)
    safe = safe.strip("-")
    return safe or "unnamed"


def allocate_group_slug(runs_root: Path, base_slug: str) -> str:
    """Return an unused group directory name under ``runs_root``.

    Two different run names can slug to the same string, and the same pipeline is
    usually run more than once. Rather than merging them, the second and later
    groups get ``slug_2``, ``slug_3``, ... so runs are never silently interleaved.
    """
    root = Path(runs_root)
    root.mkdir(parents=True, exist_ok=True)
    if not (root / base_slug).exists():
        return base_slug
    n = 2
    while (root / f"{base_slug}_{n}").exists():
        n += 1
    return f"{base_slug}_{n}"


def allocate_run_dir(runs_root: str | Path, run_name: str, run_id: str) -> Path:
    """Compute (without creating) the directory for one run."""
    root = Path(runs_root)
    slug = allocate_group_slug(root, slug_run_name(run_name))
    return root / slug / run_id


def write_resolved_config(run_dir: Path, cfg: JsonDict) -> Path:
    """Write the fully resolved config that this run executed."""
    path = run_dir / RESOLVED_CONFIG_FILENAME
    text = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_run_metadata(run_dir: Path, metadata: JsonDict) -> None:
    """Write the run's status, timings, provenance and failure details."""
    path = run_dir / RUN_METADATA_FILENAME
    path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

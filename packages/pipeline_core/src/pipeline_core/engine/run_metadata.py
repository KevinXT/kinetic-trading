import json
import subprocess
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

JsonDict = Dict[str, Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_head_sha(cwd: Optional[Path] = None) -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            text=True,
            timeout=3,
            stderr=subprocess.DEVNULL,
        )
        return out.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None


def find_git_root(start: Path, *, max_up: int = 24) -> Optional[Path]:
    """Walk parents from ``start`` until a directory contains ``.git``."""
    p = start.resolve()
    for _ in range(max_up):
        if (p / ".git").exists():
            return p
        parent = p.parent
        if parent == p:
            return None
        p = parent
    return None


def git_head_sha_near(anchor: Path) -> Optional[str]:
    """
    Resolve git HEAD using the repository that contains ``anchor`` (e.g. engine/ or package root).

    Prefer this over passing ``run_dir.parent`` when runs live outside the clone.
    """
    root = find_git_root(anchor)
    if root is None:
        return None
    return git_head_sha(cwd=root)


def write_resolved_config(run_dir: Path, cfg: JsonDict) -> Path:
    path = run_dir / "config_resolved.yaml"
    text = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_run_metadata(run_dir: Path, metadata: JsonDict) -> None:
    path = run_dir / "run_metadata.json"
    path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

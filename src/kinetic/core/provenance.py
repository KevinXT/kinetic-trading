"""Where a run came from: wall-clock timestamps and the git revision it ran at.

Kept separate from :mod:`kinetic.core.artifacts` (which writes run outputs)
because provenance answers a different question — not *what did this run
produce* but *what code and what moment produced it*. A research result that
cannot be tied back to a revision is not reproducible.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def utc_now_iso() -> str:
    """Current time as a timezone-aware ISO-8601 UTC string."""
    return datetime.now(timezone.utc).isoformat()


def git_head_sha(cwd: Optional[Path] = None) -> Optional[str]:
    """Resolve ``git rev-parse HEAD``, or ``None`` when git is unavailable."""
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
    """Resolve git HEAD using the repository that contains ``anchor``.

    Prefer this over passing ``run_dir.parent``: runs are written under
    ``warehouse/runs`` but may be redirected anywhere, and the revision we care
    about is the one the *code* was loaded from.
    """
    root = find_git_root(anchor)
    if root is None:
        return None
    return git_head_sha(cwd=root)

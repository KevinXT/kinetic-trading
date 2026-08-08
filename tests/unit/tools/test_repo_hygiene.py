"""Offline tests for repository release-hygiene scripts."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "scripts" / "dev"


def _run(
    script: str,
    *,
    env: dict[str, str] | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        ["bash", str(SCRIPTS / script)],
        cwd=REPO_ROOT,
        env=merged,
        text=True,
        capture_output=True,
        check=check,
    )


def test_check_build_pollution_clean_by_default() -> None:
    # Ensure no leftover pollution from other tests before asserting.
    _run("clean_build.sh", check=True)
    result = _run("check_build_pollution.sh")
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def test_check_build_pollution_detects_a_stale_build_tree(tmp_path: Path) -> None:
    _run("clean_build.sh", check=True)
    build_dir = REPO_ROOT / "build" / "lib"
    build_dir.mkdir(parents=True, exist_ok=True)
    marker = build_dir / "pollution_marker.txt"
    marker.write_text("x", encoding="utf-8")
    try:
        result = _run("check_build_pollution.sh")
        assert result.returncode == 1
        assert "build/" in result.stderr
    finally:
        _run("clean_build.sh", check=True)


def test_check_dirty_tree_override() -> None:
    result = _run("check_dirty_tree.sh", env={"ALLOW_DIRTY_TREE": "1"})
    assert result.returncode == 0, result.stderr
    assert "overridden" in result.stdout

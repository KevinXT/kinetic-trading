"""Tests for the collection runner orchestration script."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.run_collections import discover_configs, run_all, run_config

# ── discovery ────────────────────────────────────────────────────────────


def test_discover_finds_yaml_recursively(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.yaml").write_text("name: one\n")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "two.yaml").write_text("name: two\n")
    (tmp_path / "b" / "skip.txt").write_text("not yaml")

    found = discover_configs(tmp_path)
    assert len(found) == 2
    assert all(p.suffix == ".yaml" for p in found)


def test_discover_returns_empty_for_no_yaml(tmp_path: Path) -> None:
    (tmp_path / "readme.md").write_text("nothing")
    assert discover_configs(tmp_path) == []


# ── deterministic sorting ────────────────────────────────────────────────


def test_discover_sorts_deterministically(tmp_path: Path) -> None:
    for name in ["z.yaml", "a.yaml", "m.yaml"]:
        (tmp_path / name).write_text(f"name: {name}\n")

    found = discover_configs(tmp_path)
    names = [p.name for p in found]
    assert names == ["a.yaml", "m.yaml", "z.yaml"]


def test_discover_sorts_across_subdirs(tmp_path: Path) -> None:
    (tmp_path / "risk").mkdir()
    (tmp_path / "macro").mkdir()
    (tmp_path / "risk" / "geo.yaml").write_text("name: geo\n")
    (tmp_path / "macro" / "cpi.yaml").write_text("name: cpi\n")

    found = discover_configs(tmp_path)
    assert "macro" in str(found[0])
    assert "risk" in str(found[1])


# ── max-configs limiting ─────────────────────────────────────────────────


@patch("scripts.run_collections.run_config")
@patch("scripts.run_collections.time.sleep")
def test_max_configs_limits_runs(
    mock_sleep: MagicMock, mock_run: MagicMock, tmp_path: Path
) -> None:
    for i in range(5):
        (tmp_path / f"{i}.yaml").write_text(f"name: {i}\n")

    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="ok", stderr=""
    )

    summary = run_all(discover_configs(tmp_path), sleep_seconds=0, max_configs=2)
    assert summary["total"] == 2
    assert summary["succeeded"] == 2
    assert mock_run.call_count == 2


# ── sleep behaviour ──────────────────────────────────────────────────────


@patch("scripts.run_collections.run_config")
@patch("scripts.run_collections.time.sleep")
def test_zero_sleep_never_sleeps(
    mock_sleep: MagicMock, mock_run: MagicMock, tmp_path: Path
) -> None:
    for name in ["a.yaml", "b.yaml", "c.yaml"]:
        (tmp_path / name).write_text(f"name: {name}\n")

    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr=""
    )

    run_all(discover_configs(tmp_path), sleep_seconds=0)
    mock_sleep.assert_not_called()


@patch("scripts.run_collections.run_config")
@patch("scripts.run_collections.time.sleep")
def test_sleep_called_between_runs(
    mock_sleep: MagicMock, mock_run: MagicMock, tmp_path: Path
) -> None:
    for name in ["a.yaml", "b.yaml", "c.yaml"]:
        (tmp_path / name).write_text(f"name: {name}\n")

    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr=""
    )

    run_all(discover_configs(tmp_path), sleep_seconds=5)
    assert mock_sleep.call_count == 2  # between 3 runs = 2 sleeps
    mock_sleep.assert_called_with(5)


# ── subprocess invocation ────────────────────────────────────────────────


@patch("scripts.run_collections.subprocess.run")
def test_run_config_invokes_trading_platform(mock_run: MagicMock) -> None:
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="done", stderr=""
    )
    cfg = Path("configs/collections/macro/inflation_rates.yaml")
    run_config(cfg)

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "-m" in args
    assert "trading_platform" in args
    assert str(cfg) in args


# ── failure handling ─────────────────────────────────────────────────────


@patch("scripts.run_collections.run_config")
@patch("scripts.run_collections.time.sleep")
def test_failure_continues_execution(
    mock_sleep: MagicMock, mock_run: MagicMock, tmp_path: Path
) -> None:
    for name in ["a.yaml", "b.yaml", "c.yaml"]:
        (tmp_path / name).write_text(f"name: {name}\n")

    ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
    mock_run.side_effect = [ok, fail, ok]

    summary = run_all(discover_configs(tmp_path), sleep_seconds=0)

    assert mock_run.call_count == 3
    assert summary["succeeded"] == 2
    assert summary["failed"] == 1
    assert len(summary["failed_configs"]) == 1


@patch("scripts.run_collections.run_config")
@patch("scripts.run_collections.time.sleep")
def test_all_failures_reported(
    mock_sleep: MagicMock, mock_run: MagicMock, tmp_path: Path
) -> None:
    for name in ["a.yaml", "b.yaml"]:
        (tmp_path / name).write_text(f"name: {name}\n")

    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="error"
    )

    summary = run_all(discover_configs(tmp_path), sleep_seconds=0)

    assert summary["succeeded"] == 0
    assert summary["failed"] == 2
    assert len(summary["failed_configs"]) == 2


# ── summary structure ────────────────────────────────────────────────────


@patch("scripts.run_collections.run_config")
@patch("scripts.run_collections.time.sleep")
def test_summary_contains_expected_keys(
    mock_sleep: MagicMock, mock_run: MagicMock, tmp_path: Path
) -> None:
    (tmp_path / "x.yaml").write_text("name: x\n")

    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr=""
    )

    summary = run_all(discover_configs(tmp_path), sleep_seconds=0)

    assert set(summary.keys()) == {
        "total",
        "succeeded",
        "failed",
        "failed_configs",
        "elapsed_s",
    }
    assert isinstance(summary["elapsed_s"], float)

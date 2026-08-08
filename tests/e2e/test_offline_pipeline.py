"""One small, complete pipeline, run through the real CLI.

This is the test that would have caught the refactor breaking: it goes from a
command line, through config loading, the composition root, the registry, the
runner, a real task, and out to artifacts on disk — with no network, no
credentials and no cloud account.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from kinetic.interface.cli.app import app

runner = CliRunner()

DEMO_CONFIG = "configs/research/news_market_dataset_demo.yaml"


def _run(tmp_path: Path, run_id: str = "e2e"):
    result = runner.invoke(
        app,
        ["run", DEMO_CONFIG, "--runs-root", str(tmp_path), "--run-id", run_id],
    )
    assert result.exit_code == 0, result.output
    return result


def test_offline_demo_pipeline_produces_a_complete_run(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert "completed run: news_market_dataset_demo" in result.output

    run_dir = tmp_path / "news_market_dataset_demo" / "e2e"
    assert run_dir.is_dir()

    # Every run leaves the config it actually executed...
    resolved = yaml.safe_load((run_dir / "config_resolved.yaml").read_text(encoding="utf-8"))
    assert resolved["pipeline"]["steps"][0]["task"] == "research.build_news_market_dataset"

    # ...and a metadata record with status, timings and provenance.
    meta = json.loads((run_dir / "run_metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "completed"
    assert meta["run_name"] == "news_market_dataset_demo"
    assert meta["run_id"] == "e2e"
    assert meta["error"] is None
    assert len(meta["steps_executed"]) == 1
    assert meta["steps_executed"][0]["task"] == "research.build_news_market_dataset"
    assert "started_at" in meta and "completed_at" in meta
    assert "git_commit" in meta

    assert not (run_dir / "traceback.txt").exists()


def test_offline_demo_pipeline_writes_research_artifacts(tmp_path: Path) -> None:
    _run(tmp_path)
    artifacts = tmp_path / "news_market_dataset_demo" / "e2e" / "artifacts"
    assert artifacts.is_dir()
    produced = sorted(p.name for p in artifacts.rglob("*") if p.is_file())
    assert produced, "the research task produced no artifacts"
    # The manifest is what makes a research dataset reproducible; it must exist.
    assert any("manifest" in name for name in produced), produced


# Fields that legitimately differ between two runs: they record *when the run
# happened*, which is point-in-time metadata, not a computed result. The
# in-process reproducibility test injects a fixed clock and gets byte-identical
# output; a CLI run cannot, and should not pretend to.
RUN_TIMESTAMP_FIELDS = (
    "generated_at",
    "ingested_at",
    "feature_available_at",
    "observed_at",
)


def _strip_run_timestamps(text: str) -> str:
    import re

    for field in RUN_TIMESTAMP_FIELDS:
        text = re.sub(rf'"{field}": "[^"]*"', f'"{field}": "<ts>"', text)
    return text


def test_offline_demo_pipeline_is_reproducible_across_reruns(tmp_path: Path) -> None:
    """Two CLI runs over committed fixtures must agree on every computed value.

    Only the recorded wall-clock timestamps may differ.
    """
    first = tmp_path / "first"
    second = tmp_path / "second"
    _run(first)
    _run(second)

    a = first / "news_market_dataset_demo" / "e2e" / "artifacts"
    b = second / "news_market_dataset_demo" / "e2e" / "artifacts"

    files_a = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    assert files_a == files_b
    assert files_a, "the demo pipeline produced no artifacts"

    differing = [
        str(rel)
        for rel in files_a
        if _strip_run_timestamps((a / rel).read_text(encoding="utf-8"))
        != _strip_run_timestamps((b / rel).read_text(encoding="utf-8"))
    ]
    assert not differing, f"non-deterministic artifacts: {differing}"


def test_deprecated_task_names_still_run_and_warn(tmp_path: Path) -> None:
    """The one compatibility path we kept has to actually work."""
    legacy_config = tmp_path / "legacy.yaml"
    demo = yaml.safe_load(Path(DEMO_CONFIG).read_text(encoding="utf-8"))
    demo["pipeline"]["steps"][0]["task"] = "build_news_market_dataset"
    legacy_config.write_text(yaml.safe_dump(demo, sort_keys=False), encoding="utf-8")

    result = runner.invoke(
        app,
        ["run", str(legacy_config), "--runs-root", str(tmp_path / "runs"), "--run-id", "legacy"],
    )
    assert result.exit_code == 0, result.output
    meta = json.loads(
        (tmp_path / "runs" / "news_market_dataset_demo" / "legacy" / "run_metadata.json").read_text(
            encoding="utf-8"
        )
    )
    assert meta["status"] == "completed"

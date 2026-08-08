"""The `kinetic` CLI: help, task listing, config validation and migration.

These run the real Typer app in-process. They deliberately do not run a pipeline
— that is covered end to end by ``tests/e2e/test_offline_pipeline.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from kinetic.interface.cli.app import app

runner = CliRunner()

DEMO_CONFIG = "configs/research/news_market_dataset_demo.yaml"


def test_help_lists_the_top_level_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("run", "pipeline", "task", "config", "cost"):
        assert command in result.output


def test_version() -> None:
    from kinetic import __version__

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_bare_invocation_shows_help_rather_than_erroring() -> None:
    result = runner.invoke(app, [])
    assert "Usage: kinetic" in result.output


def test_task_list_shows_namespaced_ids_and_hides_aliases() -> None:
    result = runner.invoke(app, ["task", "list"])
    assert result.exit_code == 0
    lines = [line.strip() for line in result.output.splitlines() if line.strip()]
    assert "research.build_news_market_dataset" in lines
    assert "news.gdelt.fetch_articles" in lines
    # Deprecated names must not appear in the advertised interface.
    assert "gdelt_docs" not in lines
    assert all("." in line for line in lines), lines


def test_task_list_can_show_deprecated_aliases() -> None:
    result = runner.invoke(app, ["task", "list", "--show-deprecated"])
    assert result.exit_code == 0
    assert "deprecated aliases:" in result.output
    assert "gdelt_docs -> news.gdelt.fetch_articles" in result.output


def test_config_validate_accepts_the_demo_config() -> None:
    result = runner.invoke(app, ["config", "validate", DEMO_CONFIG])
    assert result.exit_code == 0, result.output
    assert "valid" in result.output
    assert "research.build_news_market_dataset" in result.output


def _pipeline_configs() -> list[str]:
    """Every checked-in config that actually describes a pipeline."""
    out = []
    for path in sorted(Path("configs").rglob("*.yaml")):
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and isinstance(loaded.get("pipeline"), dict):
            out.append(str(path))
    for path in sorted(Path("projects").rglob("configs/*.yaml")):
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and isinstance(loaded.get("pipeline"), dict):
            out.append(str(path))
    return out


@pytest.mark.parametrize("config", _pipeline_configs())
def test_every_checked_in_config_validates(config: str) -> None:
    """No checked-in config may name a task the platform does not have."""
    result = runner.invoke(app, ["config", "validate", "--strict-task-names", config])
    assert result.exit_code == 0, f"{config}: {result.output}"


def test_config_validate_reports_a_missing_file() -> None:
    result = runner.invoke(app, ["config", "validate", "configs/does-not-exist.yaml"])
    assert result.exit_code == 1
    assert "config not found" in result.output


def test_config_validate_reports_an_unknown_task(tmp_path: Path) -> None:
    config = tmp_path / "bad.yaml"
    config.write_text(
        "name: bad\npipeline:\n  steps:\n    - task: news.gdelt.fetch_article\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["config", "validate", str(config)])
    assert result.exit_code == 1
    assert "unknown task" in result.output


def test_config_validate_reports_a_malformed_step(tmp_path: Path) -> None:
    config = tmp_path / "bad.yaml"
    config.write_text(
        "name: bad\npipeline:\n  steps:\n    - task: news.tag_articles\n      params: 3\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["config", "validate", str(config)])
    assert result.exit_code == 1
    assert "pipeline.steps[0]" in result.output
    assert "news.tag_articles" in result.output
    assert "'params' must be a mapping" in result.output


def test_config_migrate_rewrites_a_legacy_config(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text(
        "name: legacy\n"
        "pipeline:\n"
        "  ingest:\n"
        "    source: gdelt_docs\n"
        "    max_records: 10\n"
        "  transform:\n"
        "    - type: dedupe_articles\n",
        encoding="utf-8",
    )
    output = tmp_path / "migrated.yaml"
    result = runner.invoke(app, ["config", "migrate", str(legacy), "--output", str(output)])
    assert result.exit_code == 0, result.output

    text = output.read_text(encoding="utf-8")
    assert "steps:" in text
    # Legacy task names are translated to their current identifiers.
    assert "news.gdelt.fetch_articles" in text
    assert "news.dedupe_articles" in text
    assert "ingest:" not in text

    # The migrated file must validate with no deprecation left in it.
    validated = runner.invoke(app, ["config", "validate", "--strict-task-names", str(output)])
    assert validated.exit_code == 0, validated.output


def test_config_migrate_refuses_a_current_config() -> None:
    result = runner.invoke(app, ["config", "migrate", DEMO_CONFIG])
    assert result.exit_code == 1
    assert "already uses the current" in result.output


def test_run_reports_a_missing_config() -> None:
    result = runner.invoke(app, ["run", "configs/nope.yaml"])
    assert result.exit_code == 1
    assert "config not found" in result.output

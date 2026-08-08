"""Runner failure paths and run-metadata side effects."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kinetic.core.errors import PipelineError
from kinetic.core.pipeline.registry import TaskRegistry
from kinetic.core.pipeline.runner import run_pipeline


def _plan(task: str = "news.tag_articles") -> dict:
    return {
        "name": "failure_test",
        "pipeline": {"steps": [{"task": task, "params": {"q": 1}}]},
    }


def test_run_metadata_on_task_failure(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"

    def boom(ctx, params):  # type: ignore[no-untyped-def]
        raise ValueError("task failed")

    with pytest.raises(ValueError, match="task failed"):
        run_pipeline(
            _plan("news.boom"),
            registry=TaskRegistry({"news.boom": boom}),
            run_dir=run_root / "r1",
            run_id="rid1",
        )

    meta_path = run_root / "r1" / "run_metadata.json"
    assert meta_path.is_file()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["status"] == "failed"
    assert meta["failed_step"]["task"] == "news.boom"
    assert meta["error"]["type"] == "ValueError"
    tb = run_root / "r1" / "traceback.txt"
    assert tb.is_file()
    assert "ValueError" in tb.read_text(encoding="utf-8")


def test_run_metadata_success(tmp_path: Path) -> None:
    def ok(ctx, params):  # type: ignore[no-untyped-def]
        pass

    ctx = run_pipeline(
        _plan("news.ok"),
        registry=TaskRegistry({"news.ok": ok}),
        run_dir=tmp_path / "ok",
        run_id="r2",
    )
    assert ctx.run_name == "failure_test"
    meta = json.loads((tmp_path / "ok" / "run_metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "completed"
    assert meta["failed_step"] is None
    assert meta["error"] is None
    assert len(meta["steps_executed"]) == 1
    assert meta["steps_executed"][0]["task"] == "news.ok"


def test_unregistered_task_records_a_failed_step(tmp_path: Path) -> None:
    with pytest.raises(PipelineError, match="No task registered"):
        run_pipeline(
            _plan("news.missing"),
            registry=TaskRegistry(),
            run_dir=tmp_path / "bad",
            run_id="r3",
        )
    meta = json.loads((tmp_path / "bad" / "run_metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "failed"
    assert meta["failed_step"]["task"] == "news.missing"


def test_resolved_config_is_written_before_the_first_step(tmp_path: Path) -> None:
    """A run that dies on step 1 must still be reproducible."""
    seen: dict[str, bool] = {}

    def check(ctx, params):  # type: ignore[no-untyped-def]
        seen["config_written"] = (ctx.run_dir / "config_resolved.yaml").is_file()

    run_pipeline(
        _plan("news.check"),
        registry=TaskRegistry({"news.check": check}),
        run_dir=tmp_path / "run",
        run_id="r4",
    )
    assert seen["config_written"] is True


def test_runs_root_allocates_a_named_group(tmp_path: Path) -> None:
    def ok(ctx, params):  # type: ignore[no-untyped-def]
        pass

    registry = TaskRegistry({"news.ok": ok})
    first = run_pipeline(_plan("news.ok"), registry=registry, runs_root=tmp_path, run_id="a")
    second = run_pipeline(_plan("news.ok"), registry=registry, runs_root=tmp_path, run_id="b")

    assert first.run_dir == tmp_path / "failure_test" / "a"
    # The slug is already taken, so the second run gets its own group.
    assert second.run_dir == tmp_path / "failure_test_2" / "b"

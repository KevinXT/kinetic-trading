"""Runner failure paths and metadata side effects."""

import json
from pathlib import Path

import pytest

from common.errors import PipelineError
from pipeline_core.engine.runner import run_plan


def _ingest_only_plan(source: str = "ok_ingest") -> dict:
    return {
        "name": "failure_test",
        "pipeline": {"ingest": {"source": source, "q": 1}},
    }


def test_run_metadata_on_task_failure(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"

    def boom(ctx, p):  # type: ignore[no-untyped-def]
        raise ValueError("task failed")

    with pytest.raises(ValueError, match="task failed"):
        run_plan(
            _ingest_only_plan("boom"),
            run_dir=run_root / "r1",
            run_name="failure_test",
            run_id="rid1",
            registry={"boom": boom},
        )

    meta_path = run_root / "r1" / "run_metadata.json"
    assert meta_path.is_file()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["status"] == "failed"
    assert meta["failed_step"]["task"] == "boom"
    assert meta["error"]["type"] == "ValueError"
    tb = run_root / "r1" / "traceback.txt"
    assert tb.is_file()
    assert "ValueError" in tb.read_text(encoding="utf-8")


def test_run_metadata_success(tmp_path: Path) -> None:

    def ok(ctx, p):  # type: ignore[no-untyped-def]
        pass

    run_plan(
        _ingest_only_plan("ok"),
        run_dir=tmp_path / "ok",
        run_name="failure_test",
        run_id="r2",
        registry={"ok": ok},
    )
    meta = json.loads((tmp_path / "ok" / "run_metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "completed"
    assert meta["failed_step"] is None
    assert meta["error"] is None
    assert len(meta["steps_executed"]) == 1
    assert meta["steps_executed"][0]["task"] == "ok"


def test_unregistered_task_pipeline_error_metadata(tmp_path: Path) -> None:
    with pytest.raises(PipelineError):
        run_plan(
            _ingest_only_plan("missing"),
            run_dir=tmp_path / "bad",
            run_name="failure_test",
            run_id="r3",
            registry={},
        )
    meta = json.loads((tmp_path / "bad" / "run_metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "failed"
    assert meta["failed_step"]["task"] == "missing"

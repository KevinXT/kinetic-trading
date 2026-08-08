"""RunContext artifact writers."""

from pathlib import Path

import pytest

from kinetic.core.pipeline.context import RunContext


def test_write_json_rejects_non_serializable(tmp_path: Path) -> None:
    ctx = RunContext(cfg={}, run_name="t", run_id="1", run_dir=tmp_path / "r")
    with pytest.raises(TypeError, match="JSON-serializable"):
        ctx.write_json("bad.json", {"fn": lambda: 1})


def test_write_jsonl_rejects_non_dict_row(tmp_path: Path) -> None:
    ctx = RunContext(cfg={}, run_name="t", run_id="1", run_dir=tmp_path / "r")
    with pytest.raises(TypeError, match="must be a dict"):
        ctx.write_jsonl("rows.jsonl", [{"ok": 1}, "not-a-dict"])


def test_write_json_roundtrip(tmp_path: Path) -> None:
    import json

    ctx = RunContext(cfg={}, run_name="t", run_id="1", run_dir=tmp_path / "r")
    p = ctx.write_json("x.json", {"a": 1})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1}

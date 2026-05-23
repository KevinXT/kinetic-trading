"""Tests for the store_features transform task."""

import json
from pathlib import Path

import pytest

from pipeline_core.engine.context import RunContext
from news_data.task.store_features import (
    store_features_task,
    _feature_key,
    _load_existing_keys,
)


# ── helpers ───────────────────────────────────────────────────────────────


def _ctx(tmp_path: Path, state: dict | None = None) -> RunContext:
    ctx = RunContext(cfg={}, run_name="t", run_id="1", run_dir=tmp_path / "run")
    if state:
        ctx.state.update(state)
    return ctx


def _row(
    *,
    date: str = "2026-05-20",
    topic: str = "ai",
    article_count: int = 3,
    earliest: str = "2026-05-20T08:00:00Z",
    latest: str = "2026-05-20T20:00:00Z",
) -> dict:
    return {
        "date": date,
        "topic": topic,
        "article_count": article_count,
        "unique_domains": 2,
        "unique_source_countries": 1,
        "domains": ["a.com", "b.com"],
        "source_countries": ["United States"],
        "primary_topic_count": article_count,
        "untagged_count": 0,
        "queries": [],
        "earliest_published_at": earliest,
        "latest_published_at": latest,
    }


def _read_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _summary(ctx: RunContext) -> dict:
    path = ctx.artifacts_dir / "store_features_summary.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ── feature key ──────────────────────────────────────────────────────────


def test_feature_key_deterministic() -> None:
    r = _row()
    assert _feature_key(r) == "2026-05-20|ai|2026-05-20T08:00:00Z|2026-05-20T20:00:00Z"


def test_feature_key_none_when_date_and_topic_empty() -> None:
    assert _feature_key({"date": "", "topic": ""}) is None


def test_feature_key_works_with_none_timestamps() -> None:
    r = _row()
    r["earliest_published_at"] = None
    r["latest_published_at"] = None
    key = _feature_key(r)
    assert key == "2026-05-20|ai||"


# ── input state ──────────────────────────────────────────────────────────


def test_raises_when_no_input_state(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    with pytest.raises(ValueError, match="article_features_daily"):
        store_features_task(ctx, {})


# ── default output path ─────────────────────────────────────────────────


def test_default_output_path_used(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"article_features_daily": [_row()]})

    import os
    original = os.getcwd()
    os.chdir(tmp_path)
    try:
        store_features_task(ctx, {})
    finally:
        os.chdir(original)

    assert (tmp_path / "data" / "processed" / "features" / "article_features_daily.jsonl").is_file()


# ── directory creation ───────────────────────────────────────────────────


def test_creates_parent_directories(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"article_features_daily": [_row()]})
    out = tmp_path / "deep" / "nested" / "features.jsonl"

    store_features_task(ctx, {"output_path": str(out)})

    assert out.is_file()


# ── append-only behavior ────────────────────────────────────────────────


def test_appends_new_rows(tmp_path: Path) -> None:
    out = tmp_path / "features.jsonl"
    existing = _row(date="2026-05-19", topic="energy", earliest="2026-05-19T06:00:00Z", latest="2026-05-19T18:00:00Z")
    out.write_text(json.dumps(existing) + "\n", encoding="utf-8")

    new_row = _row(date="2026-05-20", topic="ai")
    ctx = _ctx(tmp_path, {"article_features_daily": [new_row]})

    store_features_task(ctx, {"output_path": str(out)})

    records = _read_jsonl(out)
    assert len(records) == 2
    assert records[0]["topic"] == "energy"
    assert records[1]["topic"] == "ai"


def test_preserves_existing_rows(tmp_path: Path) -> None:
    out = tmp_path / "features.jsonl"
    existing = _row(date="2026-05-18", topic="bonds_yields", earliest="2026-05-18T10:00:00Z", latest="2026-05-18T22:00:00Z")
    out.write_text(json.dumps(existing) + "\n", encoding="utf-8")

    ctx = _ctx(tmp_path, {"article_features_daily": [_row()]})

    store_features_task(ctx, {"output_path": str(out)})

    records = _read_jsonl(out)
    assert records[0]["topic"] == "bonds_yields"
    assert records[0]["date"] == "2026-05-18"


# ── duplicate detection ─────────────────────────────────────────────────


def test_skips_duplicate_rows(tmp_path: Path) -> None:
    out = tmp_path / "features.jsonl"
    existing = _row()
    out.write_text(json.dumps(existing) + "\n", encoding="utf-8")

    ctx = _ctx(tmp_path, {"article_features_daily": [_row()]})

    store_features_task(ctx, {"output_path": str(out)})

    records = _read_jsonl(out)
    assert len(records) == 1

    s = _summary(ctx)
    assert s["skipped_duplicates"] == 1
    assert s["stored_rows"] == 0


def test_skips_within_same_batch(tmp_path: Path) -> None:
    out = tmp_path / "features.jsonl"
    r = _row()
    ctx = _ctx(tmp_path, {"article_features_daily": [r, r]})

    store_features_task(ctx, {"output_path": str(out)})

    records = _read_jsonl(out)
    assert len(records) == 1

    s = _summary(ctx)
    assert s["skipped_duplicates"] == 1
    assert s["stored_rows"] == 1


def test_different_timestamps_not_treated_as_duplicate(tmp_path: Path) -> None:
    out = tmp_path / "features.jsonl"
    r1 = _row(earliest="2026-05-20T08:00:00Z", latest="2026-05-20T12:00:00Z")
    r2 = _row(earliest="2026-05-20T14:00:00Z", latest="2026-05-20T20:00:00Z")

    ctx = _ctx(tmp_path, {"article_features_daily": [r1, r2]})

    store_features_task(ctx, {"output_path": str(out)})

    records = _read_jsonl(out)
    assert len(records) == 2


# ── load_existing_keys ───────────────────────────────────────────────────


def test_load_existing_keys_from_file(tmp_path: Path) -> None:
    out = tmp_path / "features.jsonl"
    out.write_text(json.dumps(_row()) + "\n", encoding="utf-8")

    keys = _load_existing_keys(out)
    assert len(keys) == 1
    assert "2026-05-20|ai|2026-05-20T08:00:00Z|2026-05-20T20:00:00Z" in keys


def test_load_existing_keys_missing_file(tmp_path: Path) -> None:
    keys = _load_existing_keys(tmp_path / "nonexistent.jsonl")
    assert keys == set()


def test_load_existing_keys_skips_corrupt_lines(tmp_path: Path) -> None:
    out = tmp_path / "features.jsonl"
    out.write_text("not json\n" + json.dumps(_row()) + "\n", encoding="utf-8")

    keys = _load_existing_keys(out)
    assert len(keys) == 1


# ── summary artifact ────────────────────────────────────────────────────


def test_summary_values_correct(tmp_path: Path) -> None:
    out = tmp_path / "features.jsonl"
    existing = _row(date="2026-05-19", topic="energy", earliest="2026-05-19T06:00:00Z", latest="2026-05-19T18:00:00Z")
    out.write_text(json.dumps(existing) + "\n", encoding="utf-8")

    new_row = _row(date="2026-05-20", topic="ai")
    dup_row = _row(date="2026-05-19", topic="energy", earliest="2026-05-19T06:00:00Z", latest="2026-05-19T18:00:00Z")
    ctx = _ctx(tmp_path, {"article_features_daily": [new_row, dup_row]})

    store_features_task(ctx, {"output_path": str(out)})

    s = _summary(ctx)
    assert s["input_rows"] == 2
    assert s["stored_rows"] == 1
    assert s["skipped_duplicates"] == 1
    assert s["output_path"] == str(out)
    assert s["total_rows_after_write"] == 2


def test_summary_artifact_written(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"article_features_daily": [_row()]})
    out = tmp_path / "features.jsonl"

    store_features_task(ctx, {"output_path": str(out)})

    assert (ctx.artifacts_dir / "store_features_summary.json").is_file()


# ── ctx.state updated ───────────────────────────────────────────────────


def test_state_updated_with_stored_rows(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"article_features_daily": [_row()]})
    out = tmp_path / "features.jsonl"

    store_features_task(ctx, {"output_path": str(out)})

    stored = ctx.state["stored_feature_rows"]
    assert len(stored) == 1
    assert stored[0]["topic"] == "ai"


# ── empty input ─────────────────────────────────────────────────────────


def test_empty_input_handled(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"article_features_daily": []})
    out = tmp_path / "features.jsonl"

    store_features_task(ctx, {"output_path": str(out)})

    assert out.is_file()
    records = _read_jsonl(out)
    assert len(records) == 0

    s = _summary(ctx)
    assert s["input_rows"] == 0
    assert s["stored_rows"] == 0
    assert s["skipped_duplicates"] == 0


# ── JSONL persistence correct ───────────────────────────────────────────


def test_jsonl_roundtrip(tmp_path: Path) -> None:
    r = _row()
    ctx = _ctx(tmp_path, {"article_features_daily": [r]})
    out = tmp_path / "features.jsonl"

    store_features_task(ctx, {"output_path": str(out)})

    records = _read_jsonl(out)
    assert len(records) == 1
    assert records[0]["date"] == "2026-05-20"
    assert records[0]["topic"] == "ai"
    assert records[0]["article_count"] == 3
    assert records[0]["earliest_published_at"] == "2026-05-20T08:00:00Z"
    assert records[0]["latest_published_at"] == "2026-05-20T20:00:00Z"

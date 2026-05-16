"""Tests for the store_articles transform task."""

import json
from pathlib import Path

import pytest

from pipeline_core.engine.context import RunContext
from news_data.task.store_articles import store_articles_task


# ── helpers ───────────────────────────────────────────────────────────────


def _ctx(tmp_path: Path, state: dict | None = None) -> RunContext:
    ctx = RunContext(cfg={}, run_name="t", run_id="1", run_dir=tmp_path / "run")
    if state:
        ctx.state.update(state)
    return ctx


def _article(
    *,
    title: str = "Article",
    url: str = "https://example.com",
    domain: str = "example.com",
) -> dict:
    return {
        "provider": "gdelt",
        "title": title,
        "url": url,
        "domain": domain,
        "language": "English",
        "source_country": "United States",
    }


def _read_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# ── input state selection ─────────────────────────────────────────────────


def test_prefers_deduped_over_filtered_and_normalized(tmp_path: Path) -> None:
    deduped = [_article(title="Deduped")]
    filtered = [_article(title="Filtered")]
    normalized = [_article(title="Normalized")]
    ctx = _ctx(tmp_path, {
        "deduped_articles": deduped,
        "filtered_articles": filtered,
        "normalized_articles": normalized,
    })
    out = tmp_path / "out.jsonl"

    store_articles_task(ctx, {"output_path": str(out)})

    records = _read_jsonl(out)
    assert len(records) == 1
    assert records[0]["title"] == "Deduped"
    assert ctx.state["stored_articles_summary"]["input_state_used"] == "deduped_articles"


def test_falls_back_to_filtered(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"filtered_articles": [_article(title="Filtered")]})
    out = tmp_path / "out.jsonl"

    store_articles_task(ctx, {"output_path": str(out)})

    records = _read_jsonl(out)
    assert records[0]["title"] == "Filtered"
    assert ctx.state["stored_articles_summary"]["input_state_used"] == "filtered_articles"


def test_falls_back_to_normalized(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"normalized_articles": [_article(title="Norm")]})
    out = tmp_path / "out.jsonl"

    store_articles_task(ctx, {"output_path": str(out)})

    records = _read_jsonl(out)
    assert records[0]["title"] == "Norm"
    assert ctx.state["stored_articles_summary"]["input_state_used"] == "normalized_articles"


def test_raises_when_no_input_state(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    with pytest.raises(ValueError, match="deduped_articles.*filtered_articles.*normalized_articles"):
        store_articles_task(ctx, {})


# ── directory creation ────────────────────────────────────────────────────


def test_creates_parent_directories(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"normalized_articles": [_article()]})
    out = tmp_path / "deep" / "nested" / "dir" / "articles.jsonl"

    store_articles_task(ctx, {"output_path": str(out)})

    assert out.is_file()


# ── append behavior ──────────────────────────────────────────────────────


def test_appends_new_records(tmp_path: Path) -> None:
    out = tmp_path / "articles.jsonl"
    out.write_text(
        json.dumps(_article(title="Existing", url="https://existing.com")) + "\n",
        encoding="utf-8",
    )

    ctx = _ctx(tmp_path, {
        "normalized_articles": [_article(title="New", url="https://new.com")],
    })

    store_articles_task(ctx, {"output_path": str(out)})

    records = _read_jsonl(out)
    assert len(records) == 2
    assert records[0]["title"] == "Existing"
    assert records[1]["title"] == "New"


# ── duplicate detection ──────────────────────────────────────────────────


def test_skips_duplicate_url_records(tmp_path: Path) -> None:
    out = tmp_path / "articles.jsonl"
    out.write_text(
        json.dumps(_article(title="Old", url="https://same.com")) + "\n",
        encoding="utf-8",
    )

    ctx = _ctx(tmp_path, {
        "normalized_articles": [
            _article(title="Dup", url="https://same.com"),
            _article(title="Fresh", url="https://fresh.com"),
        ],
    })

    store_articles_task(ctx, {"output_path": str(out)})

    records = _read_jsonl(out)
    assert len(records) == 2
    assert records[0]["title"] == "Old"
    assert records[1]["title"] == "Fresh"

    summary = ctx.state["stored_articles_summary"]
    assert summary["skipped_duplicates"] == 1


def test_title_domain_fallback_duplicate_detection(tmp_path: Path) -> None:
    out = tmp_path / "articles.jsonl"
    out.write_text(
        json.dumps(_article(title="Same Title", url="", domain="a.com")) + "\n",
        encoding="utf-8",
    )

    ctx = _ctx(tmp_path, {
        "normalized_articles": [
            _article(title="Same Title", url="", domain="a.com"),
            _article(title="Different", url="", domain="b.com"),
        ],
    })

    store_articles_task(ctx, {"output_path": str(out)})

    records = _read_jsonl(out)
    assert len(records) == 2
    assert records[0]["title"] == "Same Title"
    assert records[1]["title"] == "Different"

    summary = ctx.state["stored_articles_summary"]
    assert summary["skipped_duplicates"] == 1


# ── unkeyed records ──────────────────────────────────────────────────────


def test_unkeyed_records_appended_and_counted(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {
        "normalized_articles": [
            _article(title="Normal", url="https://a.com"),
            {"provider": "gdelt", "title": "", "url": "", "domain": ""},
            {"provider": "gdelt", "title": "", "url": "", "domain": ""},
        ],
    })
    out = tmp_path / "articles.jsonl"

    store_articles_task(ctx, {"output_path": str(out)})

    records = _read_jsonl(out)
    assert len(records) == 3

    summary = ctx.state["stored_articles_summary"]
    assert summary["unkeyed_records"] == 2


# ── order preservation ───────────────────────────────────────────────────


def test_preserves_output_order(tmp_path: Path) -> None:
    articles = [
        _article(title="C", url="https://c.com"),
        _article(title="A", url="https://a.com"),
        _article(title="B", url="https://b.com"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})
    out = tmp_path / "articles.jsonl"

    store_articles_task(ctx, {"output_path": str(out)})

    records = _read_jsonl(out)
    titles = [r["title"] for r in records]
    assert titles == ["C", "A", "B"]


# ── artifact written ─────────────────────────────────────────────────────


def test_store_summary_artifact_written(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"normalized_articles": [_article()]})
    out = tmp_path / "articles.jsonl"

    store_articles_task(ctx, {"output_path": str(out)})

    summary_path = ctx.artifacts_dir / "store_summary.json"
    assert summary_path.is_file()


# ── summary correctness ──────────────────────────────────────────────────


def test_summary_values_correct(tmp_path: Path) -> None:
    out = tmp_path / "articles.jsonl"
    out.write_text(
        json.dumps(_article(title="Existing", url="https://existing.com")) + "\n",
        encoding="utf-8",
    )

    ctx = _ctx(tmp_path, {
        "deduped_articles": [
            _article(title="New1", url="https://new1.com"),
            _article(title="Dup", url="https://existing.com"),
            _article(title="New2", url="https://new2.com"),
            {"provider": "gdelt", "title": "", "url": "", "domain": ""},
        ],
    })

    store_articles_task(ctx, {"output_path": str(out)})

    summary = json.loads(
        (ctx.artifacts_dir / "store_summary.json").read_text(encoding="utf-8")
    )

    assert summary["input_articles"] == 4
    assert summary["existing_records"] == 1
    assert summary["appended_records"] == 3
    assert summary["skipped_duplicates"] == 1
    assert summary["unkeyed_records"] == 1
    assert summary["output_path"] == str(out)
    assert summary["input_state_used"] == "deduped_articles"


# ── default output_path ──────────────────────────────────────────────────


def test_default_output_path_used(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"normalized_articles": [_article()]})

    import os
    original = os.getcwd()
    os.chdir(tmp_path)
    try:
        store_articles_task(ctx, {})
    finally:
        os.chdir(original)

    assert (tmp_path / "data" / "processed" / "articles.jsonl").is_file()

"""Tests for the dedupe_articles transform task."""

import json
from pathlib import Path

import pytest

from pipeline_core.engine.context import RunContext
from news_data.task.dedupe_articles import dedupe_articles_task


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


# ── input state selection ─────────────────────────────────────────────────


def test_prefers_filtered_over_normalized(tmp_path: Path) -> None:
    filtered = [_article(title="Filtered")]
    normalized = [_article(title="Normalized")]
    ctx = _ctx(tmp_path, {
        "filtered_articles": filtered,
        "normalized_articles": normalized,
    })

    dedupe_articles_task(ctx, {})

    assert ctx.state["deduped_articles"][0]["title"] == "Filtered"


def test_falls_back_to_normalized(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"normalized_articles": [_article(title="Norm")]})

    dedupe_articles_task(ctx, {})

    assert ctx.state["deduped_articles"][0]["title"] == "Norm"


def test_raises_when_no_input_state(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    with pytest.raises(ValueError, match="filtered_articles.*normalized_articles"):
        dedupe_articles_task(ctx, {})


# ── URL-based dedupe ──────────────────────────────────────────────────────


def test_url_dedupe_removes_duplicates(tmp_path: Path) -> None:
    articles = [
        _article(title="First", url="https://a.com"),
        _article(title="Second", url="https://a.com"),
        _article(title="Third", url="https://b.com"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"by": ["url"]})

    deduped = ctx.state["deduped_articles"]
    assert len(deduped) == 2
    assert deduped[0]["title"] == "First"
    assert deduped[1]["title"] == "Third"


# ── title+domain fallback ────────────────────────────────────────────────


def test_title_domain_fallback_when_url_missing(tmp_path: Path) -> None:
    articles = [
        _article(title="Same Title", url="", domain="a.com"),
        _article(title="Same Title", url="", domain="a.com"),
        _article(title="Different", url="", domain="a.com"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"by": ["url"]})

    deduped = ctx.state["deduped_articles"]
    assert len(deduped) == 2
    assert deduped[0]["title"] == "Same Title"
    assert deduped[1]["title"] == "Different"


# ── custom `by` fields ────────────────────────────────────────────────────


def test_custom_by_fields(tmp_path: Path) -> None:
    articles = [
        _article(title="AI News", domain="cnn.com", url="https://1"),
        _article(title="AI News", domain="cnn.com", url="https://2"),
        _article(title="AI News", domain="bbc.com", url="https://3"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"by": ["title", "domain"]})

    deduped = ctx.state["deduped_articles"]
    assert len(deduped) == 2
    assert deduped[0]["url"] == "https://1"
    assert deduped[1]["url"] == "https://3"


# ── order preservation ────────────────────────────────────────────────────


def test_preserves_original_order(tmp_path: Path) -> None:
    articles = [
        _article(title="C", url="https://c"),
        _article(title="A", url="https://a"),
        _article(title="B", url="https://b"),
        _article(title="A dup", url="https://a"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"by": ["url"]})

    titles = [a["title"] for a in ctx.state["deduped_articles"]]
    assert titles == ["C", "A", "B"]


# ── unkeyed articles ─────────────────────────────────────────────────────


def test_unkeyed_articles_kept_and_counted(tmp_path: Path) -> None:
    articles = [
        _article(title="Normal", url="https://a"),
        {"provider": "gdelt", "title": "", "url": "", "domain": ""},
        {"provider": "gdelt", "title": "", "url": "", "domain": ""},
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"by": ["url"]})

    deduped = ctx.state["deduped_articles"]
    assert len(deduped) == 3

    summary_path = ctx.artifacts_dir / "dedupe_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["unkeyed_articles"] == 2


# ── artifacts ─────────────────────────────────────────────────────────────


def test_artifacts_written(tmp_path: Path) -> None:
    articles = [_article(), _article(url="https://other.com")]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {})

    assert (ctx.artifacts_dir / "deduped_articles.jsonl").is_file()
    assert (ctx.artifacts_dir / "dedupe_summary.json").is_file()


# ── summary correctness ──────────────────────────────────────────────────


def test_summary_values_correct(tmp_path: Path) -> None:
    articles = [
        _article(url="https://a"),
        _article(url="https://a"),
        _article(url="https://b"),
    ]
    ctx = _ctx(tmp_path, {"filtered_articles": articles})

    dedupe_articles_task(ctx, {"by": ["url"]})

    summary = json.loads(
        (ctx.artifacts_dir / "dedupe_summary.json").read_text(encoding="utf-8")
    )

    assert summary["input_articles"] == 3
    assert summary["deduped_articles"] == 2
    assert summary["duplicates_removed"] == 1
    assert summary["unkeyed_articles"] == 0
    assert summary["dedupe_fields_used"] == ["url"]
    assert summary["input_state_used"] == "filtered_articles"

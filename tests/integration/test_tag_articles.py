"""Tests for the tag_articles transform task."""

import json
from pathlib import Path

import pytest

from kinetic.core.pipeline.context import RunContext
from kinetic.processing.news.tasks.tag_articles import tag_articles_task

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
    query: str = "",
) -> dict:
    return {
        "provider": "gdelt",
        "title": title,
        "url": url,
        "domain": domain,
        "query": query,
        "language": "English",
        "source_country": "United States",
    }


def _summary(ctx: RunContext) -> dict:
    path = ctx.artifacts_dir / "tag_summary.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# ── input state selection ─────────────────────────────────────────────────


def test_prefers_deduped_over_filtered(tmp_path: Path) -> None:
    deduped = [_article(title="Deduped")]
    filtered = [_article(title="Filtered")]
    ctx = _ctx(
        tmp_path,
        {
            "deduped_articles": deduped,
            "filtered_articles": filtered,
        },
    )

    tag_articles_task(ctx, {})

    assert ctx.state["tagged_articles"][0]["title"] == "Deduped"
    assert _summary(ctx)["input_state_used"] == "deduped_articles"


def test_falls_back_to_filtered(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"filtered_articles": [_article(title="Filtered")]})

    tag_articles_task(ctx, {})

    assert ctx.state["tagged_articles"][0]["title"] == "Filtered"
    assert _summary(ctx)["input_state_used"] == "filtered_articles"


def test_falls_back_to_normalized(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"normalized_articles": [_article(title="Norm")]})

    tag_articles_task(ctx, {})

    assert ctx.state["tagged_articles"][0]["title"] == "Norm"
    assert _summary(ctx)["input_state_used"] == "normalized_articles"


def test_raises_when_no_input_state(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    with pytest.raises(
        ValueError, match="deduped_articles.*filtered_articles.*normalized_articles"
    ):
        tag_articles_task(ctx, {})


# ── default rules ────────────────────────────────────────────────────────


def test_default_rules_tag_ai_article(tmp_path: Path) -> None:
    articles = [
        _article(title="OpenAI releases new large language models for enterprise"),
    ]
    ctx = _ctx(tmp_path, {"deduped_articles": articles})

    tag_articles_task(ctx, {})

    tagged = ctx.state["tagged_articles"][0]
    assert "ai" in tagged["topics"]
    assert tagged["primary_topic"] == "ai"
    assert "OpenAI" in tagged["topic_matches"]["ai"]


def test_default_rules_tag_inflation_article(tmp_path: Path) -> None:
    articles = [
        _article(title="Federal Reserve holds interest rates steady amid inflation concerns"),
    ]
    ctx = _ctx(tmp_path, {"deduped_articles": articles})

    tag_articles_task(ctx, {})

    tagged = ctx.state["tagged_articles"][0]
    assert "inflation_rates" in tagged["topics"]


# ── custom rules ─────────────────────────────────────────────────────────


def test_custom_rules_work(tmp_path: Path) -> None:
    articles = [_article(title="Tesla stock surges on EV demand")]
    ctx = _ctx(tmp_path, {"deduped_articles": articles})

    tag_articles_task(
        ctx,
        {
            "rules": {
                "ev_stocks": ["Tesla", "EV demand"],
                "crypto": ["Bitcoin", "Ethereum"],
            },
        },
    )

    tagged = ctx.state["tagged_articles"][0]
    assert tagged["topics"] == ["ev_stocks"]
    assert tagged["primary_topic"] == "ev_stocks"
    assert "Tesla" in tagged["topic_matches"]["ev_stocks"]


# ── case insensitivity ───────────────────────────────────────────────────


def test_case_insensitive_matching(tmp_path: Path) -> None:
    articles = [_article(title="FEDERAL RESERVE raises rates")]
    ctx = _ctx(tmp_path, {"deduped_articles": articles})

    tag_articles_task(
        ctx,
        {
            "rules": {"rates": ["Federal Reserve"]},
        },
    )

    tagged = ctx.state["tagged_articles"][0]
    assert "rates" in tagged["topics"]


# ── multi-word phrase matching ───────────────────────────────────────────


def test_multi_word_phrase_matching(tmp_path: Path) -> None:
    articles = [_article(title="Machine learning drives semiconductor demand")]
    ctx = _ctx(tmp_path, {"deduped_articles": articles})

    tag_articles_task(
        ctx,
        {
            "rules": {
                "ai": ["machine learning"],
                "chips": ["semiconductor"],
            },
        },
    )

    tagged = ctx.state["tagged_articles"][0]
    assert sorted(tagged["topics"]) == ["ai", "chips"]


# ── multiple topics ──────────────────────────────────────────────────────


def test_multiple_topics_assigned(tmp_path: Path) -> None:
    articles = [
        _article(title="Federal Reserve policy impacts gold prices and bond yields"),
    ]
    ctx = _ctx(tmp_path, {"deduped_articles": articles})

    tag_articles_task(ctx, {})

    tagged = ctx.state["tagged_articles"][0]
    assert "inflation_rates" in tagged["topics"]
    assert "bonds_yields" in tagged["topics"]
    assert "commodities" in tagged["topics"]
    assert len(tagged["topics"]) >= 3


# ── no match ─────────────────────────────────────────────────────────────


def test_no_match_produces_empty_topics(tmp_path: Path) -> None:
    articles = [_article(title="Local cat show draws record attendance")]
    ctx = _ctx(tmp_path, {"deduped_articles": articles})

    tag_articles_task(
        ctx,
        {
            "rules": {"finance": ["stock market"]},
        },
    )

    tagged = ctx.state["tagged_articles"][0]
    assert tagged["topics"] == []
    assert tagged["topic_matches"] == {}
    assert tagged["primary_topic"] is None


# ── topic_matches structure ──────────────────────────────────────────────


def test_topic_matches_records_matched_terms(tmp_path: Path) -> None:
    articles = [
        _article(title="Nvidia GPU shortage hits AI chip supply"),
    ]
    ctx = _ctx(tmp_path, {"deduped_articles": articles})

    tag_articles_task(
        ctx,
        {
            "rules": {
                "semiconductors": ["Nvidia", "GPU", "chip supply"],
            },
        },
    )

    tagged = ctx.state["tagged_articles"][0]
    matches = tagged["topic_matches"]["semiconductors"]
    assert "Nvidia" in matches
    assert "GPU" in matches
    assert "chip supply" in matches


# ── original fields preserved ────────────────────────────────────────────


def test_original_fields_preserved(tmp_path: Path) -> None:
    articles = [_article(title="Test", url="https://x.com", domain="x.com")]
    ctx = _ctx(tmp_path, {"deduped_articles": articles})

    tag_articles_task(ctx, {"rules": {}})

    tagged = ctx.state["tagged_articles"][0]
    assert tagged["title"] == "Test"
    assert tagged["url"] == "https://x.com"
    assert tagged["domain"] == "x.com"
    assert tagged["provider"] == "gdelt"


# ── order preservation ───────────────────────────────────────────────────


def test_order_preserved(tmp_path: Path) -> None:
    articles = [
        _article(title="C story"),
        _article(title="A story"),
        _article(title="B story"),
    ]
    ctx = _ctx(tmp_path, {"deduped_articles": articles})

    tag_articles_task(ctx, {"rules": {}})

    titles = [a["title"] for a in ctx.state["tagged_articles"]]
    assert titles == ["C story", "A story", "B story"]


# ── summary artifact ─────────────────────────────────────────────────────


def test_summary_values_correct(tmp_path: Path) -> None:
    articles = [
        _article(title="Nvidia launches new GPU"),
        _article(title="Bitcoin hits all-time high"),
        _article(title="Local cat show recap"),
    ]
    ctx = _ctx(tmp_path, {"deduped_articles": articles})

    tag_articles_task(
        ctx,
        {
            "rules": {
                "semiconductors": ["Nvidia", "GPU"],
                "crypto": ["Bitcoin"],
            },
        },
    )

    summary = _summary(ctx)
    assert summary["input_articles"] == 3
    assert summary["tagged_articles"] == 2
    assert summary["untagged_articles"] == 1
    assert sorted(summary["topics_found"]) == ["crypto", "semiconductors"]
    assert summary["topic_counts"]["semiconductors"] == 1
    assert summary["topic_counts"]["crypto"] == 1
    assert summary["input_state_used"] == "deduped_articles"


# ── artifact written ─────────────────────────────────────────────────────


def test_tagged_articles_artifact_written(tmp_path: Path) -> None:
    articles = [_article(title="Test")]
    ctx = _ctx(tmp_path, {"deduped_articles": articles})

    tag_articles_task(ctx, {"rules": {}})

    path = ctx.artifacts_dir / "tagged_articles.jsonl"
    assert path.is_file()
    records = _read_jsonl(path)
    assert len(records) == 1
    assert records[0]["title"] == "Test"


def test_tag_summary_artifact_written(tmp_path: Path) -> None:
    articles = [_article(title="Test")]
    ctx = _ctx(tmp_path, {"deduped_articles": articles})

    tag_articles_task(ctx, {"rules": {}})

    assert (ctx.artifacts_dir / "tag_summary.json").is_file()

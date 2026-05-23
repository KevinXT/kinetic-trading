"""Tests for the aggregate_article_features transform task."""

import json
from pathlib import Path

import pytest
from news_data.task.aggregate_article_features import aggregate_article_features_task
from pipeline_core.engine.context import RunContext

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
    source_country: str = "United States",
    published_at: str | None = "2026-05-20T14:00:00Z",
    topics: list[str] | None = None,
    primary_topic: str | None = None,
    query: str = "",
) -> dict:
    a: dict = {
        "provider": "gdelt",
        "title": title,
        "url": url,
        "domain": domain,
        "source_country": source_country,
        "language": "English",
        "query": query,
    }
    if published_at is not None:
        a["published_at"] = published_at
    if topics is not None:
        a["topics"] = topics
    if primary_topic is not None:
        a["primary_topic"] = primary_topic
    return a


def _summary(ctx: RunContext) -> dict:
    path = ctx.artifacts_dir / "article_features_summary.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(ctx: RunContext) -> list[dict]:
    return ctx.state["article_features_daily"]


def _read_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# ── input state selection ─────────────────────────────────────────────────


def test_prefers_tagged_over_deduped(tmp_path: Path) -> None:
    tagged = [_article(title="Tagged", topics=["ai"], primary_topic="ai")]
    deduped = [_article(title="Deduped")]
    ctx = _ctx(tmp_path, {"tagged_articles": tagged, "deduped_articles": deduped})

    aggregate_article_features_task(ctx, {})

    assert _summary(ctx)["input_state_used"] == "tagged_articles"


def test_falls_back_to_deduped(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"deduped_articles": [_article()]})

    aggregate_article_features_task(ctx, {})

    assert _summary(ctx)["input_state_used"] == "deduped_articles"


def test_falls_back_to_filtered(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"filtered_articles": [_article()]})

    aggregate_article_features_task(ctx, {})

    assert _summary(ctx)["input_state_used"] == "filtered_articles"


def test_falls_back_to_normalized(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"normalized_articles": [_article()]})

    aggregate_article_features_task(ctx, {})

    assert _summary(ctx)["input_state_used"] == "normalized_articles"


def test_raises_when_no_input_state(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    with pytest.raises(ValueError, match="tagged_articles.*deduped_articles"):
        aggregate_article_features_task(ctx, {})


# ── date/topic grouping ──────────────────────────────────────────────────


def test_groups_by_date_and_topic(tmp_path: Path) -> None:
    articles = [
        _article(
            published_at="2026-05-20T10:00:00Z", topics=["ai"], primary_topic="ai"
        ),
        _article(
            published_at="2026-05-20T12:00:00Z", topics=["ai"], primary_topic="ai"
        ),
        _article(
            published_at="2026-05-21T08:00:00Z",
            topics=["energy"],
            primary_topic="energy",
        ),
    ]
    ctx = _ctx(tmp_path, {"tagged_articles": articles})

    aggregate_article_features_task(ctx, {})

    rows = _rows(ctx)
    assert len(rows) == 2

    ai_row = next(r for r in rows if r["topic"] == "ai")
    assert ai_row["date"] == "2026-05-20"
    assert ai_row["article_count"] == 2

    energy_row = next(r for r in rows if r["topic"] == "energy")
    assert energy_row["date"] == "2026-05-21"
    assert energy_row["article_count"] == 1


def test_multi_topic_article_contributes_to_multiple_groups(tmp_path: Path) -> None:
    articles = [
        _article(
            published_at="2026-05-20T10:00:00Z",
            topics=["ai", "semiconductors"],
            primary_topic="ai",
        ),
    ]
    ctx = _ctx(tmp_path, {"tagged_articles": articles})

    aggregate_article_features_task(ctx, {})

    rows = _rows(ctx)
    assert len(rows) == 2
    topics = {r["topic"] for r in rows}
    assert topics == {"ai", "semiconductors"}
    for r in rows:
        assert r["article_count"] == 1


def test_untagged_article_goes_to_untagged(tmp_path: Path) -> None:
    articles = [
        _article(published_at="2026-05-20T10:00:00Z", topics=[], primary_topic=None),
    ]
    ctx = _ctx(tmp_path, {"tagged_articles": articles})

    aggregate_article_features_task(ctx, {})

    rows = _rows(ctx)
    assert len(rows) == 1
    assert rows[0]["topic"] == "untagged"
    assert rows[0]["untagged_count"] == 1


def test_missing_published_at_goes_to_unknown_date(tmp_path: Path) -> None:
    articles = [
        _article(published_at=None, topics=["ai"], primary_topic="ai"),
    ]
    ctx = _ctx(tmp_path, {"tagged_articles": articles})

    aggregate_article_features_task(ctx, {})

    rows = _rows(ctx)
    assert rows[0]["date"] == "unknown"


def test_invalid_published_at_goes_to_unknown_date(tmp_path: Path) -> None:
    a = _article(topics=["ai"], primary_topic="ai")
    a["published_at"] = 12345
    ctx = _ctx(tmp_path, {"tagged_articles": [a]})

    aggregate_article_features_task(ctx, {})

    rows = _rows(ctx)
    assert rows[0]["date"] == "unknown"


# ── domain / country metrics ─────────────────────────────────────────────


def test_unique_domains_count(tmp_path: Path) -> None:
    articles = [
        _article(domain="a.com", topics=["ai"], primary_topic="ai"),
        _article(domain="b.com", topics=["ai"], primary_topic="ai"),
        _article(domain="a.com", topics=["ai"], primary_topic="ai"),
    ]
    ctx = _ctx(tmp_path, {"tagged_articles": articles})

    aggregate_article_features_task(ctx, {})

    row = _rows(ctx)[0]
    assert row["unique_domains"] == 2
    assert sorted(row["domains"]) == ["a.com", "b.com"]


def test_unique_source_countries_count(tmp_path: Path) -> None:
    articles = [
        _article(source_country="United States", topics=["ai"], primary_topic="ai"),
        _article(source_country="United Kingdom", topics=["ai"], primary_topic="ai"),
        _article(source_country="United States", topics=["ai"], primary_topic="ai"),
    ]
    ctx = _ctx(tmp_path, {"tagged_articles": articles})

    aggregate_article_features_task(ctx, {})

    row = _rows(ctx)[0]
    assert row["unique_source_countries"] == 2
    assert sorted(row["source_countries"]) == ["United Kingdom", "United States"]


def test_domains_sorted_deterministically(tmp_path: Path) -> None:
    articles = [
        _article(domain="z.com", topics=["ai"], primary_topic="ai"),
        _article(domain="a.com", topics=["ai"], primary_topic="ai"),
        _article(domain="m.com", topics=["ai"], primary_topic="ai"),
    ]
    ctx = _ctx(tmp_path, {"tagged_articles": articles})

    aggregate_article_features_task(ctx, {})

    row = _rows(ctx)[0]
    assert row["domains"] == ["a.com", "m.com", "z.com"]


def test_source_countries_sorted_deterministically(tmp_path: Path) -> None:
    articles = [
        _article(source_country="Germany", topics=["ai"], primary_topic="ai"),
        _article(source_country="Australia", topics=["ai"], primary_topic="ai"),
    ]
    ctx = _ctx(tmp_path, {"tagged_articles": articles})

    aggregate_article_features_task(ctx, {})

    row = _rows(ctx)[0]
    assert row["source_countries"] == ["Australia", "Germany"]


# ── timestamps ────────────────────────────────────────────────────────────


def test_earliest_latest_published_at(tmp_path: Path) -> None:
    articles = [
        _article(
            published_at="2026-05-20T14:00:00Z", topics=["ai"], primary_topic="ai"
        ),
        _article(
            published_at="2026-05-20T08:00:00Z", topics=["ai"], primary_topic="ai"
        ),
        _article(
            published_at="2026-05-20T20:00:00Z", topics=["ai"], primary_topic="ai"
        ),
    ]
    ctx = _ctx(tmp_path, {"tagged_articles": articles})

    aggregate_article_features_task(ctx, {})

    row = _rows(ctx)[0]
    assert row["earliest_published_at"] == "2026-05-20T08:00:00Z"
    assert row["latest_published_at"] == "2026-05-20T20:00:00Z"


# ── summary artifact ─────────────────────────────────────────────────────


def test_summary_values_correct(tmp_path: Path) -> None:
    articles = [
        _article(
            published_at="2026-05-20T10:00:00Z", topics=["ai"], primary_topic="ai"
        ),
        _article(
            published_at="2026-05-21T10:00:00Z",
            topics=["energy"],
            primary_topic="energy",
        ),
        _article(published_at="2026-05-20T12:00:00Z", topics=[], primary_topic=None),
    ]
    ctx = _ctx(tmp_path, {"tagged_articles": articles})

    aggregate_article_features_task(ctx, {})

    s = _summary(ctx)
    assert s["input_articles"] == 3
    assert s["feature_rows"] == 3
    assert sorted(s["dates"]) == ["2026-05-20", "2026-05-21"]
    assert sorted(s["topics"]) == ["ai", "energy", "untagged"]
    assert s["input_state_used"] == "tagged_articles"
    assert s["untagged_articles"] == 1


# ── artifact written ─────────────────────────────────────────────────────


def test_jsonl_artifact_written(tmp_path: Path) -> None:
    articles = [
        _article(topics=["ai"], primary_topic="ai"),
    ]
    ctx = _ctx(tmp_path, {"tagged_articles": articles})

    aggregate_article_features_task(ctx, {})

    path = ctx.artifacts_dir / "article_features_daily.jsonl"
    assert path.is_file()
    records = _read_jsonl(path)
    assert len(records) == 1
    assert records[0]["topic"] == "ai"


def test_summary_artifact_written(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path, {"tagged_articles": [_article(topics=["ai"], primary_topic="ai")]}
    )

    aggregate_article_features_task(ctx, {})

    assert (ctx.artifacts_dir / "article_features_summary.json").is_file()


# ── invalid group_by ─────────────────────────────────────────────────────


def test_invalid_group_by_raises(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"tagged_articles": [_article()]})

    with pytest.raises(ValueError, match="group_by"):
        aggregate_article_features_task(ctx, {"group_by": ["date"]})


def test_extra_group_by_raises(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"tagged_articles": [_article()]})

    with pytest.raises(ValueError, match="group_by"):
        aggregate_article_features_task(ctx, {"group_by": ["date", "topic", "domain"]})


# ── duplicate group amplification ────────────────────────────────────────


def test_amplification_metrics_present_when_dup_groups_exist(tmp_path: Path) -> None:
    articles = [
        _article(
            title="Fed raises rates",
            url="https://a.com/1",
            topics=["inflation_rates"],
            primary_topic="inflation_rates",
        ),
    ]
    dup_groups = [
        {
            "canonical_title": "Fed raises rates",
            "canonical_url": "https://a.com/1",
            "duplicate_count": 3,
            "total_seen": 4,
        },
    ]
    ctx = _ctx(tmp_path, {"tagged_articles": articles, "duplicate_groups": dup_groups})

    aggregate_article_features_task(ctx, {})

    row = _rows(ctx)[0]
    assert row["duplicate_groups"] == 1
    assert row["duplicate_articles_removed"] == 3
    assert row["max_group_total_seen"] == 4


def test_amplification_metrics_absent_when_no_dup_groups_state(tmp_path: Path) -> None:
    articles = [
        _article(topics=["ai"], primary_topic="ai"),
    ]
    ctx = _ctx(tmp_path, {"tagged_articles": articles})

    aggregate_article_features_task(ctx, {})

    row = _rows(ctx)[0]
    assert "duplicate_groups" not in row
    assert "duplicate_articles_removed" not in row


def test_amplification_zero_when_no_match(tmp_path: Path) -> None:
    articles = [
        _article(
            title="Unrelated", url="https://b.com/99", topics=["ai"], primary_topic="ai"
        ),
    ]
    dup_groups = [
        {
            "canonical_title": "Something else",
            "canonical_url": "https://z.com/1",
            "duplicate_count": 5,
            "total_seen": 6,
        },
    ]
    ctx = _ctx(tmp_path, {"tagged_articles": articles, "duplicate_groups": dup_groups})

    aggregate_article_features_task(ctx, {})

    row = _rows(ctx)[0]
    assert row["duplicate_groups"] == 0
    assert row["duplicate_articles_removed"] == 0
    assert row["max_group_total_seen"] == 0


# ── deterministic output order ───────────────────────────────────────────


def test_output_rows_sorted_deterministically(tmp_path: Path) -> None:
    articles = [
        _article(
            published_at="2026-05-21T10:00:00Z",
            topics=["energy"],
            primary_topic="energy",
        ),
        _article(
            published_at="2026-05-20T10:00:00Z", topics=["ai"], primary_topic="ai"
        ),
        _article(
            published_at="2026-05-20T10:00:00Z",
            topics=["energy"],
            primary_topic="energy",
        ),
    ]
    ctx = _ctx(tmp_path, {"tagged_articles": articles})

    aggregate_article_features_task(ctx, {})

    rows = _rows(ctx)
    keys = [(r["date"], r["topic"]) for r in rows]
    assert keys == sorted(keys)


# ── primary_topic_count ──────────────────────────────────────────────────


def test_primary_topic_count(tmp_path: Path) -> None:
    articles = [
        _article(topics=["ai", "semiconductors"], primary_topic="ai"),
        _article(topics=["ai", "semiconductors"], primary_topic="semiconductors"),
        _article(topics=["ai"], primary_topic="ai"),
    ]
    ctx = _ctx(tmp_path, {"tagged_articles": articles})

    aggregate_article_features_task(ctx, {})

    rows = _rows(ctx)
    ai_row = next(r for r in rows if r["topic"] == "ai")
    semi_row = next(r for r in rows if r["topic"] == "semiconductors")
    assert ai_row["primary_topic_count"] == 2
    assert semi_row["primary_topic_count"] == 1

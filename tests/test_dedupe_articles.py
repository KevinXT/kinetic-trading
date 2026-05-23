"""Tests for the dedupe_articles transform task."""

import json
from pathlib import Path

import pytest
from news_data.task.dedupe_articles import dedupe_articles_task
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
) -> dict:
    return {
        "provider": "gdelt",
        "title": title,
        "url": url,
        "domain": domain,
        "language": "English",
        "source_country": "United States",
    }


def _summary(ctx: RunContext) -> dict:
    path = ctx.artifacts_dir / "dedupe_summary.json"
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


def test_prefers_filtered_over_normalized(tmp_path: Path) -> None:
    filtered = [_article(title="Filtered")]
    normalized = [_article(title="Normalized")]
    ctx = _ctx(
        tmp_path,
        {
            "filtered_articles": filtered,
            "normalized_articles": normalized,
        },
    )

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


# ── url strategy ──────────────────────────────────────────────────────────


def test_url_strategy_removes_duplicate_urls(tmp_path: Path) -> None:
    articles = [
        _article(title="First", url="https://a.com"),
        _article(title="Second", url="https://a.com"),
        _article(title="Third", url="https://b.com"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "url"})

    deduped = ctx.state["deduped_articles"]
    assert len(deduped) == 2
    assert deduped[0]["title"] == "First"
    assert deduped[1]["title"] == "Third"


def test_url_strategy_keeps_same_title_different_urls(tmp_path: Path) -> None:
    articles = [
        _article(title="Same Title", url="https://a.com/1"),
        _article(title="Same Title", url="https://b.com/1"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "url"})

    assert len(ctx.state["deduped_articles"]) == 2


# ── title strategy ────────────────────────────────────────────────────────


def test_title_strategy_removes_syndicated_duplicates(tmp_path: Path) -> None:
    articles = [
        _article(
            title="US manufacturers boost inventories as Iran war lifts costs",
            url="https://reuters.com/article/123",
            domain="reuters.com",
        ),
        _article(
            title="US manufacturers boost inventories as Iran war lifts costs",
            url="https://yahoo.com/news/456",
            domain="yahoo.com",
        ),
        _article(
            title="US manufacturers boost inventories as Iran war lifts costs",
            url="https://msn.com/en-us/789",
            domain="msn.com",
        ),
        _article(
            title="Tech stocks rally on AI optimism",
            url="https://cnbc.com/article/abc",
            domain="cnbc.com",
        ),
    ]
    ctx = _ctx(tmp_path, {"filtered_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "title"})

    deduped = ctx.state["deduped_articles"]
    assert len(deduped) == 2
    assert deduped[0]["domain"] == "reuters.com"
    assert deduped[1]["domain"] == "cnbc.com"


def test_title_strategy_is_default(tmp_path: Path) -> None:
    articles = [
        _article(title="Same", url="https://a.com"),
        _article(title="Same", url="https://b.com"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {})

    assert len(ctx.state["deduped_articles"]) == 1


def test_title_strategy_case_insensitive(tmp_path: Path) -> None:
    articles = [
        _article(title="Fed Raises Rates", url="https://a.com"),
        _article(title="fed raises rates", url="https://b.com"),
        _article(title="FED RAISES RATES", url="https://c.com"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "title"})

    deduped = ctx.state["deduped_articles"]
    assert len(deduped) == 1
    assert deduped[0]["url"] == "https://a.com"


def test_title_strategy_strips_whitespace(tmp_path: Path) -> None:
    articles = [
        _article(title="  Oil prices surge  ", url="https://a.com"),
        _article(title="Oil prices surge", url="https://b.com"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "title"})

    assert len(ctx.state["deduped_articles"]) == 1


# ── title_domain strategy ────────────────────────────────────────────────


def test_title_domain_strategy_allows_same_title_different_domains(
    tmp_path: Path,
) -> None:
    articles = [
        _article(title="Breaking News", url="https://1", domain="cnn.com"),
        _article(title="Breaking News", url="https://2", domain="bbc.com"),
        _article(title="Breaking News", url="https://3", domain="cnn.com"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "title_domain"})

    deduped = ctx.state["deduped_articles"]
    assert len(deduped) == 2
    domains = [a["domain"] for a in deduped]
    assert domains == ["cnn.com", "bbc.com"]


def test_title_domain_strategy_falls_back_to_title_when_domain_empty(
    tmp_path: Path,
) -> None:
    articles = [
        _article(title="Same", url="https://1", domain=""),
        _article(title="Same", url="https://2", domain=""),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "title_domain"})

    assert len(ctx.state["deduped_articles"]) == 1


# ── order preservation ────────────────────────────────────────────────────


def test_preserves_original_order(tmp_path: Path) -> None:
    articles = [
        _article(title="C", url="https://c"),
        _article(title="A", url="https://a"),
        _article(title="B", url="https://b"),
        _article(title="A", url="https://a2"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "title"})

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

    dedupe_articles_task(ctx, {"dedupe_strategy": "title"})

    deduped = ctx.state["deduped_articles"]
    assert len(deduped) == 3

    summary = _summary(ctx)
    assert summary["unkeyed_articles"] == 2


def test_url_strategy_unkeyed_when_url_empty(tmp_path: Path) -> None:
    articles = [
        _article(title="Has Title", url="", domain="x.com"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "url"})

    assert len(ctx.state["deduped_articles"]) == 1
    assert _summary(ctx)["unkeyed_articles"] == 1


# ── mixed duplicate / nonduplicate inputs ────────────────────────────────


def test_mixed_duplicates_and_unique(tmp_path: Path) -> None:
    articles = [
        _article(title="Unique Alpha", url="https://1"),
        _article(title="Duplicate", url="https://2", domain="a.com"),
        _article(title="Unique Beta", url="https://3"),
        _article(title="Duplicate", url="https://4", domain="b.com"),
        _article(title="Unique Gamma", url="https://5"),
        _article(title="Duplicate", url="https://6", domain="c.com"),
    ]
    ctx = _ctx(tmp_path, {"filtered_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "title"})

    deduped = ctx.state["deduped_articles"]
    assert len(deduped) == 4
    titles = [a["title"] for a in deduped]
    assert titles == ["Unique Alpha", "Duplicate", "Unique Beta", "Unique Gamma"]


# ── artifacts ─────────────────────────────────────────────────────────────


def test_artifacts_written(tmp_path: Path) -> None:
    articles = [_article(), _article(url="https://other.com")]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {})

    assert (ctx.artifacts_dir / "deduped_articles.jsonl").is_file()
    assert (ctx.artifacts_dir / "dedupe_summary.json").is_file()


# ── summary correctness ──────────────────────────────────────────────────


def test_summary_fields_for_title_strategy(tmp_path: Path) -> None:
    articles = [
        _article(title="Dup", url="https://a"),
        _article(title="Dup", url="https://b"),
        _article(title="Unique", url="https://c"),
    ]
    ctx = _ctx(tmp_path, {"filtered_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "title"})

    summary = _summary(ctx)
    assert summary["input_articles"] == 3
    assert summary["deduped_articles"] == 2
    assert summary["duplicates_removed"] == 1
    assert summary["unique_articles"] == 2
    assert summary["unkeyed_articles"] == 0
    assert summary["dedupe_strategy"] == "title"
    assert summary["input_state_used"] == "filtered_articles"
    assert summary["duplicate_groups"] == 1
    assert summary["max_duplicate_group_size"] == 2
    assert summary["total_duplicate_articles"] == 1


def test_summary_fields_for_url_strategy(tmp_path: Path) -> None:
    articles = [
        _article(url="https://a"),
        _article(url="https://a"),
        _article(url="https://b"),
    ]
    ctx = _ctx(tmp_path, {"filtered_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "url"})

    summary = _summary(ctx)
    assert summary["dedupe_strategy"] == "url"
    assert summary["duplicates_removed"] == 1
    assert summary["unique_articles"] == 2


def test_summary_with_unkeyed(tmp_path: Path) -> None:
    articles = [
        _article(title="OK", url="https://a"),
        {"provider": "gdelt", "title": "", "url": "", "domain": ""},
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "title"})

    summary = _summary(ctx)
    assert summary["unique_articles"] == 1
    assert summary["unkeyed_articles"] == 1
    assert summary["deduped_articles"] == 2


def test_summary_no_duplicates_zeros(tmp_path: Path) -> None:
    articles = [
        _article(title="A", url="https://a"),
        _article(title="B", url="https://b"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "title"})

    summary = _summary(ctx)
    assert summary["duplicate_groups"] == 0
    assert summary["max_duplicate_group_size"] == 0
    assert summary["total_duplicate_articles"] == 0


# ── duplicate group metadata ─────────────────────────────────────────────


def test_duplicate_groups_only_for_actual_duplicates(tmp_path: Path) -> None:
    articles = [
        _article(title="Unique A", url="https://1"),
        _article(title="Unique B", url="https://2"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "title"})

    assert ctx.state["duplicate_groups"] == []
    assert not (ctx.artifacts_dir / "duplicate_groups.jsonl").exists()


def test_duplicate_group_count_and_total_seen(tmp_path: Path) -> None:
    articles = [
        _article(title="Story", url="https://1", domain="a.com"),
        _article(title="Story", url="https://2", domain="b.com"),
        _article(title="Story", url="https://3", domain="c.com"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "title"})

    groups = ctx.state["duplicate_groups"]
    assert len(groups) == 1
    assert groups[0]["duplicate_count"] == 2
    assert groups[0]["total_seen"] == 3


def test_duplicate_group_domains_include_canonical(tmp_path: Path) -> None:
    articles = [
        _article(title="Story", url="https://1", domain="reuters.com"),
        _article(title="Story", url="https://2", domain="yahoo.com"),
        _article(title="Story", url="https://3", domain="msn.com"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "title"})

    group = ctx.state["duplicate_groups"][0]
    assert "reuters.com" in group["domains"]
    assert "yahoo.com" in group["domains"]
    assert "msn.com" in group["domains"]
    assert len(group["domains"]) == 3


def test_duplicate_group_urls_exclude_canonical(tmp_path: Path) -> None:
    articles = [
        _article(title="Story", url="https://canonical.com/1", domain="a.com"),
        _article(title="Story", url="https://dup.com/2", domain="b.com"),
        _article(title="Story", url="https://dup.com/3", domain="c.com"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "title"})

    group = ctx.state["duplicate_groups"][0]
    assert "https://canonical.com/1" not in group["duplicate_urls"]
    assert "https://dup.com/2" in group["duplicate_urls"]
    assert "https://dup.com/3" in group["duplicate_urls"]
    assert group["canonical_url"] == "https://canonical.com/1"


def test_duplicate_group_titles_captured(tmp_path: Path) -> None:
    articles = [
        _article(title="Story", url="https://1", domain="a.com"),
        _article(title="Story", url="https://2", domain="b.com"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "title"})

    group = ctx.state["duplicate_groups"][0]
    assert len(group["duplicate_titles"]) == 1
    assert group["duplicate_titles"][0] == "Story"


def test_duplicate_groups_artifact_written(tmp_path: Path) -> None:
    articles = [
        _article(title="Dup", url="https://1", domain="a.com"),
        _article(title="Dup", url="https://2", domain="b.com"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "title"})

    path = ctx.artifacts_dir / "duplicate_groups.jsonl"
    assert path.is_file()
    records = _read_jsonl(path)
    assert len(records) == 1
    assert records[0]["dedupe_strategy"] == "title"


def test_syndicated_title_strategy_full_group(tmp_path: Path) -> None:
    """End-to-end: syndicated article across 4 domains produces correct group."""
    articles = [
        _article(
            title="US manufacturers boost inventories",
            url="http://www.northkoreatimes.com/news/1",
            domain="northkoreatimes.com",
        ),
        _article(
            title="US manufacturers boost inventories",
            url="http://www.nigeriasun.com/news/2",
            domain="nigeriasun.com",
        ),
        _article(
            title="US manufacturers boost inventories",
            url="http://www.israelherald.com/news/3",
            domain="israelherald.com",
        ),
        _article(
            title="US manufacturers boost inventories",
            url="http://www.hongkongherald.com/news/4",
            domain="hongkongherald.com",
        ),
        _article(
            title="Other headline",
            url="http://www.reuters.com/5",
            domain="reuters.com",
        ),
    ]
    ctx = _ctx(tmp_path, {"filtered_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "title"})

    deduped = ctx.state["deduped_articles"]
    assert len(deduped) == 2

    groups = ctx.state["duplicate_groups"]
    assert len(groups) == 1

    g = groups[0]
    assert g["canonical_title"] == "US manufacturers boost inventories"
    assert g["canonical_url"] == "http://www.northkoreatimes.com/news/1"
    assert g["duplicate_count"] == 3
    assert g["total_seen"] == 4
    assert len(g["domains"]) == 4
    assert len(g["duplicate_urls"]) == 3
    assert g["dedupe_strategy"] == "title"

    summary = _summary(ctx)
    assert summary["duplicate_groups"] == 1
    assert summary["max_duplicate_group_size"] == 4
    assert summary["total_duplicate_articles"] == 3


def test_multiple_duplicate_groups(tmp_path: Path) -> None:
    articles = [
        _article(title="Group A", url="https://1", domain="x.com"),
        _article(title="Group A", url="https://2", domain="y.com"),
        _article(title="Group B", url="https://3", domain="p.com"),
        _article(title="Group B", url="https://4", domain="q.com"),
        _article(title="Group B", url="https://5", domain="r.com"),
        _article(title="Unique", url="https://6", domain="u.com"),
    ]
    ctx = _ctx(tmp_path, {"filtered_articles": articles})

    dedupe_articles_task(ctx, {"dedupe_strategy": "title"})

    groups = ctx.state["duplicate_groups"]
    assert len(groups) == 2

    summary = _summary(ctx)
    assert summary["duplicate_groups"] == 2
    assert summary["max_duplicate_group_size"] == 3
    assert summary["total_duplicate_articles"] == 3


# ── legacy `by` param backward compat ────────────────────────────────────


def test_legacy_by_url_maps_to_url_strategy(tmp_path: Path) -> None:
    articles = [
        _article(url="https://a"),
        _article(url="https://a"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"by": ["url"]})

    assert _summary(ctx)["dedupe_strategy"] == "url"
    assert len(ctx.state["deduped_articles"]) == 1


def test_legacy_by_title_domain_maps_to_title_domain_strategy(
    tmp_path: Path,
) -> None:
    articles = [
        _article(title="T", domain="a.com", url="https://1"),
        _article(title="T", domain="a.com", url="https://2"),
        _article(title="T", domain="b.com", url="https://3"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"by": ["title", "domain"]})

    assert _summary(ctx)["dedupe_strategy"] == "title_domain"
    assert len(ctx.state["deduped_articles"]) == 2


def test_legacy_unknown_by_falls_back_to_default(tmp_path: Path) -> None:
    articles = [
        _article(title="Same", url="https://a"),
        _article(title="Same", url="https://b"),
    ]
    ctx = _ctx(tmp_path, {"normalized_articles": articles})

    dedupe_articles_task(ctx, {"by": ["url", "title"]})

    assert _summary(ctx)["dedupe_strategy"] == "title"


# ── invalid strategy ─────────────────────────────────────────────────────


def test_invalid_strategy_raises(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"normalized_articles": [_article()]})

    with pytest.raises(ValueError, match="Unknown dedupe_strategy"):
        dedupe_articles_task(ctx, {"dedupe_strategy": "bogus"})

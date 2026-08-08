"""TaskRegistry: explicit construction, namespacing, duplicates and aliases."""

from __future__ import annotations

import pytest

from kinetic.core.errors import DuplicateTaskError, PipelineError
from kinetic.core.pipeline.registry import TaskRegistry, validate_task_id


def _noop(ctx, params):  # type: ignore[no-untyped-def]
    pass


def _other(ctx, params):  # type: ignore[no-untyped-def]
    pass


def test_register_and_resolve() -> None:
    registry = TaskRegistry()
    registry.register("news.gdelt.fetch_articles", _noop)
    assert registry.resolve("news.gdelt.fetch_articles") is _noop
    assert registry.task_ids() == ["news.gdelt.fetch_articles"]
    assert len(registry) == 1


def test_constructor_accepts_a_mapping() -> None:
    registry = TaskRegistry({"news.tag_articles": _noop})
    assert registry.resolve("news.tag_articles") is _noop


def test_duplicate_registration_raises() -> None:
    registry = TaskRegistry()
    registry.register("news.tag_articles", _noop)
    with pytest.raises(DuplicateTaskError, match="already registered"):
        registry.register("news.tag_articles", _other)


def test_allow_override_replaces_task() -> None:
    registry = TaskRegistry()
    registry.register("news.tag_articles", _noop)
    registry.register("news.tag_articles", _other, allow_override=True)
    assert registry.resolve("news.tag_articles") is _other


@pytest.mark.parametrize(
    "bad",
    ["", "  ", "News.Gdelt", "news..gdelt", "1news.gdelt", "news-gdelt", "news.gdelt."],
)
def test_invalid_task_ids_are_rejected(bad: str) -> None:
    with pytest.raises(PipelineError, match="Invalid task identifier"):
        validate_task_id(bad)


def test_unknown_task_names_the_namespace_siblings() -> None:
    registry = TaskRegistry()
    registry.register("news.tag_articles", _noop)
    registry.register("news.dedupe_articles", _noop)
    with pytest.raises(PipelineError) as excinfo:
        registry.resolve("news.tag_article")
    message = str(excinfo.value)
    assert "news.tag_articles" in message
    assert "news.dedupe_articles" in message


def test_unknown_namespace_lists_known_namespaces() -> None:
    registry = TaskRegistry()
    registry.register("news.tag_articles", _noop)
    registry.register("research.build_news_market_dataset", _noop)
    with pytest.raises(PipelineError) as excinfo:
        registry.resolve("trading.place_order")
    message = str(excinfo.value)
    assert "news" in message and "research" in message


def test_alias_resolves_and_warns() -> None:
    registry = TaskRegistry()
    registry.register("news.tag_articles", _noop)
    registry.register_alias("tag_articles", "news.tag_articles", removal_version="0.4.0")

    with pytest.warns(DeprecationWarning, match="news.tag_articles"):
        assert registry.resolve("tag_articles") is _noop

    assert registry.canonical_id("tag_articles") == "news.tag_articles"
    assert registry.alias_target("tag_articles") == "news.tag_articles"
    assert registry.alias_target("news.tag_articles") is None
    assert "tag_articles" in registry


def test_aliases_are_not_part_of_the_public_task_list() -> None:
    registry = TaskRegistry()
    registry.register("news.tag_articles", _noop)
    registry.register_alias("tag_articles", "news.tag_articles")
    assert registry.task_ids() == ["news.tag_articles"]
    assert registry.deprecated_aliases() == {"tag_articles": "news.tag_articles"}


def test_alias_to_unknown_task_is_rejected() -> None:
    registry = TaskRegistry()
    with pytest.raises(PipelineError, match="unknown task"):
        registry.register_alias("tag_articles", "news.tag_articles")


def test_alias_cannot_shadow_a_registered_task() -> None:
    registry = TaskRegistry()
    registry.register("news.tag_articles", _noop)
    registry.register("news.dedupe_articles", _other)
    with pytest.raises(DuplicateTaskError, match="collides"):
        registry.register_alias("news.dedupe_articles", "news.tag_articles")

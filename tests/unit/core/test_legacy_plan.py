"""The deprecated ingest/transform/strategy config shape.

These tests exist so the compatibility path stays honest until it is removed in
0.4.0: it must keep parsing what it used to parse, and it must warn every time.
"""

from __future__ import annotations

import warnings

import pytest

from kinetic.core.errors import ConfigError
from kinetic.core.pipeline.legacy_plan import to_current_shape
from kinetic.core.pipeline.plan import Step, parse_plan


def _parse(cfg: dict, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return parse_plan(cfg, **kwargs)


def test_legacy_shape_warns() -> None:
    with pytest.warns(DeprecationWarning, match="pipeline.steps"):
        parse_plan({"pipeline": {"ingest": {"source": "gdelt_docs"}}})


def test_ingest_then_transform_then_strategy_order() -> None:
    plan = _parse(
        {
            "pipeline": {
                "strategy": {"type": "my_strategy", "risk": "low"},
                "ingest": {"source": "gdelt_docs", "limit": 10},
                "transform": [{"type": "dedupe_articles"}],
            }
        }
    )
    assert [s.task for s in plan.steps] == ["gdelt_docs", "dedupe_articles", "my_strategy"]
    assert plan.steps[0] == Step(task="gdelt_docs", params={"limit": 10})
    assert plan.steps[2] == Step(task="my_strategy", params={"risk": "low"})


def test_single_key_transform_shorthand() -> None:
    plan = _parse({"pipeline": {"transform": [{"dedupe_articles": True}]}})
    assert plan.steps[0] == Step(task="dedupe_articles", params={})


def test_single_key_transform_shorthand_with_params() -> None:
    plan = _parse({"pipeline": {"transform": [{"dedupe_articles": {"by": ["url"]}}]}})
    assert plan.steps[0] == Step(task="dedupe_articles", params={"by": ["url"]})


def test_missing_ingest_source() -> None:
    with pytest.raises(ConfigError, match="pipeline.ingest.*source"):
        _parse({"pipeline": {"ingest": {"limit": 5}}})


def test_missing_strategy_type() -> None:
    with pytest.raises(ConfigError, match="pipeline.strategy.*type"):
        _parse({"pipeline": {"strategy": {"risk": "x"}}})


def test_transform_list_rejects_null_item() -> None:
    with pytest.raises(ConfigError, match=r"transform\[0\]"):
        _parse({"pipeline": {"transform": [None], "strategy": {"type": "s", "x": 1}}})


def test_transform_wrong_type() -> None:
    with pytest.raises(ConfigError, match="transform.*mapping or a list"):
        _parse({"pipeline": {"transform": "nope"}})


def test_empty_pipeline_is_rejected() -> None:
    with pytest.raises(ConfigError, match="'pipeline' is empty"):
        _parse({"pipeline": {}})


def test_unrecognized_sections_are_reported() -> None:
    with pytest.raises(ConfigError, match="unrecognized"):
        _parse({"pipeline": {"stages": [{"task": "a.b"}]}})


def test_migration_rewrites_to_the_current_shape() -> None:
    migrated = to_current_shape(
        {
            "name": "demo",
            "providers": {"news": {"gdelt": {"timeout_s": 30}}},
            "pipeline": {
                "ingest": {"source": "gdelt_docs", "limit": 10},
                "transform": [{"type": "dedupe_articles"}],
            },
        }
    )
    assert migrated["providers"] == {"news": {"gdelt": {"timeout_s": 30}}}
    assert migrated["pipeline"] == {
        "steps": [
            {"task": "gdelt_docs", "params": {"limit": 10}},
            {"task": "dedupe_articles"},
        ]
    }
    # The migrated output must parse under the current shape without warning.
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        plan = parse_plan(migrated)
    assert [s.task for s in plan.steps] == ["gdelt_docs", "dedupe_articles"]


def test_migrating_a_current_config_is_refused() -> None:
    with pytest.raises(ConfigError, match="already uses the current"):
        to_current_shape({"pipeline": {"steps": [{"task": "a.b"}]}})

"""The authoritative `pipeline.steps` config shape, and its validation errors.

Every message a user sees here has to name the file, the step and the field —
a config error that only says "invalid config" costs more time than it saves.
"""

from __future__ import annotations

import pytest

from kinetic.core.errors import ConfigError
from kinetic.core.pipeline.plan import Plan, Step, parse_plan


def _cfg(*steps: dict, name: str = "demo") -> dict:
    return {"name": name, "pipeline": {"steps": list(steps)}}


def test_parses_steps_in_order() -> None:
    plan = parse_plan(
        _cfg(
            {"task": "news.gdelt.fetch_articles", "params": {"max_records": 10}},
            {"task": "news.dedupe_articles", "params": {"by": ["url"]}},
            {"task": "news.store_articles"},
        )
    )
    assert isinstance(plan, Plan)
    assert plan.name == "demo"
    assert len(plan) == 3
    assert plan.steps[0] == Step(task="news.gdelt.fetch_articles", params={"max_records": 10})
    assert plan.steps[2] == Step(task="news.store_articles", params={})


def test_name_defaults_to_unnamed() -> None:
    plan = parse_plan({"pipeline": {"steps": [{"task": "news.tag_articles"}]}})
    assert plan.name == "unnamed"


def test_blank_name_defaults_to_unnamed() -> None:
    plan = parse_plan(_cfg({"task": "news.tag_articles"}, name="   "))
    assert plan.name == "unnamed"


def test_non_string_name_is_rejected() -> None:
    with pytest.raises(ConfigError, match="'name' must be a string"):
        parse_plan({"name": 7, "pipeline": {"steps": [{"task": "a.b"}]}})


def test_missing_pipeline_section() -> None:
    with pytest.raises(ConfigError, match="no 'pipeline' section"):
        parse_plan({"name": "demo"})


def test_pipeline_must_be_a_mapping() -> None:
    with pytest.raises(ConfigError, match="'pipeline' must be a mapping, got list"):
        parse_plan({"pipeline": []})


def test_steps_must_be_a_list() -> None:
    with pytest.raises(ConfigError, match="'pipeline.steps' must be a list, got dict"):
        parse_plan({"pipeline": {"steps": {"task": "a.b"}}})


def test_empty_steps_is_rejected() -> None:
    with pytest.raises(ConfigError, match="is empty"):
        parse_plan({"pipeline": {"steps": []}})


def test_step_must_be_a_mapping() -> None:
    with pytest.raises(ConfigError) as excinfo:
        parse_plan(_cfg({"task": "a.b"}, "oops"))  # type: ignore[arg-type]
    assert "pipeline.steps[1] must be a mapping" in str(excinfo.value)
    assert "got str" in str(excinfo.value)


def test_step_requires_a_task() -> None:
    with pytest.raises(ConfigError, match=r"pipeline\.steps\[0\] requires a non-empty"):
        parse_plan(_cfg({"params": {"x": 1}}))


def test_blank_task_is_rejected() -> None:
    with pytest.raises(ConfigError, match=r"pipeline\.steps\[0\] requires a non-empty"):
        parse_plan(_cfg({"task": "   "}))


def test_params_must_be_a_mapping_and_the_error_names_the_task() -> None:
    with pytest.raises(ConfigError) as excinfo:
        parse_plan(_cfg({"task": "news.tag_articles", "params": [1, 2]}))
    message = str(excinfo.value)
    assert "pipeline.steps[0]" in message
    assert "news.tag_articles" in message
    assert "'params' must be a mapping, got list" in message


def test_null_params_is_treated_as_empty() -> None:
    plan = parse_plan(_cfg({"task": "news.tag_articles", "params": None}))
    assert plan.steps[0].params == {}


def test_unknown_step_keys_are_rejected() -> None:
    """The commonest mistake is putting arguments beside `task` instead of in `params`."""
    with pytest.raises(ConfigError) as excinfo:
        parse_plan(_cfg({"task": "news.tag_articles", "max_records": 10}))
    message = str(excinfo.value)
    assert "'max_records'" in message
    assert "put step arguments inside 'params'" in message


def test_errors_name_the_source_file() -> None:
    with pytest.raises(ConfigError, match=r"configs/demo\.yaml: "):
        parse_plan({"pipeline": {"steps": []}}, source="configs/demo.yaml")


def test_params_are_copied_not_aliased() -> None:
    params = {"x": 1}
    plan = parse_plan(_cfg({"task": "news.tag_articles", "params": params}))
    params["x"] = 2
    assert plan.steps[0].params == {"x": 1}

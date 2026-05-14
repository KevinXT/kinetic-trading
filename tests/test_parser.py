"""Tests for pipeline plan parsing validation."""

import pytest

from common.errors import PipelineError
from pipeline_core.engine.parser import parse_plan, Step


def test_parse_minimal_ingest_and_strategy() -> None:
    cfg = {
        "pipeline": {
            "ingest": {"source": "gdelt_docs", "limit": 10},
            "strategy": {"type": "my_strategy", "risk": "low"},
        }
    }
    steps = parse_plan(cfg)
    assert len(steps) == 2
    assert steps[0] == Step(task="gdelt_docs", params={"limit": 10})
    assert steps[1] == Step(task="my_strategy", params={"risk": "low"})


def test_unknown_task_name_not_used_for_missing_source() -> None:
    with pytest.raises(PipelineError, match="pipeline.ingest.*source"):
        parse_plan({"pipeline": {"ingest": {"limit": 5}}})


def test_missing_strategy_type() -> None:
    with pytest.raises(PipelineError, match="pipeline.strategy.*type"):
        parse_plan({"pipeline": {"strategy": {"risk": "x"}}})


def test_pipeline_must_be_mapping() -> None:
    with pytest.raises(PipelineError, match="pipeline.*mapping"):
        parse_plan({"pipeline": []})


def test_transform_list_rejects_null_item() -> None:
    with pytest.raises(PipelineError, match="transform\\[0\\]"):
        parse_plan(
            {"pipeline": {"transform": [None], "strategy": {"type": "s", "x": 1}}}
        )


def test_transform_wrong_type() -> None:
    with pytest.raises(PipelineError, match="transform.*mapping or a list"):
        parse_plan({"pipeline": {"transform": "nope"}})


def test_empty_pipeline_dict_returns_no_steps() -> None:
    assert parse_plan({"pipeline": {}}) == []

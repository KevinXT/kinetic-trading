"""Tests for the cost policy model + loader."""

from pathlib import Path

import pytest
from common.cost.policy import CostPolicy, cost_policy_from_dict, load_cost_policy
from common.errors import ConfigError


def _write_policy(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "cost_policy.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_loads_repo_cost_policy_yaml() -> None:
    policy = load_cost_policy("configs/cost_policy.yaml")
    assert isinstance(policy, CostPolicy)
    assert policy.monthly_cap_usd == 10.0
    assert policy.emergency_stop_usd == 8.0
    # Single-query cap raised to $0.20 (~32.8 GiB at $6.25/TiB) so the year-long
    # semiconductor GDELT scans (and the seeded free-text scan) can run under the
    # typed execution gate; still well below the daily/monthly caps.
    assert policy.single_query_cap_usd == 0.20
    assert policy.execute_confirmation_value == "ENABLE"
    assert policy.bigquery_usd_per_tib == 6.25
    assert policy.free_query_tib_per_month == 1.0


def test_applies_defaults_when_fields_missing() -> None:
    # explicit value preserved, everything else defaulted
    policy = cost_policy_from_dict({"cost_policy": {"single_query_cap_usd": 0.10}})
    assert policy.single_query_cap_usd == 0.10
    assert policy.monthly_cap_usd == 10.0
    assert policy.emergency_stop_usd == 8.0
    assert policy.billing_cycle_start_day == 1
    assert policy.daily_cap_usd == 0.50
    assert policy.require_dry_run is True
    assert policy.bigquery_usd_per_tib == 6.25


def test_accepts_inner_mapping_without_wrapper() -> None:
    policy = cost_policy_from_dict({"single_query_cap_usd": 0.10})
    assert policy.single_query_cap_usd == 0.10


def test_rejects_negative_caps() -> None:
    with pytest.raises(ConfigError, match="cannot be negative"):
        cost_policy_from_dict({"cost_policy": {"daily_cap_usd": -1.0}})


def test_rejects_emergency_stop_above_monthly_cap() -> None:
    with pytest.raises(ConfigError, match="emergency_stop_usd"):
        cost_policy_from_dict({"cost_policy": {"monthly_cap_usd": 5.0, "emergency_stop_usd": 9.0}})


def test_rejects_non_positive_max_query_gb() -> None:
    with pytest.raises(ConfigError, match="default_max_query_gb"):
        cost_policy_from_dict({"cost_policy": {"default_max_query_gb": 0}})


def test_rejects_empty_confirmation_when_required() -> None:
    with pytest.raises(ConfigError, match="execute_confirmation_value"):
        cost_policy_from_dict(
            {
                "cost_policy": {
                    "require_execute_confirmation": True,
                    "execute_confirmation_value": "",
                }
            }
        )


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_cost_policy(tmp_path / "nope.yaml")


def test_default_max_query_bytes_property() -> None:
    policy = cost_policy_from_dict({"cost_policy": {"default_max_query_gb": 2}})
    assert policy.default_max_query_bytes == 2 * (1024**3)

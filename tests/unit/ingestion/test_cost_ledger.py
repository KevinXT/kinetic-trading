"""Tests for the append-only cost ledger."""

from datetime import date
from pathlib import Path

import pytest

from kinetic.ingestion.cost.ledger import (
    CostLedger,
    billing_cycle_bounds,
    billing_cycle_label,
)
from kinetic.ingestion.cost.policy import cost_policy_from_dict

POLICY = cost_policy_from_dict(
    {
        "cost_policy": {
            "monthly_cap_usd": 10.0,
            "emergency_stop_usd": 8.0,
            "daily_cap_usd": 0.50,
        }
    }
)


def _rec(**kw) -> dict:
    base = {
        "provider": "bigquery",
        "query_name": "q",
        "topic": "inflation_rates",
        "dry_run": False,
        "estimated_bytes": 0,
        "estimated_cost_usd": 0.0,
        "decision": "ALLOW",
        "cache_hit": False,
        "status": "success",
    }
    base.update(kw)
    return base


def test_append_creates_missing_dir_and_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "cost_ledger.jsonl"
    ledger = CostLedger(path)
    assert not path.exists()
    ledger.append(_rec())
    assert path.is_file()


def test_append_adds_timestamp_when_missing(tmp_path: Path) -> None:
    ledger = CostLedger(tmp_path / "l.jsonl")
    stored = ledger.append(_rec())
    assert "timestamp" in stored and stored["timestamp"].endswith("Z")


def test_read_all_roundtrip(tmp_path: Path) -> None:
    ledger = CostLedger(tmp_path / "l.jsonl")
    ledger.append(_rec(query_name="a"))
    ledger.append(_rec(query_name="b"))
    records = ledger.read_all()
    assert [r["query_name"] for r in records] == ["a", "b"]


def test_read_all_missing_file_is_empty(tmp_path: Path) -> None:
    assert CostLedger(tmp_path / "missing.jsonl").read_all() == []


def test_summary_by_provider_and_totals(tmp_path: Path) -> None:
    ledger = CostLedger(tmp_path / "l.jsonl")
    ts = "2026-05-15T10:00:00Z"
    ledger.append(_rec(provider="bigquery", estimated_cost_usd=0.03, timestamp=ts))
    ledger.append(_rec(provider="bigquery", estimated_cost_usd=0.02, timestamp=ts))
    # blocked + dry-run do not count toward spend
    ledger.append(_rec(decision="DRY_RUN_ONLY", estimated_cost_usd=99.0, timestamp=ts))
    ledger.append(_rec(decision="BLOCK_OVER_MAX_BYTES", estimated_cost_usd=99.0, timestamp=ts))

    summary = ledger.summarize(POLICY, on=date(2026, 5, 15))
    assert summary.provider_estimated_usd["bigquery"] == pytest.approx(0.05)
    assert summary.cycle_estimated_usd == pytest.approx(0.05)
    assert summary.today_estimated_usd == pytest.approx(0.05)
    assert summary.remaining_monthly_usd == pytest.approx(10.0 - 0.05)
    assert summary.blocked_query_count == 1


def test_summary_daily_vs_cycle(tmp_path: Path) -> None:
    ledger = CostLedger(tmp_path / "l.jsonl")
    ledger.append(_rec(estimated_cost_usd=0.10, timestamp="2026-05-02T00:00:00Z"))
    ledger.append(_rec(estimated_cost_usd=0.20, timestamp="2026-05-20T00:00:00Z"))

    summary = ledger.summarize(POLICY, on=date(2026, 5, 20))
    assert summary.cycle_estimated_usd == pytest.approx(0.30)
    assert summary.today_estimated_usd == pytest.approx(0.20)


def test_summary_cache_hit_rate_and_most_expensive(tmp_path: Path) -> None:
    ledger = CostLedger(tmp_path / "l.jsonl")
    ts = "2026-05-10T00:00:00Z"
    ledger.append(_rec(decision="USE_CACHE", cache_hit=True, timestamp=ts))
    ledger.append(_rec(query_name="cheap", estimated_cost_usd=0.01, timestamp=ts))
    ledger.append(_rec(query_name="pricey", estimated_cost_usd=0.04, timestamp=ts))

    summary = ledger.summarize(POLICY, on=date(2026, 5, 10))
    assert summary.cache_hit_count == 1
    assert summary.total_query_count == 3
    assert summary.cache_hit_rate == 1 / 3
    assert summary.most_expensive_job == "pricey"


def test_emergency_stop_reached(tmp_path: Path) -> None:
    ledger = CostLedger(tmp_path / "l.jsonl")
    ledger.append(_rec(estimated_cost_usd=8.5, timestamp="2026-05-05T00:00:00Z"))
    summary = ledger.summarize(POLICY, on=date(2026, 5, 5))
    assert summary.emergency_stop_reached is True


def test_billing_cycle_bounds_default_month() -> None:
    start, end = billing_cycle_bounds(date(2026, 5, 15), start_day=1)
    assert start == date(2026, 5, 1)
    assert end == date(2026, 5, 31)


def test_billing_cycle_label() -> None:
    assert billing_cycle_label(date(2026, 5, 15), start_day=1) == "2026-05-01_to_2026-05-31"


def test_billing_cycle_mid_month_start() -> None:
    # cycle starts on the 10th; a date before the 10th belongs to prior cycle
    start, end = billing_cycle_bounds(date(2026, 5, 3), start_day=10)
    assert start == date(2026, 4, 10)
    assert end == date(2026, 5, 9)

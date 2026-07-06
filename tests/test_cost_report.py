"""Tests for the cost report formatter + CLI wiring."""

from datetime import date
from pathlib import Path

from common.cost.ledger import CostLedger
from common.cost.policy import cost_policy_from_dict
from common.cost.report import format_cost_report

POLICY = cost_policy_from_dict(
    {"cost_policy": {"monthly_cap_usd": 10.0, "emergency_stop_usd": 8.0}}
)


def _ledger(tmp_path: Path) -> CostLedger:
    ledger = CostLedger(tmp_path / "l.jsonl")
    ts = "2026-05-15T00:00:00Z"

    def rec(**kw):
        base = {
            "provider": "bigquery_gdelt",
            "query_name": "q",
            "decision": "ALLOW",
            "cache_hit": False,
            "estimated_cost_usd": 0.0,
            "timestamp": ts,
        }
        base.update(kw)
        return base

    ledger.append(rec(query_name="inflation_rates_bigquery_30d", estimated_cost_usd=0.03))
    ledger.append(rec(decision="BLOCK_OVER_MAX_BYTES"))
    ledger.append(rec(decision="BLOCK_MISSING_EXECUTE_CONFIRMATION"))
    return ledger


def test_report_contains_key_lines(tmp_path: Path) -> None:
    summary = _ledger(tmp_path).summarize(POLICY, on=date(2026, 5, 15))
    text = format_cost_report(summary)

    assert "Billing cycle: May 1 \u2192 May 31" in text
    assert "Monthly cap:        $10.00" in text
    assert "Emergency stop:     $8.00" in text
    assert "Estimated used:     $0.03" in text
    assert "Remaining:          $9.97" in text
    assert "BigQuery:           $0.03" in text
    assert "Blocked queries:    2" in text
    assert "Most expensive job: inflation_rates_bigquery_30d" in text


def test_report_empty_ledger(tmp_path: Path) -> None:
    summary = CostLedger(tmp_path / "empty.jsonl").summarize(POLICY, on=date(2026, 5, 1))
    text = format_cost_report(summary)
    assert "Estimated used:     $0.00" in text
    assert "(none)" in text
    assert "Blocked queries:    0" in text


def test_emergency_stop_warning(tmp_path: Path) -> None:
    ledger = CostLedger(tmp_path / "l.jsonl")
    ledger.append(
        {
            "provider": "bigquery_gdelt",
            "query_name": "big",
            "decision": "ALLOW",
            "estimated_cost_usd": 9.0,
            "timestamp": "2026-05-15T00:00:00Z",
        }
    )
    summary = ledger.summarize(POLICY, on=date(2026, 5, 15))
    text = format_cost_report(summary)
    assert "EMERGENCY STOP REACHED" in text


def test_cli_cost_report_runs(tmp_path, capsys) -> None:
    from trading_platform.__main__ import run_cost_report

    ledger_path = tmp_path / "l.jsonl"
    _ledger(tmp_path)  # writes to tmp_path / "l.jsonl"

    run_cost_report(
        ["--ledger-path", str(ledger_path), "--policy-path", "configs/cost_policy.yaml"]
    )
    out = capsys.readouterr().out
    assert "Billing cycle:" in out
    assert "Provider usage:" in out

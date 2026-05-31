"""Tests for SafeBigQueryClient (mocked BigQuery; no network)."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from common.cost.ledger import CostLedger
from common.cost.policy import cost_policy_from_dict
from common.errors import CostGuardrailError
from news_data.bigquery.client import (
    SafeBigQueryClient,
    SafeQueryDecision,
)

_GIB = 1024**3
_TIB = 1024**4

SAFE_SQL = (
    "SELECT DATE AS date, COUNT(*) AS article_count\n"
    "FROM `gdelt-bq.gdeltv2.gkg_partitioned`\n"
    "WHERE DATE BETWEEN 20260501 AND 20260530\n"
    "  AND (LOWER(V2Themes) LIKE '%inflation%')\n"
    "GROUP BY date\nORDER BY date"
)
TABLES = ["gdelt-bq.gdeltv2.gkg_partitioned"]


class _FakeJob:
    def __init__(self, total_bytes: int, rows=None) -> None:
        self.total_bytes_processed = total_bytes
        self._rows = rows or []

    def result(self):
        return list(self._rows)


class _FakeBQClient:
    """Records query calls; returns a dry-run job or a result job by config."""

    def __init__(self, total_bytes: int, rows=None) -> None:
        self.total_bytes = total_bytes
        self.rows = rows or []
        self.calls = []  # list of (sql, job_config)

    def query(self, sql, job_config=None):
        self.calls.append((sql, job_config))
        if getattr(job_config, "dry_run", False):
            return _FakeJob(self.total_bytes)
        return _FakeJob(self.total_bytes, self.rows)


def _factory(*, dry_run, maximum_bytes_billed):
    return SimpleNamespace(dry_run=dry_run, maximum_bytes_billed=maximum_bytes_billed)


def _policy(**overrides):
    body = {
        "monthly_cap_usd": 10.0,
        "emergency_stop_usd": 8.0,
        "daily_cap_usd": 0.50,
        "single_query_cap_usd": 0.02,
    }
    body.update(overrides)
    return cost_policy_from_dict({"cost_policy": body})


def _client(total_bytes, rows=None):
    fake = _FakeBQClient(total_bytes, rows)
    client = SafeBigQueryClient(project_id="proj", client=fake, job_config_factory=_factory)
    return client, fake


def test_dry_run_only_does_not_execute(tmp_path: Path) -> None:
    client, fake = _client(1 * _GIB)
    ledger = CostLedger(tmp_path / "l.jsonl")
    result = client.safe_query(
        sql=SAFE_SQL,
        query_name="q",
        topic="inflation_rates",
        cost_policy=_policy(),
        ledger=ledger,
        allowed_tables=TABLES,
        dry_run=True,
        maximum_bytes_billed=2 * _GIB,
        execute_query="",
    )
    assert result.decision == SafeQueryDecision.DRY_RUN_ONLY
    assert result.rows == []
    # only the dry-run call happened
    assert len(fake.calls) == 1
    assert fake.calls[0][1].dry_run is True
    assert ledger.read_all()[-1]["decision"] == "DRY_RUN_ONLY"


def test_missing_enable_blocks_execution(tmp_path: Path) -> None:
    client, fake = _client(1 * _GIB)
    ledger = CostLedger(tmp_path / "l.jsonl")
    with pytest.raises(CostGuardrailError, match="typed confirmation"):
        client.safe_query(
            sql=SAFE_SQL,
            query_name="q",
            topic="t",
            cost_policy=_policy(),
            ledger=ledger,
            allowed_tables=TABLES,
            dry_run=False,
            maximum_bytes_billed=2 * _GIB,
            execute_query="",
        )
    # dry-run happened, no execution
    assert all(c[1].dry_run for c in fake.calls)
    assert ledger.read_all()[-1]["decision"] == "BLOCK_MISSING_EXECUTE_CONFIRMATION"


def test_estimated_bytes_over_cap_blocks(tmp_path: Path) -> None:
    client, fake = _client(5 * _GIB)
    ledger = CostLedger(tmp_path / "l.jsonl")
    with pytest.raises(CostGuardrailError):
        client.safe_query(
            sql=SAFE_SQL,
            query_name="q",
            topic="t",
            cost_policy=_policy(single_query_cap_usd=99.0),
            ledger=ledger,
            allowed_tables=TABLES,
            dry_run=False,
            maximum_bytes_billed=1 * _GIB,  # below the 5 GiB estimate
            execute_query="ENABLE",
        )
    assert ledger.read_all()[-1]["decision"] == "BLOCK_OVER_MAX_BYTES"


def test_estimated_cost_over_single_query_cap_blocks(tmp_path: Path) -> None:
    # 5 GiB ~= $0.0305, above the 0.02 single-query cap, but within max bytes.
    client, fake = _client(5 * _GIB)
    ledger = CostLedger(tmp_path / "l.jsonl")
    with pytest.raises(CostGuardrailError):
        client.safe_query(
            sql=SAFE_SQL,
            query_name="q",
            topic="t",
            cost_policy=_policy(),
            ledger=ledger,
            allowed_tables=TABLES,
            dry_run=False,
            maximum_bytes_billed=100 * _GIB,
            execute_query="ENABLE",
        )
    assert ledger.read_all()[-1]["decision"] == "BLOCK_OVER_SINGLE_QUERY_CAP"


def test_cache_hit_skips_bigquery(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.jsonl"
    cache_path.write_text('{"date": "2026-05-01", "article_count": 5}\n', encoding="utf-8")
    client, fake = _client(1 * _GIB)
    ledger = CostLedger(tmp_path / "l.jsonl")
    result = client.safe_query(
        sql=SAFE_SQL,
        query_name="q",
        topic="t",
        cost_policy=_policy(),
        ledger=ledger,
        allowed_tables=TABLES,
        cache_path=cache_path,
        dry_run=False,
        maximum_bytes_billed=2 * _GIB,
        execute_query="ENABLE",
        use_cache=True,
    )
    assert result.decision == SafeQueryDecision.USE_CACHE
    assert result.cache_hit is True
    assert result.rows == [{"date": "2026-05-01", "article_count": 5}]
    assert fake.calls == []  # never touched BigQuery
    assert ledger.read_all()[-1]["decision"] == "USE_CACHE"


def test_allowed_execution_sets_maximum_bytes_billed(tmp_path: Path) -> None:
    rows = [{"date": "2026-05-01", "article_count": 10}]
    client, fake = _client(1 * _GIB, rows=rows)
    ledger = CostLedger(tmp_path / "l.jsonl")
    cache_path = tmp_path / "cache.jsonl"
    result = client.safe_query(
        sql=SAFE_SQL,
        query_name="q",
        topic="t",
        cost_policy=_policy(),
        ledger=ledger,
        allowed_tables=TABLES,
        cache_path=cache_path,
        dry_run=False,
        maximum_bytes_billed=2 * _GIB,
        execute_query="ENABLE",
        use_cache=True,
    )
    assert result.decision == SafeQueryDecision.ALLOW
    assert result.rows == rows
    # last call is the execution; max bytes billed enforced
    exec_calls = [c for c in fake.calls if not c[1].dry_run]
    assert len(exec_calls) == 1
    assert exec_calls[0][1].maximum_bytes_billed == 2 * _GIB
    # result cached for next time
    assert cache_path.is_file()
    assert ledger.read_all()[-1]["decision"] == "ALLOW"


def test_ledger_records_each_decision_kind(tmp_path: Path) -> None:
    ledger = CostLedger(tmp_path / "l.jsonl")
    policy = _policy()

    # dry-run
    client, _ = _client(1 * _GIB)
    client.safe_query(
        sql=SAFE_SQL,
        query_name="dry",
        topic="t",
        cost_policy=policy,
        ledger=ledger,
        allowed_tables=TABLES,
        dry_run=True,
        maximum_bytes_billed=2 * _GIB,
    )
    # allow
    client, _ = _client(1 * _GIB, rows=[{"date": "2026-05-01", "article_count": 1}])
    client.safe_query(
        sql=SAFE_SQL,
        query_name="run",
        topic="t",
        cost_policy=policy,
        ledger=ledger,
        allowed_tables=TABLES,
        cache_path=tmp_path / "c.jsonl",
        dry_run=False,
        maximum_bytes_billed=2 * _GIB,
        execute_query="ENABLE",
    )
    decisions = [r["decision"] for r in ledger.read_all()]
    assert "DRY_RUN_ONLY" in decisions
    assert "ALLOW" in decisions


def test_dry_run_over_cap_still_reports_dry_run_only(tmp_path: Path) -> None:
    # 5 GiB estimate exceeds the 1 GiB cap, but dry-run is informational: the
    # decision stays DRY_RUN_ONLY with a would-be-blocked note.
    client, fake = _client(5 * _GIB)
    ledger = CostLedger(tmp_path / "l.jsonl")
    result = client.safe_query(
        sql=SAFE_SQL,
        query_name="q",
        topic="t",
        cost_policy=_policy(),
        ledger=ledger,
        allowed_tables=TABLES,
        dry_run=True,
        maximum_bytes_billed=1 * _GIB,
        execute_query="",
    )
    assert result.decision == SafeQueryDecision.DRY_RUN_ONLY
    assert "would be blocked" in (result.message or "")
    assert ledger.read_all()[-1]["decision"] == "DRY_RUN_ONLY"


def test_dry_run_does_not_write_result_cache(tmp_path: Path) -> None:
    client, fake = _client(1 * _GIB)
    ledger = CostLedger(tmp_path / "l.jsonl")
    cache_path = tmp_path / "cache.jsonl"
    result = client.safe_query(
        sql=SAFE_SQL,
        query_name="q",
        topic="t",
        cost_policy=_policy(),
        ledger=ledger,
        allowed_tables=TABLES,
        cache_path=cache_path,
        dry_run=True,
        maximum_bytes_billed=2 * _GIB,
        execute_query="",
        use_cache=True,
    )
    assert result.decision == SafeQueryDecision.DRY_RUN_ONLY
    # dry-run must never fabricate a cache entry
    assert not cache_path.exists()


def test_run_query_requires_max_bytes(tmp_path: Path) -> None:
    client, _ = _client(1 * _GIB)
    with pytest.raises(ValueError, match="maximum_bytes_billed"):
        client.run_query(SAFE_SQL, maximum_bytes_billed=None)  # type: ignore[arg-type]

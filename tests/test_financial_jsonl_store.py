"""Tests for atomic provider-independent financial JSONL persistence."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from market_data.domain.models import PriceBar
from market_data.storage import jsonl
from market_data.storage.errors import (
    ConcurrentDatasetWriteError,
    ConflictingBatchRecordsError,
    DatasetSchemaError,
)
from market_data.storage.jsonl import JsonlFinancialDataStore

NOW = datetime(2026, 7, 14, 20, 0, tzinfo=timezone.utc)


def _bar(
    *,
    symbol: str = "AAPL",
    close: float = 204.0,
    feed: str = "iex",
    adjustment: str = "all",
) -> PriceBar:
    return PriceBar(
        symbol=symbol,
        timestamp=NOW,
        timeframe="1Day",
        open=200.0,
        high=205.0,
        low=199.0,
        close=close,
        volume=1000,
        vwap=None,
        trade_count=None,
        provider="alpaca",
        feed=feed,
        adjustment=adjustment,
        retrieved_at=NOW,
    )


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_insert_then_identical_rerun_is_idempotent(tmp_path: Path) -> None:
    store = JsonlFinancialDataStore(tmp_path)
    first = store.upsert_price_bars([_bar()])
    second = store.upsert_price_bars([_bar()])
    assert (first.inserted, first.updated, first.skipped) == (1, 0, 0)
    assert (second.inserted, second.updated, second.skipped) == (0, 0, 1)
    assert second.total_records == 1
    assert len(_rows(second.path)) == 1


def test_same_logical_key_with_changed_payload_updates(tmp_path: Path) -> None:
    store = JsonlFinancialDataStore(tmp_path)
    store.upsert_price_bars([_bar(close=204.0)])
    result = store.upsert_price_bars([_bar(close=203.0)])
    assert (result.inserted, result.updated, result.skipped) == (0, 1, 0)
    assert _rows(result.path)[0]["close"] == 203.0


def test_feed_and_adjustment_are_distinct_logical_records(tmp_path: Path) -> None:
    store = JsonlFinancialDataStore(tmp_path)
    result = store.upsert_price_bars([_bar(feed="iex"), _bar(feed="sip"), _bar(adjustment="raw")])
    assert result.inserted == 3
    assert result.total_records == 3


def test_duplicate_records_within_batch_are_skipped(tmp_path: Path) -> None:
    store = JsonlFinancialDataStore(tmp_path)
    result = store.upsert_price_bars([_bar(), _bar()])
    assert (result.inserted, result.skipped) == (1, 1)
    assert result.total_records == 1


def test_conflicting_records_within_batch_raise_without_writing(tmp_path: Path) -> None:
    store = JsonlFinancialDataStore(tmp_path)
    with pytest.raises(ConflictingBatchRecordsError) as error:
        store.upsert_price_bars([_bar(close=204.0), _bar(close=203.0)])
    assert error.value.logical_key[0] == "AAPL"
    assert not (tmp_path / "market_bars.jsonl").exists()
    assert not (tmp_path / "market_bars.jsonl.metadata.json").exists()


def test_serialization_and_record_order_are_stable(tmp_path: Path) -> None:
    first = JsonlFinancialDataStore(tmp_path / "first")
    second = JsonlFinancialDataStore(tmp_path / "second")
    first_result = first.upsert_price_bars([_bar(symbol="MSFT"), _bar(symbol="AAPL")])
    second_result = second.upsert_price_bars([_bar(symbol="AAPL"), _bar(symbol="MSFT")])
    assert first_result.path.read_bytes() == second_result.path.read_bytes()
    text = first_result.path.read_text(encoding="utf-8")
    assert '"timestamp":"2026-07-14T20:00:00Z"' in text


def test_dataset_metadata_contains_deterministic_schema_version(tmp_path: Path) -> None:
    result = JsonlFinancialDataStore(tmp_path).upsert_price_bars([_bar()])
    assert result.schema_version == "financial-data.v1"
    assert json.loads(result.metadata_path.read_text(encoding="utf-8")) == {
        "record_type": "PriceBar",
        "schema_version": "financial-data.v1",
    }


def test_incompatible_dataset_schema_is_rejected(tmp_path: Path) -> None:
    store = JsonlFinancialDataStore(tmp_path)
    result = store.upsert_price_bars([_bar()])
    result.metadata_path.write_text(
        '{"record_type":"PriceBar","schema_version":"future.v2"}\n',
        encoding="utf-8",
    )
    with pytest.raises(DatasetSchemaError, match="incompatible"):
        store.upsert_price_bars([_bar()])


def test_existing_lock_enforces_single_writer_rule(tmp_path: Path) -> None:
    lock_path = tmp_path / "market_bars.jsonl.lock"
    lock_path.write_text("another-writer\n", encoding="utf-8")
    with pytest.raises(ConcurrentDatasetWriteError) as error:
        JsonlFinancialDataStore(tmp_path).upsert_price_bars([_bar()])
    assert error.value.lock_path == lock_path
    assert lock_path.read_text(encoding="utf-8") == "another-writer\n"
    assert not (tmp_path / "market_bars.jsonl").exists()


def test_empty_upsert_creates_an_empty_dataset(tmp_path: Path) -> None:
    result = JsonlFinancialDataStore(tmp_path).upsert_price_bars([])
    assert result.path.is_file()
    assert result.path.read_text(encoding="utf-8") == ""
    assert result.total_records == 0
    assert result.metadata_path.is_file()


def test_malformed_existing_jsonl_fails_without_rewrite(tmp_path: Path) -> None:
    path = tmp_path / "market_bars.jsonl"
    path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed JSONL"):
        JsonlFinancialDataStore(tmp_path).upsert_price_bars([_bar()])
    assert path.read_text(encoding="utf-8") == "not-json\n"


def test_replace_failure_preserves_existing_target_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JsonlFinancialDataStore(tmp_path)
    path = store.upsert_price_bars([_bar()]).path
    original = path.read_bytes()

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(jsonl.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        store.upsert_price_bars([_bar(close=203.0)])
    assert path.read_bytes() == original
    assert list(tmp_path.glob("*.tmp")) == []
    assert not (tmp_path / "market_bars.jsonl.lock").exists()


def test_market_event_storage_is_deferred() -> None:
    assert not hasattr(JsonlFinancialDataStore, "upsert_market_events")

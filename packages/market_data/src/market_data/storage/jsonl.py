"""Atomic, idempotent JSONL persistence for normalized financial records."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, TypeVar

from market_data.domain.models import (
    FilingEvent,
    FinancialFact,
    Instrument,
    MacroObservation,
    PriceBar,
)
from market_data.domain.serialization import record_to_dict, stable_json_dumps
from market_data.storage.base import StoreResult
from market_data.storage.errors import (
    ConcurrentDatasetWriteError,
    ConflictingBatchRecordsError,
    DatasetSchemaError,
)

FINANCIAL_DATA_SCHEMA_VERSION = "financial-data.v1"

RecordT = TypeVar(
    "RecordT",
    PriceBar,
    FilingEvent,
    FinancialFact,
    MacroObservation,
    Instrument,
)
JsonRecord = dict[str, Any]
RecordKey = tuple[object, ...]


def _fields_key(*fields: str) -> Callable[[JsonRecord], RecordKey]:
    def key(record: JsonRecord) -> RecordKey:
        missing = [field for field in fields if field not in record]
        if missing:
            raise ValueError(f"stored record is missing logical key fields: {', '.join(missing)}")
        return tuple(record[field] for field in fields)

    return key


_BAR_KEY = _fields_key("symbol", "timestamp", "timeframe", "provider", "feed", "adjustment")
_FILING_KEY = _fields_key("accession_number")
_FACT_KEY = _fields_key(
    "cik",
    "taxonomy",
    "concept",
    "unit",
    "period_start",
    "period_end",
    "form",
    "filed_date",
    "accession_number",
)
_MACRO_KEY = _fields_key(
    "series_id",
    "observation_date",
    "realtime_start",
    "realtime_end",
    "vintage_date",
    "provider",
)
_INSTRUMENT_KEY = _fields_key("instrument_id")


class JsonlFinancialDataStore:
    """Persist deterministic JSONL with one explicitly locked writer per dataset."""

    def __init__(self, root: Path | str = "data/processed/financial_data") -> None:
        self.root = Path(root)

    def _read(
        self, path: Path, key_fn: Callable[[JsonRecord], RecordKey]
    ) -> dict[RecordKey, JsonRecord]:
        records: dict[RecordKey, JsonRecord] = {}
        if not path.exists():
            return records
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"malformed JSONL at {path}:{line_number}: {exc.msg}") from exc
                if not isinstance(record, dict):
                    raise ValueError(f"JSONL record at {path}:{line_number} must be an object")
                key = key_fn(record)
                if key in records:
                    raise ValueError(
                        f"duplicate logical key in existing dataset {path}:{line_number}"
                    )
                records[key] = record
        return records

    @staticmethod
    @contextmanager
    def _writer_lock(path: Path) -> Iterator[None]:
        """Acquire an exclusive lock file; stale locks require manual removal."""
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise ConcurrentDatasetWriteError(path) from exc
        try:
            os.write(descriptor, f"{os.getpid()}\n".encode())
            os.close(descriptor)
            descriptor = -1
            yield
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            path.unlink(missing_ok=True)

    @staticmethod
    def _write_atomic(path: Path, records: dict[RecordKey, JsonRecord]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                ordered = sorted(records.items(), key=lambda item: stable_json_dumps(item[0]))
                for _, record in ordered:
                    handle.write(stable_json_dumps(record))
                    handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _write_metadata(path: Path, record_type: str) -> None:
        metadata = {
            "record_type": record_type,
            "schema_version": FINANCIAL_DATA_SCHEMA_VERSION,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(stable_json_dumps(metadata))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _validate_metadata(path: Path, record_type: str) -> bool:
        if not path.exists():
            return False
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise DatasetSchemaError(f"invalid dataset metadata: {path}") from exc
        expected = {
            "record_type": record_type,
            "schema_version": FINANCIAL_DATA_SCHEMA_VERSION,
        }
        if metadata != expected:
            raise DatasetSchemaError(
                f"incompatible dataset metadata at {path}: "
                f"expected {expected!r}, got {metadata!r}"
            )
        return True

    @staticmethod
    def _collapse_batch(
        incoming: Sequence[RecordT],
        key_fn: Callable[[JsonRecord], RecordKey],
    ) -> tuple[dict[RecordKey, JsonRecord], int]:
        collapsed: dict[RecordKey, JsonRecord] = {}
        duplicate_count = 0
        for domain_record in incoming:
            record = record_to_dict(domain_record)
            key = key_fn(record)
            existing = collapsed.get(key)
            if existing is None:
                collapsed[key] = record
            elif existing == record:
                duplicate_count += 1
            else:
                raise ConflictingBatchRecordsError(key)
        return collapsed, duplicate_count

    def _upsert(
        self,
        filename: str,
        record_type: str,
        incoming: Sequence[RecordT],
        key_fn: Callable[[JsonRecord], RecordKey],
    ) -> StoreResult:
        path = self.root / filename
        metadata_path = path.with_suffix(f"{path.suffix}.metadata.json")
        lock_path = path.with_suffix(f"{path.suffix}.lock")
        batch, skipped = self._collapse_batch(incoming, key_fn)

        with self._writer_lock(lock_path):
            has_metadata = self._validate_metadata(metadata_path, record_type)
            records = self._read(path, key_fn)
            inserted = 0
            updated = 0

            for key, record in batch.items():
                existing = records.get(key)
                if existing is None:
                    records[key] = record
                    inserted += 1
                elif existing == record:
                    skipped += 1
                else:
                    records[key] = record
                    updated += 1

            if not has_metadata:
                self._write_metadata(metadata_path, record_type)
            if inserted or updated or not path.exists():
                self._write_atomic(path, records)

        return StoreResult(
            inserted=inserted,
            updated=updated,
            skipped=skipped,
            total_records=len(records),
            path=path,
            metadata_path=metadata_path,
            schema_version=FINANCIAL_DATA_SCHEMA_VERSION,
        )

    def upsert_price_bars(self, records: Sequence[PriceBar]) -> StoreResult:
        return self._upsert("market_bars.jsonl", "PriceBar", records, _BAR_KEY)

    def upsert_filing_events(self, records: Sequence[FilingEvent]) -> StoreResult:
        return self._upsert("sec_filings.jsonl", "FilingEvent", records, _FILING_KEY)

    def upsert_financial_facts(self, records: Sequence[FinancialFact]) -> StoreResult:
        return self._upsert("financial_facts.jsonl", "FinancialFact", records, _FACT_KEY)

    def upsert_macro_observations(self, records: Sequence[MacroObservation]) -> StoreResult:
        return self._upsert("macro_observations.jsonl", "MacroObservation", records, _MACRO_KEY)

    def upsert_instruments(self, records: Sequence[Instrument]) -> StoreResult:
        return self._upsert("instrument_master.jsonl", "Instrument", records, _INSTRUMENT_KEY)

"""Explicit failures raised by financial data stores."""

from __future__ import annotations

from pathlib import Path


class FinancialDataStoreError(RuntimeError):
    """Base class for local financial store failures."""


class ConcurrentDatasetWriteError(FinancialDataStoreError):
    """Raised when another writer already holds a dataset lock."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        super().__init__(
            f"dataset is already locked for writing: {lock_path}; "
            "only one writer per dataset is supported"
        )


class ConflictingBatchRecordsError(FinancialDataStoreError):
    """Raised when one batch contains different payloads for one logical key."""

    def __init__(self, logical_key: tuple[object, ...]) -> None:
        self.logical_key = logical_key
        super().__init__(f"conflicting records share logical key {logical_key!r}")


class DatasetSchemaError(FinancialDataStoreError):
    """Raised when persisted schema metadata is invalid or incompatible."""

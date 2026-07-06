"""
BigQuery cost estimation helpers.

These are deliberately conservative: per-query guardrails ignore any remaining
monthly free tier so a query is never under-estimated when deciding whether to
let it run. The ledger may separately note free-tier context, but the
single-query guardrail must not depend on remaining free bytes.
"""

from __future__ import annotations

# 1 GiB / 1 TiB in bytes (binary units, matching Google Cloud billing docs).
_BYTES_PER_GIB = 1024**3
_BYTES_PER_TIB = 1024**4

# Default BigQuery on-demand price (USD per TiB scanned).
DEFAULT_USD_PER_TIB = 6.25


def bytes_to_gib(bytes_value: int) -> float:
    """Convert a byte count to gibibytes (GiB)."""
    if bytes_value < 0:
        raise ValueError(f"bytes_value must be >= 0, got {bytes_value}")
    return bytes_value / _BYTES_PER_GIB


def bytes_to_tib(bytes_value: int) -> float:
    """Convert a byte count to tebibytes (TiB)."""
    if bytes_value < 0:
        raise ValueError(f"bytes_value must be >= 0, got {bytes_value}")
    return bytes_value / _BYTES_PER_TIB


def estimate_bigquery_cost_usd(
    bytes_processed: int, usd_per_tib: float = DEFAULT_USD_PER_TIB
) -> float:
    """
    Estimate the on-demand BigQuery cost for a scan of ``bytes_processed``.

    Conservative by design: the free tier is intentionally ignored so the
    per-query guardrail never under-estimates cost.

    Raises:
        ValueError: ``bytes_processed`` is negative or ``usd_per_tib`` is
            negative.
    """
    if bytes_processed < 0:
        raise ValueError(f"bytes_processed must be >= 0, got {bytes_processed}")
    if usd_per_tib < 0:
        raise ValueError(f"usd_per_tib must be >= 0, got {usd_per_tib}")
    return bytes_to_tib(bytes_processed) * usd_per_tib

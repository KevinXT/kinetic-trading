"""A cost-guarded BigQuery client.

:class:`SafeBigQueryClient` always dry-runs first to estimate scanned bytes,
always sets ``maximum_bytes_billed``, enforces the shared
:class:`kinetic.ingestion.cost.CostPolicy`, checks a local result cache before
touching the cloud, requires a typed confirmation value before real execution,
and logs every decision to the cost ledger.

``google-cloud-bigquery`` is imported lazily, so importing this package never
requires the dependency or credentials.
"""

from kinetic.ingestion.warehouse.bigquery.cache import (
    bigquery_cache_path,
    build_cache_key,
    load_cached_rows,
    write_cached_rows,
)
from kinetic.ingestion.warehouse.bigquery.client import (
    BigQueryEstimate,
    SafeBigQueryClient,
    SafeQueryDecision,
    SafeQueryResult,
)
from kinetic.ingestion.warehouse.bigquery.sql_guardrails import validate_bigquery_sql

__all__ = [
    "BigQueryEstimate",
    "SafeBigQueryClient",
    "SafeQueryDecision",
    "SafeQueryResult",
    "bigquery_cache_path",
    "build_cache_key",
    "load_cached_rows",
    "validate_bigquery_sql",
    "write_cached_rows",
]

"""
Cost-aware BigQuery provider path for historical GDELT measurement.

BigQuery is used for *broad historical measurement* (daily topic counts,
baselines, spike detection). The GDELT DOC/artlist flow elsewhere in
``news_data`` remains the tool for *zooming in* on specific article evidence.

Everything in this subpackage routes real execution through
:class:`SafeBigQueryClient`, which:

  - always dry-runs first to estimate scanned bytes,
  - always sets ``maximum_bytes_billed``,
  - enforces the shared :class:`common.cost.CostPolicy`,
  - checks a local cache before touching the cloud,
  - requires a typed confirmation value before real execution,
  - and logs every decision to the cost ledger.

``google-cloud-bigquery`` is imported lazily so importing this package never
requires the dependency or credentials.
"""

from .cache import bigquery_cache_path, build_cache_key, load_cached_rows, write_cached_rows
from .client import (
    BigQueryEstimate,
    SafeBigQueryClient,
    SafeQueryDecision,
    SafeQueryResult,
)
from .gdelt_queries import (
    DEFAULT_THEME_SEARCH_PATTERNS,
    QUERY_BUILDER_VERSION,
    build_gdelt_daily_counts_query,
    build_gdelt_theme_discovery_query,
)
from .normalize_counts import normalize_gdelt_daily_counts
from .sql_guardrails import validate_bigquery_sql
from .theme_bundles import (
    DEFAULT_THEME_BUNDLES_PATH,
    get_bundle_themes,
    load_theme_bundles,
    resolve_query_terms,
)

__all__ = [
    "BigQueryEstimate",
    "SafeBigQueryClient",
    "SafeQueryDecision",
    "SafeQueryResult",
    "validate_bigquery_sql",
    "build_gdelt_daily_counts_query",
    "build_gdelt_theme_discovery_query",
    "QUERY_BUILDER_VERSION",
    "DEFAULT_THEME_SEARCH_PATTERNS",
    "normalize_gdelt_daily_counts",
    "build_cache_key",
    "bigquery_cache_path",
    "load_cached_rows",
    "write_cached_rows",
    "load_theme_bundles",
    "get_bundle_themes",
    "resolve_query_terms",
    "DEFAULT_THEME_BUNDLES_PATH",
]

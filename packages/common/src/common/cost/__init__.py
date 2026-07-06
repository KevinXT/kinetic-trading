"""
Cost-awareness primitives shared across the monorepo.

This subpackage provides provider-agnostic building blocks for keeping cloud
query spend under control:

  - ``policy``   : load + validate a :class:`CostPolicy` from YAML
  - ``estimate`` : convert BigQuery bytes-processed into a conservative USD cost
  - ``ledger``   : append-only JSONL record of every cost decision

Provider-specific execution (e.g. the BigQuery client) lives in the relevant
provider package and depends on these primitives.
"""

from .estimate import (
    bytes_to_gib,
    bytes_to_tib,
    estimate_bigquery_cost_usd,
)
from .ledger import CostLedger, CostSummary
from .policy import CostPolicy, load_cost_policy
from .report import format_cost_report

__all__ = [
    "CostPolicy",
    "load_cost_policy",
    "bytes_to_gib",
    "bytes_to_tib",
    "estimate_bigquery_cost_usd",
    "CostLedger",
    "CostSummary",
    "format_cost_report",
]

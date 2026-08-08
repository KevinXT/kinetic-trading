"""
Reporting / analytics layer for Kinetic Trading.

Builds dashboard-ready BigQuery reporting views over the GDELT GKG data for a
Looker Studio dashboard. See :mod:`kinetic.ingestion.warehouse.bigquery.reporting.build_views` for the
CLI runner and :mod:`kinetic.ingestion.warehouse.bigquery.reporting.views` for the view registry.
"""

from __future__ import annotations

from .views import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_TOP_N,
    REPORTING_VIEWS,
    ReportingView,
    build_create_view_statement,
    discover_sql_files,
    get_reporting_views,
    get_view,
    qualified_view_id,
    render_view_sql,
)

__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_TOP_N",
    "REPORTING_VIEWS",
    "ReportingView",
    "build_create_view_statement",
    "discover_sql_files",
    "get_reporting_views",
    "get_view",
    "qualified_view_id",
    "render_view_sql",
]

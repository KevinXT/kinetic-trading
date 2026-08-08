"""GDELT accessed through BigQuery.

The GDELT DOC API (:mod:`kinetic.ingestion.news.gdelt.client`) is the tool for
*zooming in* on specific article evidence. This path is for *broad historical
measurement* — daily topic counts, baselines, theme discovery and seeded-vs-
background theme scoring across multi-year windows.

The generic, cost-guarded BigQuery client it runs on lives in
:mod:`kinetic.ingestion.warehouse.bigquery`; only the GDELT-specific SQL,
normalization and tasks live here.
"""

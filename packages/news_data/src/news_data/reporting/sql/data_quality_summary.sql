-- Reporting view: dashboard-ready data quality summary (single row).
--
-- Purpose: surface headline data-quality metrics for the rolling window so the
-- dashboard can show scorecards: total rows, rows missing a required field
-- (SourceCommonName), duplicate record ids, and the latest record timestamp.
--
-- The window is bounded by _PARTITIONTIME so the check stays cost-safe.
--
-- GKG DATE is a 14-digit YYYYMMDDHHMMSS integer, parsed here into a TIMESTAMP so
-- the dashboard can show how fresh the underlying data is.
SELECT
  CURRENT_DATE() AS check_date,
  COUNT(*) AS total_rows,
  COUNTIF(SourceCommonName IS NULL OR SourceCommonName = '') AS missing_required_field_count,
  COUNT(*) - COUNT(DISTINCT GKGRECORDID) AS duplicate_count,
  MAX(PARSE_TIMESTAMP('%Y%m%d%H%M%S', CAST(DATE AS STRING))) AS latest_record_timestamp
FROM `{{ source_table }}`
WHERE _PARTITIONTIME >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {{ lookback_days }} DAY)
  AND DATE > 0

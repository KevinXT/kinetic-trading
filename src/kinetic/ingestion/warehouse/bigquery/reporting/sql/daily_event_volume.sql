-- Reporting view: daily GDELT/news record volume.
--
-- Purpose: one row per calendar day with the number of GDELT GKG records, for a
-- dashboard "article/event volume over time" trend chart.
--
-- The window is bounded by _PARTITIONTIME so Looker Studio only ever scans the
-- rolling lookback window (cost-safe), not the entire multi-TiB GKG table.
--
-- GKG DATE is a 14-digit YYYYMMDDHHMMSS integer, so DIV(DATE, 1000000) yields
-- the 8-digit YYYYMMDD calendar day used as event_date.
SELECT
  PARSE_DATE('%Y%m%d', CAST(DIV(DATE, 1000000) AS STRING)) AS event_date,
  COUNT(*) AS record_count
FROM `{{ source_table }}`
WHERE _PARTITIONTIME >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {{ lookback_days }} DAY)
  AND DATE > 0
GROUP BY event_date
ORDER BY event_date

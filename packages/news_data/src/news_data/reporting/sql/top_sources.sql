-- Reporting view: top news sources / domains by record volume.
--
-- Purpose: rank the most active source domains (SourceCommonName) in the rolling
-- window, for a dashboard "top sources" bar chart.
--
-- The window is bounded by _PARTITIONTIME so the dashboard scans only the rolling
-- lookback window. Blank/NULL domains are excluded so the chart is meaningful.
SELECT
  SourceCommonName AS source_domain,
  COUNT(*) AS record_count
FROM `{{ source_table }}`
WHERE _PARTITIONTIME >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {{ lookback_days }} DAY)
  AND SourceCommonName IS NOT NULL
  AND SourceCommonName != ''
GROUP BY source_domain
ORDER BY record_count DESC
LIMIT {{ top_n }}

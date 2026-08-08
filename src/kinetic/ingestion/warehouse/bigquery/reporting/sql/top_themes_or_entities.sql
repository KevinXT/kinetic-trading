-- Reporting view: most frequent GDELT themes.
--
-- Purpose: rank the most common theme codes in the rolling window, for a
-- dashboard "top themes / entities" chart.
--
-- V2Themes is not a scalar field: each row holds a semicolon-delimited list of
-- "THEME_CODE,charOffset" entries. It is normalized the way GDELT recommends --
-- strip the numeric offsets, trim the trailing delimiter, then split on the
-- delimiter -- so each whole theme code can be counted.
--
-- The window is bounded by _PARTITIONTIME so the dashboard stays cost-safe.
SELECT
  theme AS entity_or_theme,
  COUNT(*) AS record_count
FROM `{{ source_table }}`,
  UNNEST(SPLIT(RTRIM(REGEXP_REPLACE(V2Themes, r',\d+;', ';'), ';'), ';')) AS theme
WHERE _PARTITIONTIME >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {{ lookback_days }} DAY)
  AND LENGTH(V2Themes) > 1
  AND theme != ''
GROUP BY entity_or_theme
ORDER BY record_count DESC
LIMIT {{ top_n }}

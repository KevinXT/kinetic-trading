# Looker Studio setup (BI / reporting layer)

This document describes how to create a Looker Studio dashboard from the
reporting views. The Python reporting layer under
`kinetic.ingestion.warehouse.bigquery.reporting` remains available for BigQuery view
creation and optional JSON export; a managed Looker Studio report can be added
separately from the steps below.

This project ships a lightweight analytics/reporting layer that turns the
GDELT GKG data into a small set of **Looker Studio-ready BigQuery views**. The
views can be connected to [Looker Studio](https://lookerstudio.google.com/) —
this guide covers that optional setup.

The layer lives in `src/kinetic/ingestion/warehouse/bigquery/reporting/`:

```text
reporting/
  views.py          View registry, {{ }} SQL template renderer, CREATE VIEW builder
  build_views.py    CLI runner: validate / dry-run estimate / create views
  sql/
    daily_event_volume.sql
    top_sources.sql
    top_themes_or_entities.sql
    data_quality_summary.sql
```

Everything reuses the existing cost-aware BigQuery path: the same
`SafeBigQueryClient`, SQL guardrails, cost policy, and cost ledger used by the
research pipeline. Nothing is created in BigQuery unless you explicitly ask for
it and provide a typed confirmation.

---

## Reporting views

All four views read from `providers.bigquery.table`
(`gdelt-bq.gdeltv2.gkg_partitioned` by default) and are **bounded by a rolling
`_PARTITIONTIME` window** (`reporting.lookback_days`, default 30). This keeps
every dashboard query cheap — Looker Studio only ever scans the recent window,
never the whole multi-TiB GKG table.


| View                     | Purpose                                     | Output columns                                                                                           |
| ------------------------ | ------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `daily_event_volume`     | Records per calendar day (volume trend).    | `event_date`, `record_count`                                                                             |
| `top_sources`            | Most active source domains.                 | `source_domain`, `record_count`                                                                          |
| `top_themes_or_entities` | Most frequent normalized GDELT theme codes. | `entity_or_theme`, `record_count`                                                                        |
| `data_quality_summary`   | Single-row data-quality scorecard metrics.  | `check_date`, `total_rows`, `missing_required_field_count`, `duplicate_count`, `latest_record_timestamp` |


Assumptions / notes:

- `event_date` is derived from GKG `DATE` (a 14-digit `YYYYMMDDHHMMSS` integer)
via `DIV(DATE, 1000000)` → `YYYYMMDD`.
- `source_domain` uses `SourceCommonName` (the GKG source/domain field). Blank
and `NULL` domains are excluded.
- `entity_or_theme` normalizes `V2Themes` the way GDELT recommends (strip the
numeric offsets, trim the trailing delimiter, split on the delimiter) so whole
theme codes are counted. Swapping the theme column for `V2Organizations`,
`V2Persons`, or `V2Locations` would produce entity views with the same shape.
- `data_quality_summary` treats `SourceCommonName` as the required field,
measures duplicates via `COUNT(*) - COUNT(DISTINCT GKGRECORDID)`, and reports
the latest record timestamp parsed from `DATE`.

---

## Running the runner

The runner defaults to **dry-run** (validate + estimate only). It never creates
anything unless `--create` is passed *and* a typed confirmation is present.

```bash
# Local validation only (render + SQL guardrails, no BigQuery call):
python -m kinetic.ingestion.warehouse.bigquery.reporting.build_views --no-estimate

# Dry-run validation + BigQuery cost estimate per view (no data, no billable query):
python -m kinetic.ingestion.warehouse.bigquery.reporting.build_views --dry-run

# Create / replace the views in BigQuery (requires confirmation, see below):
python -m kinetic.ingestion.warehouse.bigquery.reporting.build_views --create --yes
```

Useful overrides: `--project`, `--dataset`, `--source-table`,
`--lookback-days`, `--top-n`, `--config`.

### Data-validation checks performed

For every view, the runner:

1. renders the SQL template and rejects unknown placeholders,
2. runs it through `validate_bigquery_sql` — enforces no `SELECT *`, a bounded
  `WHERE` with a `_PARTITIONTIME` date filter, single-statement, no DDL/DML,
   and that it references the allowed source table (so the view is queryable),
3. takes a BigQuery dry-run estimate (bytes scanned / cost) and logs a
  `DRY_RUN_ONLY` decision to the cost ledger,
4. only on `--create`, issues `CREATE OR REPLACE VIEW`.

The `data_quality_summary` view itself covers the data-quality checks the task
asks for at query time: row counts (> 0), a required field that is not entirely
null, a reasonable latest timestamp, and duplicate detection.

---

## Creating the views in BigQuery

Prerequisites:

```bash
pip install "google-cloud-bigquery>=3.0"     # if not already installed
# and have application-default credentials configured (gcloud auth application-default login)
```

1. Put your real project id in `configs/local.yaml` (git-ignored, deep-merged
  at runtime) or pass `--project`:
2. Create the destination dataset once:
  ```bash
   bq mk --location=US your-gcp-project:kinetic_reporting
  ```
3. Estimate first, then create:
  ```bash
   python -m kinetic.ingestion.warehouse.bigquery.reporting.build_views --dry-run
   python -m kinetic.ingestion.warehouse.bigquery.reporting.build_views --create --yes
  ```
   Instead of `--yes`, you can set `reporting.cost_controls.execute_query: "ENABLE"`
   in `configs/reporting.yaml` (mirrors the pipeline's typed-confirmation pattern).

The views are created as `your-gcp-project.kinetic_reporting.<view_name>`.

---

## Connecting to Looker Studio

1. Go to [Looker Studio](https://lookerstudio.google.com/) → **Create** →
  **Data source** → **BigQuery**.
2. Select your project → `kinetic_reporting` dataset → pick a reporting view
  (e.g. `daily_event_volume`). Add one data source per view.
3. **Create report** and add charts (below). Because each view is a small,
  pre-aggregated result set, dashboard refreshes stay fast and cheap.
4. Optional: add a report-level **date range control** and a **source** filter
  control so viewers can slice the data.

### Suggested dashboard charts


| Chart                            | Data source              | Config                                                                       |
| -------------------------------- | ------------------------ | ---------------------------------------------------------------------------- |
| Daily article/event volume trend | `daily_event_volume`     | Time series — dimension `event_date`, metric `record_count`                  |
| Top sources / domains            | `top_sources`            | Bar chart — dimension `source_domain`, metric `record_count`                 |
| Top themes / entities            | `top_themes_or_entities` | Bar chart or table — dimension `entity_or_theme`, metric `record_count`      |
| Data quality summary card        | `data_quality_summary`   | Scorecards — `total_rows`, `missing_required_field_count`, `duplicate_count` |
| Latest refresh timestamp         | `data_quality_summary`   | Scorecard — `latest_record_timestamp`                                        |
| Missing-field count              | `data_quality_summary`   | Scorecard — `missing_required_field_count` (add a threshold color)           |


Recommended filters/controls: date-range control, a `source_domain` filter, and
a `top_n` you can tune by re-running the runner with `--top-n`.

---


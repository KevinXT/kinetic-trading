# Kinetic Trading — GDELT Intelligence Dashboard

A local BI dashboard preview for the BigQuery reporting layer
(`news_data.reporting`) — a credential-free, dependency-free page. It renders KPI
cards, a daily-volume chart, top-source and top-theme bars, and a data-quality
panel from committed **sample JSON** that matches the output schemas of the
BigQuery reporting views.

> Subtitle: *Sample dashboard using schemas that match the Looker Studio-ready
> BigQuery reporting views: source coverage, event volume, entity/theme trends,
> and data quality checks.*

This is a **local HTML/CSS/JS dashboard** — no build step, no `npm install`, no
external libraries. Charts are drawn with inline SVG/CSS so it works fully
offline. It is a preview of the reporting layer; connecting the same views to
Looker Studio is a separate, optional deployment path.

## Run it locally

Browsers block `fetch` from `file://`, so serve the folder over HTTP (Python is
already required by this repo — no extra tooling):

```bash
cd apps/reporting_dashboard
python3 -m http.server 8000
# then open http://localhost:8000
```

That's it — the page loads the committed sample data and renders immediately.

## Data files

The dashboard reads four JSON files from `public/sample_data/`. Each is a JSON
array of row objects whose keys are exactly the output-column aliases of the
matching BigQuery reporting view:


| File                          | Reporting view           | Columns                                                                                                  |
| ----------------------------- | ------------------------ | -------------------------------------------------------------------------------------------------------- |
| `daily_event_volume.json`     | `daily_event_volume`     | `event_date`, `record_count`                                                                             |
| `top_sources.json`            | `top_sources`            | `source_domain`, `record_count`                                                                          |
| `top_themes_or_entities.json` | `top_themes_or_entities` | `entity_or_theme`, `record_count`                                                                        |
| `data_quality_summary.json`   | `data_quality_summary`   | `check_date`, `total_rows`, `missing_required_field_count`, `duplicate_count`, `latest_record_timestamp` |


The committed files are **realistic sample/demo data**, not live GDELT output.
The UI labels this with a "Sample / demo data" badge. The shapes are validated
against the reporting-view aliases by `tests/test_reporting_dashboard_data.py`.

## Replacing sample data with real exported data

The reporting layer ships an optional exporter that writes the *real* view
results into this same JSON format, reusing the cost-aware BigQuery path
(`SafeBigQueryClient`, guardrails, cost policy + ledger). It is dry-run by
default and writes nothing without a typed confirmation:

```bash
# Estimate only (writes nothing):
python -m news_data.reporting.export_dashboard_data --dry-run

# Fetch real rows and overwrite the JSON files in public/sample_data/:
python -m news_data.reporting.export_dashboard_data --execute --yes
```

Requires a real `providers.bigquery.project_id` (via `configs/local.yaml` or  
`--project`) and BigQuery credentials. See  
`[docs/looker_studio_dashboard.md](../../docs/looker_studio_dashboard.md)` for  
the reporting views and optional Looker Studio setup guidance.


# kinetic-trading

[![CI](https://github.com/KevinXT/kinetic-trading/actions/workflows/ci.yml/badge.svg)](https://github.com/KevinXT/kinetic-trading/actions/workflows/ci.yml)

A modular, YAML-driven research pipeline for financial/news data ingestion, caching, normalization, transformation, and future strategy experimentation.

This project is an early-stage trading research platform. The current implementation focuses on the execution engine: a reusable pipeline core, config system, and task registry; two complementary GDELT paths (DOC API for recent articles, BigQuery for historical measurement); normalized article records with enrichment transforms; cache-aware provider calls; cost-aware cloud query guardrails; and reproducible run artifacts.

The goal is not to be a finished trading bot. The goal is to build the infrastructure layer that future market-data providers, signal-generation tasks, backtests, and strategy modules can plug into cleanly.

---
## Current status

Implemented today:

**Pipeline engine**

- YAML-driven pipeline execution with recursive `include:` loading and deep merge
- Git-ignored local overrides via `configs/local.yaml` (deep-merged at runtime over any selected config)
- Linear pipeline runner with lifecycle hooks, failure metadata, and timing
- Task registry for pluggable pipeline steps (10 registered tasks)
- `RunContext` for shared runtime state and artifact writing
- Experiment/run folder allocation, resolved-config snapshots, and run metadata

**GDELT DOC / artlist path** (recent headlines, article evidence)

- GDELT DOC API client and `gdelt_docs` ingestion task
- Cache-aside API response caching (`.cache/<namespace>/`)
- Article normalization with idempotent `ingested_at` preservation
- Filter, dedupe (`url` / `title` / `title_domain`), tag, aggregate, and store transforms
- Duplicate/syndication group metadata and deterministic 25-category topic tagging
- Daily topic-level feature aggregation with amplification metrics
- Persistent article and feature stores with cross-run deduplication
- 25 collection configs (macro, sectors, risk, markets) and batch collection runner

**BigQuery GDELT history path** (broad historical measurement)

- Cost-aware BigQuery provider: `SafeBigQueryClient`, SQL guardrails, dry-run-first execution
- Cost policy (`configs/cost_policy.yaml`), append-only cost ledger, and `cost-report` CLI
- Local BigQuery result cache (`data/cache/bigquery_gdelt_counts/`) with builder-version invalidation
- Daily-count task (`bigquery_gdelt_counts`) with configurable `search_columns`, `query_terms`, or `theme_bundle`
- Theme discovery / debug task (`bigquery_gdelt_theme_discovery`) — lists actual GDELT theme codes in a window
- Theme bundles (`configs/gdelt_theme_bundles.yaml`) mapping research topics to sets of GDELT theme codes
- Partition pruning via `_PARTITIONTIME`, 14-digit GKG `DATE` bounds (`YYYYMMDDHHMMSS`), normalized `V2Themes` matching
- Partitioned research configs under `configs/research/` (dry-run and execute variants; legacy unpartitioned configs quarantined)

**BI / reporting layer** (Looker Studio dashboard)

- Dashboard-ready BigQuery reporting views over the GDELT GKG data: daily event volume, top sources, top themes/entities, and a data-quality summary
- `build_views` CLI runner: dry-run validation + cost estimate by default, gated `--create` to create/replace views (reuses `SafeBigQueryClient`, guardrails, cost policy/ledger)
- Rolling `_PARTITIONTIME` window keeps every dashboard query cheap; see [`docs/looker_studio_dashboard.md`](docs/looker_studio_dashboard.md)

**Quality**

- **421 tests** across config loading, pipeline engine, GDELT DOC transforms, BigQuery SQL/cost/cache/guardrails, theme discovery/bundles, local overrides, collection runner, and the reporting layer

Planned / placeholder boundaries:

- Market data providers (`market_data` package stub)
- Strategy SDK abstractions (`strategy_sdk` package stub)
- Sentiment / entity extraction transforms
- Backtesting integration
- UI / dashboard layer

---

## Why this architecture exists

The pipeline engine is intentionally provider-agnostic.

`pipeline_core` does not know what GDELT is, how articles are normalized, or how market data will be fetched later. It only knows how to:

1. load a config (with optional local overrides),
2. parse pipeline steps,
3. look up task functions,
4. execute them in order,
5. write metadata and artifacts.

Provider-specific logic lives in provider packages like `news_data` — both the GDELT DOC client and the BigQuery historical path. Downstream transforms operate on normalized internal records instead of raw provider schemas. Shared cost primitives (`common.cost`) keep cloud query spend under control without coupling the engine to any one provider.

---

## Pipeline flow

Every run follows the same engine path; the **ingest source** determines which data provider executes:

```text
YAML config (+ optional configs/local.yaml merge)
    ↓
load_runtime_config → plan parser → pipeline runner → task registry
    ↓
ingest task (one of three)
```

### Path A — GDELT DOC / artlist (recent articles)

```text
gdelt_docs
    ↓
cache-aside fetch → raw response → normalize
    ↓
filter_articles → dedupe_articles → tag_articles
    ↓
aggregate_article_features → store_features → store_articles
    ↓
data/processed/{articles,features}/*.jsonl + run artifacts
```

Used by `configs/demo.yaml` and all 25 `configs/collections/*` configs.

### Path B — BigQuery daily counts (historical measurement)

```text
bigquery_gdelt_counts
    ↓
build SQL (partition prune + 14-digit DATE + theme match)
    ↓
SafeBigQueryClient: guardrails → cache check → dry-run → cost caps → execute?
    ↓
bigquery_daily_counts.{jsonl,csv} → ctx.state["topic_daily_features"]
```

Used by `configs/research/inflation_rates_bigquery_*` configs. Supports direct `query_terms` or a named `theme_bundle`.

### Path C — BigQuery theme discovery (debug / research)

```text
bigquery_gdelt_theme_discovery
    ↓
same SafeBigQueryClient cost path as Path B
    ↓
theme_discovery.{csv,jsonl}  (which theme codes actually appear?)
```

Used by `configs/research/debug_gdelt_themes_inflation_30d_partition_*`. Discovery configs default to `use_cache: false` so stale cached results cannot mask current SQL behavior.

---

## Example pipeline

`configs/demo.yaml`

```yaml
name: demo

providers:
  news:
    gdelt:
      timeout_s: 30
      doc:
        base_url: "https://api.gdeltproject.org/api/v2/doc/doc"
        mode: "artlist"
        format: "json"
        timespan: "24h"
        maxrecords: 10
        sort: "datedesc"

pipeline:
  ingest:
    source: gdelt_docs
    query: "artificial intelligence"
    max_records: 10

  transform:
    - type: filter_articles
      language: "English"
      source_country: "United States"
    - type: dedupe_articles
      by: ["url", "title"]
    - type: tag_articles
    - type: aggregate_article_features
    - type: store_features
    - type: store_articles
      output_path: "data/processed/articles/articles.jsonl"
```

Run it with:

```bash
python3 -m trading_platform configs/demo.yaml
```

Example output:

```text
completed run: demo
run_id: <generated-run-id>
outputs: experiments/demo/<generated-run-id>
```

---

## Collection configs

25 GDELT collection configs are organized under `configs/collections/` across four categories:

```text
configs/collections/
  macro/               inflation, labor, GDP, housing, spending, debt
  sectors/             AI, semiconductors, energy, financials, healthcare, defense, EVs, crypto
  risk/                geopolitics, banking, supply chain, regulation, cyber, climate
  markets/             equities, bonds, commodities, China, currencies
```

Each config uses exact-phrase GDELT queries wrapped in parentheses, with category-specific timespans:

| Category | Timespan | Rationale |
|----------|----------|-----------|
| `macro/` | 48h | Slower-moving macro trends benefit from broader windows |
| `sectors/` | 48h | Sector-level coverage evolves over days, not hours |
| `markets/` | 24h | Fast-moving market data stays tighter |
| `risk/` | 12h | Risk/geopolitics can explode with noise; shorter windows help |

All collection configs use `maxrecords: 50`, `dedupe_strategy: "title"`, and run the full enrichment pipeline: filter → dedupe → tag → aggregate → store features → store articles.

---

## Example artifacts

Each run writes a reproducible output folder:

```text
experiments/<collection-name>/<run-id>/
  config_resolved.yaml
  run_metadata.json
  artifacts/
    gdelt_response_raw.json
    gdelt_articles.jsonl
    normalized_articles.jsonl
    filtered_articles.jsonl
    deduped_articles.jsonl
    duplicate_groups.jsonl
    tagged_articles.jsonl
    article_features_daily.jsonl
    gdelt_summary.json
    filter_summary.json
    dedupe_summary.json
    tag_summary.json
    article_features_summary.json
    store_features_summary.json
    store_summary.json
```

Runtime artifacts are intentionally gitignored. The repository should contain code, configs, tests, and documentation — not large generated datasets.

---

## Topic tagging

The `tag_articles` task enriches each deduplicated article with deterministic topic labels. It matches article titles, queries, and domains against a configurable keyword rule set covering 25 research categories (one per collection config):

```text
inflation_rates, labor_jobs, growth_recession, housing_real_estate,
consumer_spending, government_debt, ai, semiconductors, energy,
financials, healthcare_biotech, defense_aerospace, autos_ev,
crypto_fintech, geopolitics, banking_credit, supply_chain,
regulation_antitrust, cybersecurity, climate_disasters, equities,
bonds_yields, commodities, china, currencies_dollar
```

Each article receives three enrichment fields:

- `topics` — list of all matching categories
- `topic_matches` — dict mapping each matched category to the keywords that triggered it
- `primary_topic` — the first matched category, or `None` if no match

Matching is case-insensitive and supports multi-word phrases. Articles can be tagged with multiple topics simultaneously — a story about "Federal Reserve policy impacts gold prices and bond yields" matches `inflation_rates`, `commodities`, and `bonds_yields`.

Custom rules can be passed via the YAML config to override the defaults for specialized collection jobs.

---

## Feature aggregation

The `aggregate_article_features` task collapses tagged article records into daily topic-level feature rows — one row per (date, topic) pair. Each feature row captures:

- **Volume**: `article_count` — how many articles matched the topic on that date
- **Breadth**: `unique_domains`, `unique_source_countries` — how widely the story has spread
- **Timing**: `earliest_published_at`, `latest_published_at` — the publication window
- **Amplification** (when duplicate groups exist): `duplicate_groups`, `duplicate_articles_removed`, `max_group_total_seen` — syndication intensity from the dedupe step
- **Composition**: `primary_topic_count`, `untagged_count`, `queries`, sorted `domains` and `source_countries`

Articles with multiple topics fan out into multiple rows, naturally capturing cross-topic co-occurrence. Output rows are deterministically sorted by (date, topic) for stable consumption by downstream models.

---

## Deduplication and syndication tracking

The `dedupe_articles` task supports three strategies:

- `url` — dedupe by normalized URL only
- `title` — dedupe by normalized title (default; collapses syndicated copies across domains)
- `title_domain` — dedupe by title + domain pair

Collection configs use the `title` strategy because news wire services (Reuters, AP, AFP) distribute identical articles to hundreds of outlets. GDELT indexes each outlet's copy with a unique URL, but the title is the same. Title-based deduplication collapses these syndicated copies into a single canonical record.

Duplicate group metadata is written as a run artifact (`duplicate_groups.jsonl`) recording which domains carried each story, how many copies were removed, and the canonical article chosen. This preserves media-amplification data for future signal generation without polluting the primary article store.

---

## Processed data layout

```text
data/
  processed/
    articles/
      articles.jsonl                      Canonical unique articles (cross-run deduplicated)
    features/
      article_features_daily.jsonl        Historical daily topic-level feature rows
    metadata/                             Placeholder for future dataset metadata
  cost/
    cost_ledger.jsonl                     Append-only BigQuery cost decisions (gitignored)
  cache/
    bigquery_gdelt_counts/                Local BigQuery result cache (gitignored)
.cache/                                   GDELT DOC API response cache (gitignored)
experiments/                              Per-run artifacts (gitignored)
```

The `data/processed/` directory is gitignored. Both the article store and the feature store grow incrementally as collection jobs run — `store_articles` and `store_features` each append only records not already present, using deterministic dedupe keys to prevent duplicate rows across runs.

The feature store uses a composite key of `date|topic|earliest_published_at|latest_published_at` to distinguish feature rows. Re-running the same config on the same GDELT data window produces zero new rows; genuinely new data windows append cleanly.

---

## Cache-aside provider fetches

The GDELT ingestion task uses a reusable cache helper from `common.cache`.

```text
request parameters
    ↓
deterministic SHA256 cache key
    ↓
.cache/<namespace>/<key>.json
    ↓
cache hit: load local response
cache miss: call provider API, save response, continue
```

This allows repeated local experimentation on normalization and transform logic without repeatedly hitting the external API or triggering rate limits.

The cache layer is generic and can later be reused for market data, sentiment results, LLM summaries, or other expensive provider calls.

---

## Cost-aware BigQuery GDELT history

Kinetic Trading uses two complementary GDELT paths:

- **BigQuery** is for *broad historical measurement* — daily topic counts, 30-day / 1-year baselines, spike detection, and historical feature rows.
- **GDELT DOC API / artlist** (the existing `gdelt_docs` flow) is for *zooming in* — sampling headlines and article evidence on specific spike days.
- The **local feature store** is the research memory (`topic_daily_features`, and later trends / research packets).

Because BigQuery bills by bytes scanned, this path is cost-aware by construction:

- **Dry-run is the default.** A run estimates scanned bytes/cost and logs a decision; it fetches no data.
- **Real execution requires typed confirmation** — `execute_query: "ENABLE"` in the config's `cost_controls`.
- **`maximum_bytes_billed` is always enforced** on every query (even real execution dry-runs first).
- **Cache before cloud** — a cached result skips both the dry-run and the billable query. Dry-run-only runs never write a result cache.
- **Every decision is written to `data/cost/cost_ledger.jsonl`** (dry-run, blocked, cache hit, or executed), with caps defined in `configs/cost_policy.yaml` and enforced in code.
- **Scan only the columns you need.** BigQuery bills by bytes scanned, and the columns matched against `query_terms` are configurable via `search_columns`. This defaults to `["V2Themes"]` — the compact GDELT theme-code column. Broad free-text columns such as `AllNames` are much larger and can dramatically increase bytes scanned (and cost). Start with `V2Themes` / theme codes (e.g. `ECON_INFLATION`) and only add wider columns once a dry-run estimate shows the extra scan is affordable.

Keeping the scan narrow is what makes a wide time window affordable. For example, a 30-day window scanning multiple broad text columns can estimate well over 2 TiB (and be blocked by `BLOCK_OVER_MAX_BYTES`), whereas matching a theme code against `V2Themes` alone scans a small fraction of that:

```yaml
pipeline:
  ingest:
    search_columns:
      - V2Themes
    query_terms:
      - ECON_INFLATION
```

**Theme-code matching uses GDELT's normalized `UNNEST`.** `V2Themes` (and `Themes`) are not scalar fields — each row holds a semicolon-delimited list of `CODE,charOffset` entries (e.g. `ECON_INFLATION,1234;TAX_FNCACT_PRESIDENT,5678`). An exact-equality or overly strict match returns zero rows even when the theme is present, and a raw `REGEXP_CONTAINS` boundary match can still be brittle. For these columns the builder normalizes the field the way GDELT recommends — strip the numeric offsets, drop the trailing delimiter, and split on `;` — then matches whole theme codes with `theme IN (...)`:

```sql
EXISTS (
  SELECT 1
  FROM UNNEST(SPLIT(RTRIM(REGEXP_REPLACE(V2Themes, r',\d+;', ';'), ';'), ';')) AS theme
  WHERE theme IN ('ECON_INFLATION', 'ECON_INTEREST_RATES')
)
```

Other (free-text) columns like `AllNames` still use a case-insensitive substring `LIKE`. Theme codes are SQL-escaped (quotes doubled, backslashes dropped) so they can't inject into the query. The SQL builder is currently at **v4** (14-digit GKG `DATE` bounds + normalized-`UNNEST` theme matching); bumping the builder version invalidates stale cache entries.

### Partition pruning is required (`_PARTITIONTIME`)

**Every normal BigQuery GDELT config must enable `_PARTITIONTIME` partition pruning.** On `gdelt-bq.gdeltv2.gkg_partitioned` the `DATE` column is an ordinary integer field, so a `DATE BETWEEN` filter alone does **not** prune partitions — BigQuery still scans the *entire* table. The table is partitioned by `_PARTITIONTIME`, so constraining that pseudo-column is what actually limits bytes scanned to just the requested days. `DATE BETWEEN` is retained for logical correctness, but `_PARTITIONTIME` is what delivers the cost reduction.

**GKG `DATE` is a 14-digit datetime integer (`YYYYMMDDHHMMSS`), not 8-digit `YYYYMMDD`.** Real GKG `DATE` values look like `20260501000000`, `20260530123456`, `20260530235959`. Filtering with 8-digit bounds (`DATE BETWEEN 20260501 AND 20260530`) matches **zero rows** because every real value is larger than `20260530`. The builders therefore emit start-of-day / end-of-day 14-digit bounds — `20260501000000` … `20260530235959` — covering the whole requested range. The correct shape is:

```sql
WHERE _PARTITIONTIME >= TIMESTAMP("2026-05-01")
  AND _PARTITIONTIME < TIMESTAMP("2026-05-31")
  AND DATE BETWEEN 20260501000000 AND 20260530235959
```

The difference is dramatic for the same 7-day query:

| Query | Filter | Estimated scan |
| --- | --- | --- |
| Unpartitioned (legacy) | `DATE BETWEEN` only | ~1.73 TiB |
| Partitioned (normal) | `_PARTITIONTIME` + `DATE BETWEEN` | ~2.05 GiB |

Enable it with `partition_filter` (the logical `DATE BETWEEN` filter is kept too):

```yaml
pipeline:
  ingest:
    partition_filter:
      enabled: true
      column: "_PARTITIONTIME"   # use _PARTITIONDATE with type: date if applicable
      type: "timestamp"
```

This generates an exclusive upper bound (`end + 1 day`) so the final day is fully included:

```sql
WHERE _PARTITIONTIME >= TIMESTAMP("2026-05-24")
  AND _PARTITIONTIME < TIMESTAMP("2026-05-31")
  AND DATE BETWEEN 20260524000000 AND 20260530235959
```

Recommended (active) configs — all use `_PARTITIONTIME` pruning:

| Config | Purpose |
| --- | --- |
| `configs/research/inflation_rates_bigquery_1d_partition_dryrun.yaml` | Tiny 1-day debug (cap 2 GB) |
| `configs/research/inflation_rates_bigquery_7d_partition_dryrun.yaml` | 7-day dry-run (cap 5 GB) |
| `configs/research/inflation_rates_bigquery_30d_partition_dryrun.yaml` | 30-day dry-run (cap 12 GB) |
| `configs/research/inflation_rates_bigquery_30d_partition_execute.yaml` | 30-day real execution (cap 12 GB) |
| `configs/research/inflation_rates_bigquery_1y_partition_dryrun.yaml` | 1-year dry-run (cap 50 GB; no execute config yet) |
| `configs/research/debug_gdelt_themes_inflation_30d_partition_dryrun.yaml` | Theme discovery dry-run (`use_cache: false`) |
| `configs/research/debug_gdelt_themes_inflation_30d_partition_execute.yaml` | Theme discovery execute (`use_cache: false`) |

The older `DATE BETWEEN`-only configs have been moved to `configs/research/legacy/` (renamed with `_unpartitioned`). They are kept for reference only and may scan huge amounts of data — do not use them in the normal workflow.

> Prerequisites: install the BigQuery client (`pip install "google-cloud-bigquery>=3.0"`), set `providers.bigquery.project_id` to a real GCP project, and have application-default credentials configured. Without these the path fails fast with a clear message rather than guessing.

### Local config overrides (`configs/local.yaml`)

Public configs use a **placeholder** project id (`project_id: "YOUR_PROJECT_ID"`) so no personal identifier is committed. To run locally without editing the public configs, create a git-ignored `configs/local.yaml` that is **deep-merged over whichever config you pass to the CLI** at runtime:

```bash
cp configs/local.example.yaml configs/local.yaml
```

Then edit `configs/local.yaml` with your real project id:

```yaml
providers:
  bigquery:
    project_id: "kinetic-trading-497922"
```

The merge is recursive and field-by-field: this overrides only `providers.bigquery.project_id` while `providers.bigquery.table` (and everything else) is preserved from the base config. If `configs/local.yaml` is absent, behavior is unchanged.

`configs/local.yaml` (and `configs/*.local.yaml`) are git-ignored. **Never commit credentials, service-account JSON, OAuth tokens, or application-default-credentials (ADC) files** — `configs/local.yaml` is for config values (like a project id) only; real secrets belong in your credential store / ADC, not in the repo.

Commands:

```bash
# Estimate cost only — no data fetched, no billable query.
python3 -m trading_platform configs/research/inflation_rates_bigquery_30d_partition_dryrun.yaml

# Real execution — requires execute_query: "ENABLE" in the config.
python3 -m trading_platform configs/research/inflation_rates_bigquery_30d_partition_execute.yaml

# Inspect estimated spend for the current billing cycle.
python3 -m trading_platform cost-report
```

Example dry-run estimate (from `bigquery_dry_run_estimate.json` / `bigquery_summary.json`):

```text
Estimated bytes: 8.4 GB
Estimated cost: $0.05
Decision: DRY_RUN_ONLY
```

Each BigQuery run writes `bigquery_sql.sql`, `bigquery_dry_run_estimate.json`, `bigquery_cost_decision.json`, and `bigquery_summary.json`. When a query is actually executed (or served from cache), normalized rows are also written to `bigquery_daily_counts.jsonl` / `.csv` and placed in `ctx.state["topic_daily_features"]` for downstream tasks.

### Theme discovery (debug) and theme bundles

**A single theme code is often too narrow.** Picking one code like `ECON_INFLATION` is a guess — it can return zero rows if the code is wrong for the window, and it misses closely related coverage (interest rates, cost of living, central banks). Two complementary features make topic selection transparent and testable:

**1. Theme discovery** answers *"which GDELT theme codes actually appear in this window?"* It uses the same partition pruning, cost policy, ledger, cache, `SafeBigQueryClient`, and SQL guardrails as the daily-count path, but instead of counting articles for a known code it lists the real theme codes seen in the window (with per-theme counts), filtered to codes whose text matches configurable patterns (default: `inflation`, `econ`, `interest`, `cost`, `central_bank`, `prices`). It uses GDELT's recommended normalization:

```sql
WITH nested AS (
  SELECT SPLIT(RTRIM(REGEXP_REPLACE(V2Themes, r',\d+;', ';'), ';'), ';') AS themes
  FROM `gdelt-bq.gdeltv2.gkg_partitioned`
  WHERE _PARTITIONTIME >= TIMESTAMP("2026-05-01")
    AND _PARTITIONTIME < TIMESTAMP("2026-05-31")
    AND DATE BETWEEN 20260501000000 AND 20260530235959
    AND LENGTH(V2Themes) > 1
)
SELECT theme, COUNT(*) AS cnt
FROM nested, UNNEST(themes) AS theme
WHERE LOWER(theme) LIKE '%inflation%' OR LOWER(theme) LIKE '%econ%' OR ...
GROUP BY theme ORDER BY cnt DESC LIMIT 100
```

Run the dry-run first (no data fetched, no billable query), then the execute config once the estimate looks safe:

```bash
# Estimate cost only — discover which econ/inflation theme codes exist.
python3 -m trading_platform configs/research/debug_gdelt_themes_inflation_30d_partition_dryrun.yaml

# Real execution — writes theme_discovery.csv / theme_discovery.jsonl.
python3 -m trading_platform configs/research/debug_gdelt_themes_inflation_30d_partition_execute.yaml
```

On execution it writes `theme_discovery.csv` / `theme_discovery.jsonl` (alongside the usual `bigquery_sql.sql`, `bigquery_dry_run_estimate.json`, `bigquery_cost_decision.json`, `bigquery_summary.json`). The summary records `theme_search_patterns` so the discovery run is reproducible. Discovery configs default to **`use_cache: false`** — stale cached results (including earlier 0-row entries) would defeat the purpose of a debug path.

**2. Theme bundles** turn a broad research topic into a transparent set of GDELT theme codes. They live in `configs/gdelt_theme_bundles.yaml`:

```yaml
theme_bundles:
  inflation_rates:
    description: "Inflation, interest-rate, cost-of-living, and central-bank related GDELT themes."
    themes:
      - ECON_INFLATION
      - ECON_INTEREST_RATES
      - ECON_COST_OF_LIVING
      - TAX_FNCACT_CENTRAL_BANK
```

A daily-count config can reference a bundle by name instead of listing `query_terms`:

```yaml
pipeline:
  ingest:
    source: bigquery_gdelt_counts
    theme_bundle: inflation_rates
```

Resolution rules:

- `query_terms` only → existing behavior (unchanged).
- `theme_bundle` only → the bundle's themes become the query terms.
- both → **merged, de-duplicated, order-preserving** (explicit `query_terms` first, then bundle themes not already present). Nothing is silently dropped; this lets you pin extra codes on top of a bundle.

The bundle still flows through the same normalized-`UNNEST` matching and the same guardrails — no inflation-specific logic is baked into the SQL builder.

**`inflation_rates` is a *candidate* bundle, not a final answer.** It should be validated and refined with theme discovery (run discovery, see which `ECON_*` / `*CENTRAL_BANK*` codes actually appear and how often, then adjust the bundle). Theme bundles are for **topic-level research**, not final trading signals.

---

## BI / Data Warehouse Reporting Layer

A lightweight analytics/reporting layer (`news_data.reporting`) turns the GDELT GKG data into a small set of **dashboard-ready BigQuery views** for a [Looker Studio](https://lookerstudio.google.com/) dashboard. Full setup — dataset creation, connecting to Looker Studio, and recommended charts — is in [`docs/looker_studio_dashboard.md`](docs/looker_studio_dashboard.md).

Reporting views (each bounded by a rolling `_PARTITIONTIME` window so dashboard queries stay cheap):

| View | Purpose | Output columns |
| --- | --- | --- |
| `daily_event_volume` | Records per calendar day (volume trend) | `event_date`, `record_count` |
| `top_sources` | Most active source domains | `source_domain`, `record_count` |
| `top_themes_or_entities` | Most frequent normalized GDELT theme codes | `entity_or_theme`, `record_count` |
| `data_quality_summary` | Single-row data-quality scorecard | `check_date`, `total_rows`, `missing_required_field_count`, `duplicate_count`, `latest_record_timestamp` |

The `build_views` runner reuses the existing cost-aware path (`SafeBigQueryClient`, SQL guardrails, cost policy, cost ledger) and defaults to dry-run:

```bash
# Local validation only (render + SQL guardrails, no BigQuery call)
python -m news_data.reporting.build_views --no-estimate

# Dry-run validation + BigQuery cost estimate per view (no data, no billable query)
python -m news_data.reporting.build_views --dry-run

# Create / replace the views in BigQuery (requires typed confirmation or --yes)
python -m news_data.reporting.build_views --create --yes
```

**Resume summary:**

- Built BigQuery reporting views for dashboard-ready GDELT/news intelligence analytics, including daily event volume, top source coverage, entity/theme frequency, and data-quality summaries.
- Added dry-run validation and query-safety guardrails (bounded partition scans, no `SELECT *`, read-only DDL/DML checks, `maximum_bytes_billed`) before creating reporting views in BigQuery.
- Created pytest coverage for reporting SQL discovery, required output columns, template rendering, guardrail compliance, and mocked dashboard build/validation logic.
- Documented Looker Studio dashboard setup with recommended charts, filters, and data-quality validation metrics.

---

## Normalized article schema

Raw GDELT records use provider-specific field names such as:

```text
seendate
sourcecountry
socialimage
url_mobile
```

The normalization layer converts those records into a stable internal schema:

```json
{
  "provider": "gdelt",
  "query": "artificial intelligence",
  "title": "Example article title",
  "url": "https://example.com/article",
  "mobile_url": "",
  "domain": "example.com",
  "language": "English",
  "source_country": "United States",
  "published_at": "2026-05-08T00:00:00Z",
  "raw_seen_date": "20260508T000000Z",
  "image_url": "https://example.com/image.jpg",
  "ingested_at": "2026-05-08T14:30:00Z"
}
```

The `ingested_at` field records when the article was first ingested by the system. Normalization preserves an existing `ingested_at` value if one is already present (e.g., from a cached or previously stored record) and only generates a fresh UTC timestamp when the field is missing or empty. This ensures that re-normalizing cached articles never overwrites the original ingestion timestamp, which matters for reproducible historical datasets, append-only ingestion pipelines, and historical lineage tracking.

Downstream transforms consume normalized records, not raw provider responses. That keeps provider-specific quirks from leaking into the rest of the pipeline.

---

## Repo layout

```text
packages/
  common/              Shared config loading, cache, date windows, errors, cost policy/ledger
  pipeline_core/       Pipeline engine: runner, parser, hooks, context, registry
  news_data/           News providers — GDELT DOC client + BigQuery GDELT path
    gdelt/             DOC/artlist client, normalization, query builder
    bigquery/          SafeBigQueryClient, SQL builders, guardrails, cache, theme bundles
    reporting/         BI layer: dashboard-ready BigQuery views + build_views runner
    task/              Pipeline tasks (gdelt_docs, bigquery_*, transforms, stores)
  market_data/         Market data provider boundary placeholder
  strategy_sdk/        Trading-domain abstraction placeholder
apps/
  trading_platform/    CLI (`python3 -m trading_platform`) and task registration
configs/
  demo.yaml            Single-query GDELT DOC demo
  collections/         25 GDELT collection configs (macro, sectors, risk, markets)
  research/            BigQuery research configs (partitioned + theme discovery)
  research/legacy/     Unpartitioned BigQuery configs (reference only — unsafe)
  cost_policy.yaml     Monthly/daily/per-query cost caps and execute confirmation
  reporting.yaml       BI reporting layer: dataset, rolling window, cost controls
  gdelt_theme_bundles.yaml   Candidate topic → GDELT theme code mappings
  local.example.yaml   Template for git-ignored configs/local.yaml overrides
scripts/
  run_collections.py   Batch collection runner with throttling
tests/                 421 workspace-level tests (31 test modules)
docs/                  Product notes, technical guides, development TODO
experiments/           Runtime outputs (gitignored)
```

Each package uses a `pyproject.toml` and `src/<import_name>/` layout.

### Registered pipeline tasks

| Task name | Package | Purpose |
| --- | --- | --- |
| `gdelt_docs` | `news_data` | Ingest recent articles via GDELT DOC API |
| `bigquery_gdelt_counts` | `news_data` | Historical daily topic counts via BigQuery |
| `bigquery_gdelt_theme_discovery` | `news_data` | Debug: list GDELT theme codes in a date window |
| `filter_articles` | `news_data` | Filter normalized articles by language/country/etc. |
| `dedupe_articles` | `news_data` | Deduplicate with syndication group tracking |
| `tag_articles` | `news_data` | Deterministic keyword-based topic tagging |
| `aggregate_article_features` | `news_data` | Collapse tagged articles into daily topic features |
| `store_features` | `news_data` | Append feature rows to persistent store |
| `store_articles` | `news_data` | Append articles to persistent store |

---

## Dependency direction

```text
common           ← lowest layer: config, cache, errors, cost policy/ledger
pipeline_core    ← depends on common
news_data        ← depends on common (GDELT DOC + BigQuery providers)
market_data      ← placeholder boundary
strategy_sdk     ← placeholder boundary
trading_platform ← app layer, depends on pipeline_core + news_data
```

Packages never depend upward on `apps/`. Lower layers do not depend on higher-level product code.

---

## Quick start

> Tests and the CLI require editable-installing the internal packages first.

```bash
git clone <repo-url>
cd kinetic-trading

python3 -m venv .venv
source .venv/bin/activate

python3 -m pip install -e ./packages/common \
  -e ./packages/pipeline_core \
  -e ./packages/news_data \
  -e ./packages/market_data \
  -e ./packages/strategy_sdk \
  -e ./apps/trading_platform \
  -e ".[dev]"
```

**Optional — BigQuery research configs:** install the BigQuery client (already declared in `news_data`; imported lazily so DOC-only runs work without it):

```bash
pip install "google-cloud-bigquery>=3.0"
```

**Optional — local project id override** (keeps public configs free of personal identifiers):

```bash
cp configs/local.example.yaml configs/local.yaml
# edit configs/local.yaml with your real providers.bigquery.project_id
```

Run the GDELT DOC demo pipeline:

```bash
python3 -m trading_platform configs/demo.yaml
```

Run tests:

```bash
pytest
ruff check .
```

---

## Tests

```bash
pytest          # 421 tests
ruff check .    # lint
```

Coverage by area:

| Area | Test modules |
| --- | --- |
| Config loading | `test_config_loader`, `test_local_config_overrides` |
| Pipeline engine | `test_parser`, `test_registry`, `test_runner_failure`, `test_context`, `test_imports` |
| GDELT DOC path | `test_cache`, `test_gdelt_normalize`, `test_dedupe_articles`, `test_tag_articles`, `test_aggregate_article_features`, `test_store_articles`, `test_store_features`, `test_run_collections` |
| BigQuery path | `test_bigquery_gdelt_queries`, `test_bigquery_gdelt_counts_task`, `test_bigquery_theme_discovery_queries`, `test_bigquery_theme_discovery_task`, `test_bigquery_sql_guardrails`, `test_bigquery_normalize_counts`, `test_bigquery_cache`, `test_safe_bigquery_client` |
| Reporting / BI layer | `test_reporting_views` |
| Cost controls | `test_cost_policy`, `test_cost_estimate`, `test_cost_ledger`, `test_cost_report` |
| Theme bundles | `test_gdelt_theme_bundles` |
| Date windows | `test_date_windows` |
| Config guards | `test_research_configs_partitioned` (partition pruning, no leaked project ids, discovery cache policy) |

---

## Running collection jobs

The `scripts/run_collections.py` utility discovers every YAML config under `configs/collections/` and runs them sequentially through the existing pipeline, with configurable throttling between calls.

```bash
# run all collections with default 6s delay
python3 scripts/run_collections.py

# custom delay between runs
python3 scripts/run_collections.py --sleep-seconds 10

# run only the first 3 configs (useful for smoke-testing)
python3 scripts/run_collections.py --max-configs 3

# point at a different config root
python3 scripts/run_collections.py --collections-root configs/collections/macro
```

All configs run the full enrichment pipeline and append to both `data/processed/articles/articles.jsonl` and `data/processed/features/article_features_daily.jsonl`. Failed configs are logged and skipped so the remaining jobs continue to run. A summary with success/failure counts and elapsed time is printed at the end.

---

## Development roadmap

Near-term:

- Validate and refine theme bundles using theme discovery output
- Wire BigQuery daily counts into trend/spike detection over `topic_daily_features`
- Entity/ticker extraction from article titles
- Sentiment/risk scoring transform

Medium-term:

- Market data ingestion task (`market_data` package)
- Feature time-series analysis and research packets
- Strategy/backtesting prototype (`strategy_sdk` package)
- Scheduled ingestion and cron integration

Long-term:

- Dashboard/UI layer
- Larger historical dataset management
- Multiple provider support beyond GDELT
- SQLite or structured storage for articles and features

---

## Documentation

See [`docs/README.md`](docs/README.md) for product vision, technical guides, and development notes.

---

## Notes

This project is intentionally architecture-first. Some package boundaries are placeholders because the repo is designed to evolve into a larger research platform without collapsing into a single script or tightly coupled application.

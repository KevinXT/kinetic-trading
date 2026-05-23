# kinetic-trading

A modular, YAML-driven research pipeline for financial/news data ingestion, caching, normalization, transformation, and future strategy experimentation.

This project is an early-stage trading research platform. The current implementation focuses on the execution engine: a reusable pipeline core, config system, task registry, GDELT news ingestion, normalized article records, cache-aware provider calls, deduplication with syndication tracking, deterministic topic tagging, daily feature aggregation, persistent article and feature stores, batch collection orchestration, and reproducible run artifacts.

The goal is not to be a finished trading bot. The goal is to build the infrastructure layer that future market-data providers, signal-generation tasks, backtests, and strategy modules can plug into cleanly.

---
## Current status

Implemented today:

- YAML-driven pipeline execution
- Recursive config loading and deep merge support
- Linear pipeline runner with lifecycle hooks
- Task registry for pluggable pipeline steps
- `RunContext` for shared runtime state and artifact writing
- GDELT DOC API client and pipeline ingestion task
- Cache-aside API response caching
- Article normalization layer
- Filter transform task operating on normalized records
- Deduplication transform with configurable strategy (`url`, `title`, `title_domain`)
- Duplicate/syndication group metadata and artifact generation
- Deterministic topic tagging with 25-category default rule set
- Daily topic-level feature aggregation with amplification metrics
- Persistent article store with cross-run deduplication
- Persistent feature store with cross-run deduplication
- 25 GDELT collection configs across macro, sectors, risk, and markets categories
- Batch collection runner with throttling and fault tolerance
- Category-specific GDELT timespans (48h macro/sectors, 24h markets, 12h risk)
- Experiment/run folder allocation
- Run metadata and resolved-config snapshots
- 176 tests covering config loading, parsing, registry behavior, runner metadata, context artifact writing, cache, normalization, deduplication, tagging, feature aggregation, article storage, feature storage, collection runner, and import smoke tests

Planned / placeholder boundaries:

- Market data providers
- Strategy SDK abstractions
- Sentiment/entity extraction
- Backtesting integration
- UI/dashboard layer

---

## Why this architecture exists

The pipeline engine is intentionally provider-agnostic.

`pipeline_core` does not know what GDELT is, how articles are normalized, or how market data will be fetched later. It only knows how to:

1. load a config,
2. parse pipeline steps,
3. look up task functions,
4. execute them in order,
5. write metadata and artifacts.

Provider-specific logic lives in provider packages like `news_data`. Downstream transforms operate on normalized internal records instead of raw provider schemas. This makes it possible to add new tasks, data providers, or processing stages without rewriting the runner.

---

## Pipeline flow

```text
YAML config
    ↓
config loader
    ↓
plan parser
    ↓
pipeline runner
    ↓
task registry
    ↓
provider task: gdelt_docs
    ↓
cache-aside fetch layer
    ↓
GDELT raw response
    ↓
article extraction
    ↓
normalization layer
    ↓
transform task: filter_articles
    ↓
transform task: dedupe_articles
    ↓
transform task: tag_articles
    ↓
transform task: aggregate_article_features
    ↓
transform task: store_features
    ↓
data/processed/features/article_features_daily.jsonl
    ↓
transform task: store_articles
    ↓
data/processed/articles/articles.jsonl
    ↓
JSON / JSONL artifacts + run metadata
```

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
data/processed/
  articles/
    articles.jsonl                      Canonical unique articles (cross-run deduplicated)
  features/
    article_features_daily.jsonl        Historical daily topic-level feature rows (cross-run deduplicated)
  metadata/                             Placeholder for future dataset metadata
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
  "image_url": "https://example.com/image.jpg"
}
```

Downstream transforms consume normalized records, not raw provider responses. That keeps provider-specific quirks from leaking into the rest of the pipeline.

---

## Repo layout

```text
packages/
  common/              Shared config loading, cache utilities, errors
  pipeline_core/       Pipeline engine: runner, parser, hooks, context, registry
  news_data/           News data providers and news-specific tasks
  market_data/         Market data provider boundary placeholder
  strategy_sdk/        Trading-domain abstraction placeholder
apps/
  trading_platform/    CLI entrypoint and app-level task registration
configs/
  demo.yaml            Single-query demo pipeline
  collections/         25 GDELT collection configs (macro, sectors, risk, markets)
scripts/
  run_collections.py   Batch collection runner with throttling
data/
  processed/           Persistent article store and historical feature store
tests/                 Workspace-level tests
docs/                  Product notes, technical guides, development TODO
experiments/           Runtime outputs, gitignored
```

Each package uses a `pyproject.toml` and `src/<import_name>/` layout.

---

## Dependency direction

```text
common           ← lowest layer, no internal deps
pipeline_core    ← depends on common
news_data        ← depends on common
market_data      ← placeholder boundary
strategy_sdk     ← placeholder boundary
trading_platform ← app layer, depends on pipeline_core + news_data
```

Packages never depend upward on `apps/`. Lower layers do not depend on higher-level product code.

---

## Quick start

> Tests require editable-installing the internal packages first.

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

Run the demo pipeline:

```bash
python3 -m trading_platform configs/demo.yaml
```

Run tests:

```bash
pytest
```

---

## Tests

```bash
pytest
```

Current coverage includes:

- config loading and recursive includes
- deep merge behavior
- pipeline plan parsing
- task registry duplicate handling
- runner success/failure metadata
- `RunContext` JSON/JSONL artifact writing
- cache-aside fetch, key generation, and corruption handling
- GDELT article normalization and date parsing
- deduplication strategies, duplicate group metadata, and syndication tracking
- topic tagging with default and custom rules, case-insensitive matching, multi-topic assignment
- feature aggregation by date/topic, amplification metrics, and deterministic output ordering
- article storage, cross-run deduplication, and default path handling
- feature storage, cross-run deduplication, deterministic key generation, and append-only behavior
- collection runner discovery, sorting, throttling, and failure tolerance
- package import smoke tests

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

- Add sample fixtures for offline demos
- Entity/ticker extraction from article titles
- Sentiment/risk scoring transform

Medium-term:

- Market data ingestion task
- Feature time-series analysis and trend detection
- Strategy/backtesting prototype
- Scheduled ingestion and cron integration

Long-term:

- Dashboard/UI layer
- Larger historical dataset management
- Multiple provider support
- SQLite or structured storage for articles and features

---

## Documentation

See [`docs/README.md`](docs/README.md) for product vision, technical guides, and development notes.

---

## Notes

This project is intentionally architecture-first. Some package boundaries are placeholders because the repo is designed to evolve into a larger research platform without collapsing into a single script or tightly coupled application.

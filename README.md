# kinetic-trading

A modular, YAML-driven research pipeline for financial/news data ingestion, caching, normalization, transformation, and future strategy experimentation.

This project is an early-stage trading research platform. The current implementation focuses on the execution engine: a reusable pipeline core, config system, task registry, GDELT news ingestion, normalized article records, cache-aware provider calls, and reproducible run artifacts.

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
- Experiment/run folder allocation
- Run metadata and resolved-config snapshots
- 26 tests covering config loading, parsing, registry behavior, runner metadata, context artifact writing, and import smoke tests

Planned / placeholder boundaries:

- Market data providers
- Strategy SDK abstractions
- Sentiment/entity extraction
- Feature engineering
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

## Example artifacts

Each run writes a reproducible output folder:

```text
experiments/demo/<run-id>/
  config_resolved.yaml
  run_metadata.json
  artifacts/
    gdelt_response_raw.json
    gdelt_articles.jsonl
    normalized_articles.jsonl
    filtered_articles.jsonl
    gdelt_summary.json
    filter_summary.json
```

Runtime artifacts are intentionally gitignored. The repository should contain code, configs, tests, and documentation — not large generated datasets.

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
configs/               YAML plans and presets
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
- package import smoke tests

---

## Development roadmap

Near-term:

- Add sample fixtures for offline demos
- Add tests for `common.cache`
- Add tests for GDELT normalization
- Add deduplication transform by URL/domain/title
- Add local processed article store under `data/processed/`

Medium-term:

- Market data ingestion task
- Entity/ticker extraction from article titles
- Sentiment/risk scoring transform
- Feature generation for market research
- Strategy/backtesting prototype

Long-term:

- Dashboard/UI layer
- Scheduled ingestion
- Larger historical dataset management
- Multiple provider support

---

## Documentation

See [`docs/README.md`](docs/README.md) for product vision, technical guides, and development notes.

---

## Notes

This project is intentionally architecture-first. Some package boundaries are placeholders because the repo is designed to evolve into a larger research platform without collapsing into a single script or tightly coupled application.
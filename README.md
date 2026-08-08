# Kinetic Trading

## What this is

Kinetic Trading is a **point-in-time market-intelligence and algorithmic-trading
research platform**. It ingests news, macroeconomic and market data, preserves the
raw provider payloads, normalizes them into canonical records, enriches them
deterministically, and evaluates ideas with reproducible, leakage-aware research
methods.

It is one installable Python distribution (`kinetic`), one import namespace
(`kinetic`), one CLI (`kinetic`). Everything runs locally; nothing here places a
trade.

## What currently works

Each of these is implemented, exercised by the test suite, and runnable today.

| Capability | Where it lives |
| --- | --- |
| YAML-driven pipeline execution with run artifacts, timings and git provenance | `kinetic.core.pipeline` |
| GDELT DOC API article ingestion | `kinetic.ingestion.news.gdelt` |
| GDELT-over-BigQuery historical counts, theme discovery, seeded-vs-background theme scoring — every query dry-run and cost-capped before it can spend | `kinetic.ingestion.news.gdelt.bigquery`, `kinetic.ingestion.warehouse.bigquery` |
| Alpaca historical bars: paginated fetch, retries, response caching, normalization, JSONL storage | `kinetic.ingestion.market.alpaca` |
| Deterministic news processing: normalization, exact and near-duplicate clustering, rule-based entity linking, daily feature aggregation | `kinetic.processing.news` |
| Leakage-aware news × market research datasets: session calendar, explicit alignment policies, feature catalog, dataset manifest and reproducibility fingerprint | `kinetic.processing.cross_asset`, `kinetic.data.catalog` |
| Offline, deterministic event study with block-bootstrap inference, including an event-versus-control contrast and a deterministic study-report generator | `kinetic.research.event_studies`, `kinetic.research.reports` |
| Semiconductor relevance benchmark and real-corpus annotation pilot: sampling design, sample-size planning, annotator calibration, adjudication, agreement statistics, evaluation metrics | `kinetic.ml.relevance` |
| Local-only Streamlit annotation workstation | `tools/annotation` |
| BigQuery reporting views for a Looker Studio dashboard | `kinetic.ingestion.warehouse.bigquery.reporting` |

## What is experimental

**Seeded GDELT theme discovery and scoring.** The idea was to find GDELT theme
codes that genuinely characterize a topic by comparing their prevalence in a
seeded subcorpus against a matched background corpus. The machinery works and the
cost controls work. The research conclusion did not come out positive — see
[the case study](projects/semiconductor_case_study/README.md) for what was
actually learned, including what failed and why. That write-up has not been
edited to look better than the result was.

**The semiconductor news-attention event-vs-control study.** A real historical
research pipeline over AMD, NVDA, SMH and QQQ — not a fixture-only demo. It is
deliberately scoped as "research-data engineering" and its report vocabulary is
fixed to never claim causation, profitability, or trading value.

## What has not been built

- **Any trading capability.** No signals, no portfolio construction, no risk
  limits, no paper execution, no broker execution. There is no `kinetic.trading`
  package; the boundary it must respect when it is written is specified in
  [dependency-rules.md](docs/architecture/dependency-rules.md).
- **FRED**, or any macro provider. `MacroObservation` exists as a canonical
  schema; nothing populates it. [adding-a-provider.md](docs/getting-started/adding-a-provider.md)
  names the exact files a FRED integration would add.
- **Futures, options, forex and crypto instrument identity.** The extension point
  and the required fields are documented in
  [data-lifecycle.md](docs/architecture/data-lifecycle.md); none of it is
  implemented, because nothing in the codebase needs it yet.
- **Trained models.** `kinetic.ml` holds the benchmark, annotation and evaluation
  infrastructure a relevance model would be measured against, plus deterministic
  reference baselines. There is no trained relevance, sentiment or ranking model.
- **The interactive terminal.** Deliberately deferred until the non-interactive
  CLI and the application boundaries are stable.

## How data flows

```
raw provider response      warehouse/raw/          preserved, never overwritten
  → normalized record      warehouse/normalized/   canonical, provider-independent
    → curated dataset      warehouse/curated/      cleaned, deduped, session-aligned
      → feature dataset    warehouse/features/     described by the feature catalog
        → prediction       warehouse/predictions/  (nothing writes here yet)
          → research result / trading input
```

Every pipeline run writes to `warehouse/runs/<name>/<run_id>/` with the resolved
config, run metadata, git revision and per-step artifacts. The full rules —
including which point-in-time timestamps are preserved and why a missing
measurement is `None` rather than `0` — are in
[data-lifecycle.md](docs/architecture/data-lifecycle.md).

## Install

Requires Python 3.11 or 3.12.

```bash
# with uv (recommended)
uv sync --all-extras --dev
uv pip install -e .

# or with pip
python -m pip install -e ".[dev]"
```

Optional extras: `bigquery` (the cost-aware BigQuery path), `annotation` (the
Streamlit workstation), `dev` (test and lint tooling).

## Run the offline demo

This needs no credentials, no network and no cloud account. It reads committed
fixtures and writes a complete set of research artifacts:

```bash
kinetic run configs/research/news_market_dataset_demo.yaml --run-id demo
# or: make demo
```

Then look at `warehouse/runs/news_market_dataset_demo/demo/`. The same
`--run-id` produces byte-identical output on a rerun.

Other useful commands:

```bash
kinetic --help
kinetic task list                    # every task the platform provides
kinetic config validate <config>     # check a config before spending anything
kinetic cost report                  # estimated cloud spend to date
```

## Run validation

```bash
make validate
```

That runs, in order: stale-build check, ruff, import-boundary contracts, black,
mypy, dependency analysis, pytest, and a wheel build plus an isolated import/CLI
smoke test from outside the source tree. `make help` lists the individual
targets.

## Where each type of code belongs

| Question | Package |
| --- | --- |
| What runs the pipeline? | `kinetic.core` |
| What is canonical data? | `kinetic.data` |
| What talks to an external provider? | `kinetic.ingestion` |
| What is deterministic transformation? | `kinetic.processing` |
| What is model-driven? | `kinetic.ml` |
| What evaluates an idea? | `kinetic.research` |
| What does a human touch? | `kinetic.interface` |
| How is the application assembled? | `kinetic.bootstrap` |
| Is this one specific study? | `projects/` |
| Is this a supporting app, not part of pipeline execution? | `tools/` |

The rules are not advisory: they are enforced by import-linter contracts in
`pyproject.toml` and checked by `make lint-imports`.

## Configuration

Pipeline configs live in `configs/`:

- `configs/pipelines/` — the GDELT demo and the Alpaca bars example
- `configs/collections/` — GDELT topic collections (macro, markets, risk, sectors)
- `configs/research/` — research pipelines and reference data (topic→instrument
  mappings, the feature catalog, inflation BigQuery configs)
- `configs/gdelt_theme_bundles.yaml` — the curated GDELT theme bundle vocabulary
- `configs/cost_policy.yaml` — cloud spend caps
- `projects/semiconductor_case_study/configs/` — the case study's own configs,
  including `semiconductors_seeded_theme_scoring_30d_dryrun.yaml`

Every config uses one shape:

```yaml
name: news_market_dataset_demo

pipeline:
  steps:
    - task: research.build_news_market_dataset
      params:
        articles_path: tests/fixtures/research/semiconductors_articles.json
        forward_horizon: 5
```

Put private values (a real BigQuery project id) in `configs/local.yaml`, which is
git-ignored and deep-merged over whichever config you run. Start from
`configs/local.example.yaml`.

## Documentation

Start with [platform-overview.md](docs/architecture/platform-overview.md), then
[execution-flow.md](docs/architecture/execution-flow.md) for a real run traced end
to end. See also
[dependency-rules.md](docs/architecture/dependency-rules.md),
[data-lifecycle.md](docs/architecture/data-lifecycle.md),
[development.md](docs/getting-started/development.md),
[adding-a-provider.md](docs/getting-started/adding-a-provider.md), and the
[documentation index](docs/README.md).

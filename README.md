# Kinetic Trading

Market and news data pipelines for reproducible event research.

The system pulls Alpaca historical bars and GDELT news through the DOC API and BigQuery GKG, then builds aligned news × market datasets and event-study summaries.

Each run stores its resolved configuration, metadata, outputs, and—when BigQuery is used—the generated SQL and cost estimate. Runs are preserved under `experiments/` so results can be inspected and reproduced later.

> Kinetic Trading is a research system. It does not place trades or claim predictive returns.

[![CI](https://github.com/KevinXT/kinetic-trading/actions/workflows/ci.yml/badge.svg)](https://github.com/KevinXT/kinetic-trading/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)

## What is implemented

| Area | Capability |
| --- | --- |
| Inputs | Alpaca bars; GDELT DOC articles; BigQuery GKG counts and theme research |
| Pipeline execution | YAML plans via `pipeline_core`, tasks registered in `trading_platform` |
| Storage | Idempotent JSONL writes for market and news records |
| Cloud controls | BigQuery dry-run first, byte caps, spend policy + ledger (`configs/cost_policy.yaml`) |
| Research outputs | Leakage-aware alignment, event studies, run artifacts under `experiments/` |
| Validation | Offline pytest, Ruff, scoped Black/mypy, wheel import smoke on Python 3.11/3.12 |

## Run the offline demo

No API keys or BigQuery credentials are required.

<details>
<summary>Development installation</summary>

```bash
git clone https://github.com/KevinXT/kinetic-trading.git
cd kinetic-trading
python3 -m venv .venv
source .venv/bin/activate

python3 -m pip install -e ./packages/common \
  -e ./packages/pipeline_core \
  -e ./packages/news_data \
  -e ./packages/market_data \
  -e ./packages/research_data \
  -e ./packages/strategy_sdk \
  -e ./apps/trading_platform \
  -e ".[dev]"
```

</details>

```bash
python3 -m trading_platform configs/research/news_market_dataset_demo.yaml --run-id readme_offline_demo
```

```text
completed run: news_market_dataset_demo
run_id: readme_offline_demo
outputs: experiments/news_market_dataset_demo/readme_offline_demo
```

Useful files in that run: `artifacts/dataset_manifest.json`, `news_market_observations.csv`, and `event_study_summary.json`.

For BigQuery, set a real project ID in the ignored `configs/local.yaml`; checked-in configs use `YOUR_PROJECT_ID`.

```bash
python3 -m trading_platform configs/research/semiconductors_seeded_theme_scoring_30d_dryrun.yaml
```

Live execution uses a matching `*_execute.yaml`, requires an explicit `execute_query: "ENABLE"` gate, and can incur cost. More research configs are under [`configs/research/`](configs/research/); theme codes for topics are listed in [`configs/gdelt_theme_bundles.yaml`](configs/gdelt_theme_bundles.yaml).

## Data flow

```mermaid
flowchart LR
  alpaca[Alpaca bars] --> norm[Normalized records]
  doc[GDELT DOC] --> norm
  bq[GDELT BigQuery GKG] --> gate[Dry-run + cost caps]
  gate --> norm
  norm --> align[News/market alignment]
  align --> study[Event study]
  gate --> arts[Config + SQL + cost + metadata]
  align --> arts
  study --> arts
```

YAML configs drive `pipeline_core`; `news_data` and `market_data` own the provider adapters; `research_data` builds the join and event study. Pipeline BigQuery tasks estimate bytes before execution and reject queries that exceed the configured caps. That matters because a GKG scan bills on bytes read, not on how selective the `WHERE` clause looks.

Scoring tasks write review artifacts only. They do not edit production theme bundles. Credentials stay in `.env` / `configs/local.yaml`, both gitignored.

## Case study: testing GDELT themes for semiconductor news

**Hypothesis.** GDELT theme codes might already give us a semiconductor taxonomy.

**Method.** Compare GKG records that mention semiconductor companies with a disjoint background over the same 30-day window, correct for testing more than 1,300 themes, and check whether hits were dominated by one source, date, or seed company.

| | |
| --- | ---: |
| Window | 30 days |
| Seeded records | 40,334 |
| Theme hypotheses tested | 1,359 |
| Reliable semiconductor identity themes | 0 |
| Scan / estimate | 10.624 GiB / ≈ $0.0648 |

**Result.** Contextual themes appeared around manufacturing, servers, storage, and macroeconomic news, but none worked as a reliable semiconductor identity label. No themes were promoted into the production bundle. The next approach is entity resolution plus text classification rather than another theme-code search.

Full write-up: [semiconductor theme scoring](docs/research/semiconductor-theme-scoring/).

## Reporting preview

![Local reporting dashboard](docs/images/reporting_dashboard_preview.png)

*Local dashboard using committed sample data; no live market or trading feed.*

```bash
cd apps/reporting_dashboard
python3 -m http.server 8000
```

## Validation

GitHub Actions runs on Python 3.11 and 3.12: offline `pytest`, Ruff, Black on the market-data/research scopes, mypy on `common` / `market_data` / `pipeline_core` / `research_data`, plus wheel builds and an isolated import smoke test.

```bash
pytest -q
ruff check .
```

## Current limitations

GDELT records are noisy media annotations rather than unique articles. Organization matching can include irrelevant mentions, and the repository does not yet include a text relevance classifier for semiconductor news.

## Documentation

Deeper documentation: [semiconductor theme scoring](docs/research/semiconductor-theme-scoring/), [news × market dataset design](docs/research/news_market_dataset_design.md), [financial-data architecture](docs/architecture/financial-data.md), and the [documentation index](docs/README.md).

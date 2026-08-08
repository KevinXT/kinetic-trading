# Target structure

This document records the intended directory layout, and — just as importantly —
which parts of it **deliberately do not exist yet**.

A directory is created only when code, documentation, configuration, or a tracked
placeholder policy gives it a real purpose. Empty packages that exist only to
match a diagram are worse than no package: they imply an implementation that a
reader will go looking for.

## Target layout

```
kinetic-trading/
├── pyproject.toml              one authoritative root distribution
├── uv.lock                     one lockfile
├── Makefile                    make validate is the one comprehensive command
├── CLAUDE.md                   permanent repository rules
├── README.md
│
├── src/kinetic/
│   ├── __init__.py             version only; importing has no side effects
│   ├── bootstrap.py            THE composition root
│   │
│   ├── core/                   platform mechanics only
│   │   ├── errors.py           platform exception hierarchy
│   │   ├── config.py           YAML loading, includes, deep merge, local overrides
│   │   ├── artifacts.py        run directory allocation, resolved config, run metadata
│   │   ├── provenance.py       UTC timestamps, git HEAD resolution
│   │   └── pipeline/
│   │       ├── task.py         the task contract
│   │       ├── registry.py     TaskRegistry (an object, not a global)
│   │       ├── context.py      RunContext
│   │       ├── plan.py         config -> validated Plan/Step
│   │       ├── legacy_plan.py  the deprecated ingest/transform/strategy shape (0.4.0 removal)
│   │       ├── hooks.py        timing, error capture, metadata write
│   │       └── runner.py       the execution loop
│   │
│   ├── data/                   canonical, provider-independent
│   │   ├── serialization.py    deterministic JSON for canonical records
│   │   ├── schemas/
│   │   │   ├── instruments.py  Instrument identity
│   │   │   ├── market.py       PriceBar, MarketEvent
│   │   │   ├── macro.py        MacroObservation
│   │   │   ├── fundamentals.py FilingEvent, FinancialFact
│   │   │   ├── news.py         ArticleTextRecordV1
│   │   │   ├── entities.py     EntityReferenceV1
│   │   │   └── research.py     news x market research records + manifest schema
│   │   ├── catalog/
│   │   │   ├── features.py     the feature catalog (field provenance)
│   │   │   ├── manifest.py     dataset manifest + reproducibility fingerprint
│   │   │   ├── mappings.py     topic -> instrument mappings
│   │   │   ├── instruments.py  instrument resolution over a curated master
│   │   │   ├── entities.py     entity reference loading
│   │   │   └── seeds.py        seed-term vocabulary (reference data)
│   │   └── storage/            FinancialDataStore interface + JSONL implementation
│   │
│   ├── ingestion/               everything that talks to an external provider
│   │   ├── caching.py          cache-aside JSON response cache
│   │   ├── requests.py         provider-independent request contracts
│   │   ├── protocols.py        PriceDataProvider and friends
│   │   ├── registry.py         provider factory registry
│   │   ├── windows.py          config window block -> concrete date range
│   │   ├── cost/                external-spend policy, estimate, ledger, report
│   │   ├── news/
│   │   │   ├── local_corpus.py rights-aware local article corpus import
│   │   │   └── gdelt/           DOC API client, config, parsing, normalize, tasks
│   │   │       └── bigquery/    GDELT-specific SQL, normalization, tasks
│   │   ├── market/
│   │   │   └── alpaca/          client, config, raw models, normalize, provider, tasks
│   │   └── warehouse/
│   │       └── bigquery/        SafeBigQueryClient, SQL guardrails, result cache
│   │           └── reporting/   dashboard view definitions + build/export CLIs
│   │
│   ├── processing/               deterministic transformation
│   │   ├── stats.py             dependency-free lag-only estimators, plus the
│   │   │                        two-sample Welch/bootstrap helpers the
│   │   │                        event-vs-control contrast is built on
│   │   ├── news/                normalize, dedupe, entity linking, seeds, themes, features, tasks
│   │   ├── market/               session calendar, market-session features
│   │   └── cross_asset/          alignment policies, leakage-aware join, validation
│   │
│   ├── ml/
│   │   └── relevance/            benchmark, annotation, sampling, splits, agreement, metrics, tasks
│   │
│   ├── research/
│   │   ├── datasets/             the dataset builder (orchestrates processing,
│   │   │                         then runs the event study — this is why it is
│   │   │                         "research", not "processing")
│   │   ├── event_studies/        offline event study, including the event-vs-control
│   │   │                         contrast machinery
│   │   ├── reports/               human-review worksheets, deterministic study reports
│   │   └── tasks.py               research dataset pipeline task
│   │
│   └── interface/
│       └── cli/                  Typer command definitions and output formatting
│
├── configs/
│   ├── collections/               GDELT topic collection pipelines
│   ├── pipelines/                 demo and provider pipelines
│   ├── providers/                 provider-only config fragments
│   └── research/                  research pipelines and reference data
│
├── projects/semiconductor_case_study/
│   ├── configs/                   case-specific pipeline configs (theme scoring,
│   │                              relevance benchmark/pilot, and the
│   │                              news-attention event-vs-control study)
│   ├── results/                   preserved study outputs
│   ├── docs/                      design memos, protocols, runbooks, conclusions
│   └── scripts/                   case-specific offline analysis and fixture generation
│
├── tools/
│   ├── annotation/                local-only Streamlit annotation workstation
│   ├── fixtures/                  deterministic fixture generators
│   └── run_collections.py         batch runner over configs/collections
│
├── tests/
│   ├── unit/{core,data,ingestion,processing,ml,research,interface,tools}
│   ├── integration/
│   ├── e2e/
│   └── fixtures/
│
├── docs/{architecture,getting-started,concepts,reference,case-studies}
│
└── warehouse/                     generated; git-ignored
    ├── raw/  normalized/  curated/  features/  predictions/  models/  runs/
```

## Deliberately absent

| Target path | Why it does not exist |
| --- | --- |
| `src/kinetic/trading/` | There is no trading implementation. `packages/strategy_sdk` was an empty placeholder and is deleted. Creating `trading/signals`, `trading/risk`, `trading/execution` with nothing in them would advertise a capability that does not exist. The boundary this package must respect when it is written is specified in `dependency-rules.md`. |
| `src/kinetic/ingestion/macro/fred/` | There is no FRED code. `docs/getting-started/adding-a-provider.md` names the exact files a FRED integration would add. |
| `src/kinetic/ml/{ranking,entity_linking,sentiment,training,inference,evaluation}/` | No trained model exists. What does exist — relevance benchmark and evaluation infrastructure — lives in `ml/relevance/`. Deterministic rule-based entity linking is in `processing/news/entity_linking.py`, because it is deterministic. |
| `src/kinetic/research/{backtests,experiments}/` | No backtest engine and no experiment-definition layer exist. The event-vs-control study in `research/event_studies/` is a real research study, not a backtest — it makes no trading or execution claim. |
| `src/kinetic/data/schemas/{predictions,signals}.py` | Nothing produces model predictions or trading signals yet. |
| `src/kinetic/interface/terminal/` | The Textual terminal is explicitly out of scope until the non-interactive CLI and the application boundaries are stable. |
| `configs/models/`, `configs/strategies/` | No models and no strategies to configure. |

## Warehouse directories

`warehouse/` is created on demand by the code that writes into it, and the whole
tree is git-ignored. The repository tracks the *policy* — see
[`data-lifecycle.md`](data-lifecycle.md) — not the contents.

# Kinetic Trading — platform overview

Audience: a developer who did not perform this architecture refactor and needs to
find their way around the codebase in ten minutes.

## What this is

Kinetic Trading is a **point-in-time market-intelligence and algorithmic-trading
research platform**. It ingests news, macroeconomic and market data, preserves
the raw provider payloads, normalizes them into canonical records, enriches them
deterministically, evaluates ideas with reproducible research methods, and is
intended to eventually drive paper and live trading.

It is a **modular monolith**: one installable Python distribution, one import
namespace (`kinetic`), one CLI (`kinetic`), one `pyproject.toml`. The internal
structure — not the packaging — is what enforces the boundaries.

## The eight subsystems

Everything under `src/kinetic/` belongs to exactly one of these. If you cannot
decide where a new module goes, the answer is almost always in this table.

| Package | Answers the question | Contains |
| --- | --- | --- |
| `kinetic.core` | *What runs the pipeline?* | Task contract, registry, pipeline runner, execution context, plan parsing, hooks, configuration loading, artifact/run metadata, provenance, platform-level exceptions |
| `kinetic.data` | *What is canonical data?* | Provider-independent schemas, instrument identity, dataset/feature catalog, storage interfaces and implementations, deterministic serialization |
| `kinetic.ingestion` | *What talks to the outside world?* | HTTP/API clients, provider auth/config, retries, pagination, response→canonical mapping, provider-specific ingestion tasks, external-spend guardrails |
| `kinetic.processing` | *What is deterministic transformation?* | Cleaning, normalization, dedupe, calendars, alignment, joins, return calculation, feature construction, rule-based entity linking |
| `kinetic.ml` | *What is model-driven?* | Relevance benchmark, annotation/adjudication mathematics, sampling and split design, agreement statistics, evaluation metrics |
| `kinetic.research` | *What evaluates an idea?* | Event studies (including event-vs-control contrasts), research dataset construction, research report generation |
| `kinetic.interface` | *What does a human touch?* | CLI command definitions and output formatting |
| `kinetic.bootstrap` | *How is the app assembled?* | The single composition root — builds the task registry explicitly |

Two subsystems named in the long-term target **do not exist yet** and are
deliberately not invented during this refactor:

- `kinetic.trading` — signals, portfolio construction, risk limits, paper and
  broker execution. There is no implementation. The intended boundary is
  specified in [`dependency-rules.md`](dependency-rules.md).
- `kinetic.interface.terminal` — the planned interactive Textual terminal. See
  [`execution-flow.md`](execution-flow.md) for how it will reuse the same
  services the CLI calls, once it exists.

## What lives outside `src/kinetic/`

| Path | Purpose |
| --- | --- |
| `configs/` | Checked-in pipeline and provider configuration |
| `projects/semiconductor_case_study/` | One specific study: its configs, inputs, preserved results, conclusions and limitations — including both the theme-scoring experiment and the event-vs-control attention study |
| `tools/` | Supporting applications that are **not** part of pipeline execution (the Streamlit annotation workstation, the collection runner, fixture generators, offline analysis scripts) |
| `tests/` | Mirrors the `kinetic` package structure |
| `warehouse/` | All generated data. Git-ignored. See [`data-lifecycle.md`](data-lifecycle.md) |

## How a run happens, in one paragraph

`kinetic run <config>` parses the YAML into a validated plan, asks
`kinetic.bootstrap.build_default_registry()` for an explicit task registry,
allocates a run directory under `warehouse/runs/`, and executes the plan's steps
in order. Each step receives a `RunContext` (resolved config, run directory,
artifact helpers, shared state) and its own parameters. Hooks record timing, git
provenance, and failures, and write `run_metadata.json` at the end. The full
trace of one real pipeline is in [`execution-flow.md`](execution-flow.md).

## What currently works, and what does not

**Implemented and exercised by tests**

- YAML-driven linear pipeline execution with run artifacts and provenance
- GDELT DOC API ingestion (article list) and GDELT-over-BigQuery historical
  counts, theme discovery, and seeded-vs-background theme scoring, all behind
  cost guardrails that dry-run before spending
- Alpaca historical bars ingestion, normalization, caching and JSONL storage
- Deterministic news processing: normalization, exact and near duplicate
  clustering, rule-based entity matching, article feature aggregation
- Leakage-aware news×market research dataset construction with a session
  calendar, explicit alignment policies, a feature catalog and a dataset manifest
- An offline, deterministic event study — including an **event-versus-control**
  contrast (Welch's test and a two-sample session-block bootstrap CI) and a
  deterministic Markdown study-report generator with a fixed, non-overclaiming
  interpretation vocabulary
- The semiconductor relevance benchmark and real-corpus annotation pilot:
  sampling design, sample-size planning, agreement statistics, adjudication,
  duplicate-threshold calibration, and evaluation metrics
- A local-only Streamlit annotation workstation

**Experimental / research-only**

- Seeded GDELT theme discovery and scoring. See
  [`../../projects/semiconductor_case_study/README.md`](../../projects/semiconductor_case_study/README.md)
  for what this actually produced — the honest answer is that the seeded-theme
  approach did **not** yield a validated relevance signal.
- The semiconductor news-attention event-vs-control study is a real historical
  research pipeline (not a fixture-only demo), but it is explicitly scoped as
  "research-data engineering" — it makes no trading, execution, or profitability
  claim, and its report vocabulary is deliberately restricted to say so.

**Not built**

- Any trading capability whatsoever: no signals, no portfolio construction, no
  risk limits, no paper execution, no broker execution
- FRED or any other macro provider
- Futures, options, forex or crypto instrument identity
- Trained relevance/sentiment/ranking models — the ML package today contains the
  benchmark, annotation and evaluation infrastructure that a model would be
  measured against, plus deterministic reference baselines
- The interactive terminal UI

## Where to start reading

1. `src/kinetic/bootstrap.py` — the composition root, and the complete list of tasks
2. `src/kinetic/core/pipeline/runner.py` — the execution loop
3. `src/kinetic/core/pipeline/plan.py` — the authoritative config shape
4. `src/kinetic/interface/cli/app.py` — every command the CLI exposes
5. `docs/architecture/execution-flow.md` — one real run, end to end

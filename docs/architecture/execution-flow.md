# Execution flow

One real command, traced through every layer it touches. Nothing here is
illustrative — you can run it now, with no credentials and no network, and follow
along in the code.

```bash
kinetic run configs/research/news_market_dataset_demo.yaml --run-id demo
```

## The config

`configs/research/news_market_dataset_demo.yaml`:

```yaml
name: news_market_dataset_demo
pipeline:
  steps:
    - task: research.build_news_market_dataset
      params:
        articles_path: tests/fixtures/research/semiconductors_articles.json
        bars_path: tests/fixtures/research/market_bars.json
        mappings_path: configs/research/topic_instrument_mappings.yaml
        feature_catalog_path: configs/research/news_market_feature_catalog.yaml
        alignment_policy: session_information_window_v2
        forward_horizon: 5
        ...
```

## 1. CLI — `kinetic.interface.cli.app`

The console script `kinetic` resolves to `kinetic.interface.cli.app:main`. Typer
routes `run` to the `run` command, which parses three things — a config path, a
runs root (default `warehouse/runs`), an optional fixed run id — checks the file
exists, and hands off. It does no domain work at all:

```python
ctx = run_pipeline_from_file(
    config,
    registry=build_default_registry(),
    runs_root=runs_root,
    run_id=run_id,
)
```

Note the order of evaluation: the registry is built *by the caller* and passed
in. The runner never reaches for a global.

## 2. Configuration — `kinetic.core.config`

`run_pipeline_from_file` calls `load_runtime_config(path)`, which:

- reads the YAML,
- resolves any `include:` chain recursively,
- deep-merges the layers into one mapping,
- deep-merges `configs/local.yaml` over the result if that git-ignored file
  exists, so a real BigQuery project id never has to live in a committed config.

The output is one fully resolved dictionary. Everything downstream sees only
this — no file paths, no include semantics, no override rules.

## 3. Composition — `kinetic.bootstrap`

`build_default_registry()` imports each task module and binds it to a namespaced
identifier:

```python
registry.register("research.build_news_market_dataset", build_news_market_dataset_task)
registry.register("news.gdelt.fetch_articles", gdelt_docs_task)
registry.register("market.alpaca.fetch_bars", fetch_alpaca_bars)
...
```

Provider imports happen inside this function, not at module scope, so
`import kinetic` stays cheap and never requires `google-cloud-bigquery` or
credentials. The Alpaca task needs a configured provider registry, so the
composition root builds one and closes over it — the task's call site stays a
plain `(ctx, params)` callable.

Deprecated pre-0.2 task names are registered as aliases here, and nowhere else.

## 4. Plan — `kinetic.core.pipeline.plan`

`parse_plan(cfg, source=path)` validates the config into a `Plan`: a run name and
an ordered tuple of `Step(task, params)`. Every failure names the file, the step
index, the task and the field:

```
configs/demo.yaml: pipeline.steps[1] (task 'news.tag_articles'): 'params' must be
a mapping, got list.
```

If the config uses the pre-0.2 `ingest`/`transform`/`strategy` sections instead,
`plan.py` defers to `legacy_plan.py`, which emits a `DeprecationWarning` and
produces the same `Step` objects. That is the only module in the codebase that
understands the old shape.

## 5. Run directory — `kinetic.core.artifacts`

`allocate_run_dir(runs_root, plan.name, run_id)` slugs the run name and picks an
unused group directory, then appends the run id:

```
warehouse/runs/news_market_dataset_demo/demo/
```

A second run of the same pipeline gets `news_market_dataset_demo_2/`, so runs are
never silently interleaved.

`write_resolved_config` immediately writes `config_resolved.yaml` — **before the
first step runs**, so a run that dies on step 1 is still reproducible.

## 6. Runner — `kinetic.core.pipeline.runner`

The runner constructs a `RunContext` (resolved config, run name, run id, run
directory, and a mutable `state` dict shared across steps), installs the default
hook chain, and loops:

```
for each step:
    handler = registry.resolve(step.task)     # PipelineError if unknown
    hooks.before_step(...)
    handler(ctx, step.params)
    hooks.after_step(...)
```

The hooks are `TimingHook` (per-step wall time), `ErrorHook` (captures the failed
step and writes `traceback.txt`) and `MetadataHook` (run timestamps, git HEAD
resolved via `kinetic.core.provenance`, and the final `run_metadata.json` write).
`MetadataHook` runs last so its `after_run` fires after every other hook.

The loop catches `Exception`, not `BaseException`: a Ctrl-C propagates without
being recorded as a failed step, and the `finally` block still writes metadata.

## 7. Task — `kinetic.research.tasks.build_news_market_dataset_task`

This one task orchestrates the deterministic layers. It reads committed fixtures
(no provider call anywhere in this pipeline) and calls
`kinetic.research.datasets.builder.build_dataset`, which:

1. `kinetic.processing.market.calendar` — resolves US equity sessions, including
   weekends, fixed and observed holidays, and DST transitions
2. `kinetic.processing.news.features` — daily news features per topic, with an
   explicit capability flag per field so an unmeasurable field is `None`, never `0`
3. `kinetic.processing.market.features` — market-session features, with rolling
   predictors computed strictly from prior sessions
4. `kinetic.processing.cross_asset.alignment` — maps each article's publication
   time to the first session that could react to it
5. `kinetic.processing.cross_asset.join` — builds the joined observations,
   labelling same-session fields `contemporaneous` and forward outcomes `targets`
6. `kinetic.processing.cross_asset.validation` — asserts the leakage and
   availability invariants, failing loudly rather than emitting a leaky dataset
7. `kinetic.data.catalog.manifest` — the dataset manifest and reproducibility
   fingerprint
8. `kinetic.research.event_studies.event_study` — the offline event study with
   block-bootstrap inference

If the config sets `study_report_name` (as the semiconductor attention-study
configs do), the task also builds the event-versus-control contrast via
`kinetic.research.event_studies.event_study.build_event_control_contrasts` and
renders a deterministic Markdown report through
`kinetic.research.reports.study_report`, whose interpretation vocabulary is fixed
so it cannot describe causation or profitability.

## 8. Artifacts

The task writes through `RunContext.write_json` / `write_jsonl`, which place
everything under `<run_dir>/artifacts/`:

```
warehouse/runs/news_market_dataset_demo/demo/
├── config_resolved.yaml            what actually ran
├── run_metadata.json               status, timings, git commit, failed step
└── artifacts/
    ├── dataset_manifest.json       inputs, versions, reproducibility fingerprint
    ├── news_topic_daily_features.jsonl
    ├── market_session_features.jsonl
    ├── news_market_observations.jsonl
    ├── feature_catalog.json        every field's formula, source and leakage risk
    └── event_study_report.*        confirmatory vs exploratory results
```

Rerunning with the same `--run-id` reproduces every computed value byte for byte.
Only the recorded wall-clock timestamps (`generated_at`, `ingested_at`,
`feature_available_at`) differ, because those record when the run happened — they
are point-in-time metadata, not results. `tests/e2e/test_offline_pipeline.py`
asserts exactly that.

## A pipeline that does hit a provider

`configs/pipelines/demo.yaml` is the same machinery with a provider at the front:

```yaml
pipeline:
  steps:
    - task: news.gdelt.fetch_articles     # ingestion: HTTP, retries, caching
    - task: news.filter_articles          # processing: deterministic
    - task: news.dedupe_articles          # processing: deterministic
    - task: news.tag_articles             # processing: deterministic
    - task: news.aggregate_features       # processing: deterministic
    - task: news.store_features           # data: canonical storage
    - task: news.store_articles           # data: canonical storage
```

Steps pass data through `ctx.state`. The first step is the only one that touches
the network; everything after it is reproducible from what that step stored.

## The future `kinetic terminal`

The interactive Textual terminal is not built. When it is, it calls the same
three functions the CLI calls — `build_default_registry()`,
`load_runtime_config()`, `run_pipeline()` — and lives under
`kinetic/interface/terminal/`. Nothing in steps 2 through 8 changes, and nothing
in those steps knows which front end invoked it. Keeping the CLI commands as thin
as they are is what makes that true; if a command grew domain logic, the terminal
would have to reimplement it.

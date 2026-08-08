# Compatibility and deprecations

The 0.2 consolidation changed three things a config or a script could depend on:
the console command, the pipeline config shape, and task names. Each has exactly
one compatibility mechanism, in one place, with a removal date.

Nothing checked into this repository uses any of them.

## Removed with no alias

| Removed | Replacement |
| --- | --- |
| `trading-platform <config>` | `kinetic run <config>` |
| `python -m trading_platform <config>` | `kinetic run <config>` |
| `trading-platform cost-report` | `kinetic cost report` |
| Import namespaces `common`, `pipeline_core`, `news_data`, `market_data`, `research_data`, `trading_platform` | `kinetic.*` — see [migration-map.md](../architecture/migration-map.md) for the module-by-module mapping |
| Distributions `kinetic-common`, `kinetic-pipeline-core`, `kinetic-news-data`, `kinetic-market-data`, `kinetic-research-data`, `kinetic-strategy-sdk`, `kinetic-trading-platform`, `kinetic-relevance-annotation-ui` | one distribution, `kinetic` |
| Output paths `experiments/`, `data/processed/`, `data/cost/` | `warehouse/runs/`, `warehouse/normalized/`, `warehouse/cost/` |

Import paths and distribution names get no shim. A stale import fails loudly at
import time with a name that is easy to grep for, which is a better outcome than
a shim that quietly keeps two module trees alive.

## Deprecated, still working

### Task names

Pre-0.2 task names still resolve. Using one emits a `DeprecationWarning` naming
the current identifier. **Removed in 0.4.0.**

The mapping is `LEGACY_TASK_ALIASES` in `src/kinetic/bootstrap.py`, and there is
no other alias table anywhere in the codebase.

| Deprecated | Current |
| --- | --- |
| `alpaca_historical_bars` | `market.alpaca.fetch_bars` |
| `gdelt_docs` | `news.gdelt.fetch_articles` |
| `bigquery_gdelt_counts` | `news.gdelt.bigquery.fetch_daily_counts` |
| `bigquery_gdelt_theme_discovery` | `news.gdelt.bigquery.discover_themes` |
| `bigquery_gdelt_seeded_theme_discovery` | `news.gdelt.bigquery.discover_seeded_themes` |
| `bigquery_gdelt_seeded_theme_scoring` | `news.gdelt.bigquery.score_seeded_themes` |
| `filter_articles` | `news.filter_articles` |
| `dedupe_articles` | `news.dedupe_articles` |
| `tag_articles` | `news.tag_articles` |
| `aggregate_article_features` | `news.aggregate_features` |
| `store_articles` | `news.store_articles` |
| `store_features` | `news.store_features` |
| `build_news_market_dataset` | `research.build_news_market_dataset` |
| `build_semiconductor_relevance_benchmark` | `ml.relevance.build_benchmark` |
| `run_semiconductor_relevance_real_corpus_pilot` | `ml.relevance.run_real_corpus_pilot` |

Deprecated names never appear in `kinetic task list` — that command shows the
real interface. To see them:

```bash
kinetic task list --show-deprecated
```

To prove a config is free of them:

```bash
kinetic config validate --strict-task-names <config>
```

### The pipeline config shape

The pre-0.2 `ingest` / `transform` / `strategy` sections still parse, with a
`DeprecationWarning`. **Removed in 0.4.0.**

All of it is confined to `src/kinetic/core/pipeline/legacy_plan.py`; the current
shape in `plan.py` never depends on it except through one deferred import.

Before:

```yaml
pipeline:
  ingest:
    source: gdelt_docs
    max_records: 10
  transform:
    - type: dedupe_articles
    - tag_articles: true
  strategy:
    type: build_news_market_dataset
    forward_horizon: 5
```

After:

```yaml
pipeline:
  steps:
    - task: news.gdelt.fetch_articles
      params:
        max_records: 10
    - task: news.dedupe_articles
    - task: news.tag_articles
    - task: research.build_news_market_dataset
      params:
        forward_horizon: 5
```

Convert automatically — this rewrites the shape *and* translates deprecated task
names:

```bash
kinetic config migrate old.yaml --output new.yaml
kinetic config validate --strict-task-names new.yaml
```

## Tests

Both mechanisms are covered, so neither can rot into a lie:

| Behavior | Test |
| --- | --- |
| Aliases resolve, warn, and are excluded from the public task list | `tests/unit/core/test_registry.py` |
| Every alias points at a task that exists | `tests/e2e/test_imports.py` |
| Aliases can be switched off entirely | `tests/e2e/test_imports.py` |
| The legacy config shape parses, warns, and migrates to something that validates cleanly | `tests/unit/core/test_legacy_plan.py` |
| `kinetic config migrate` end to end | `tests/unit/interface/test_cli.py` |
| A deprecated task name still runs a real pipeline | `tests/e2e/test_offline_pipeline.py` |

## Removal plan

| Version | Action |
| --- | --- |
| 0.2 | Both mechanisms added, warning on use. All repository configs migrated |
| 0.3 | No change. Warnings continue |
| 0.4 | `LEGACY_TASK_ALIASES` and `legacy_plan.py` deleted, with their tests |

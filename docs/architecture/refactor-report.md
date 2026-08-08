# Refactor report — 0.2 architecture consolidation

What changed, what it cost, and what is still open.

## The problem

The repository was a monorepo of seven internal Python distributions plus a root
project that packaged nothing:

```
packages/{common,pipeline_core,news_data,market_data,research_data,strategy_sdk}
apps/{trading_platform,relevance_annotation_ui}
```

Concretely, this meant:

- **Seven editable installs** to work on the repo, and the same seven in CI.
- **A packaging boundary standing in for an architectural one.** `news_data`
  contained a GDELT HTTP client, a generic BigQuery client, canonical article
  schemas, deterministic dedupe, rule-based entity matching, theme scoring, and a
  Looker Studio view builder. The package name told you none of that.
- **Duplication forced by the boundary.** `news_data/article/serialize.py` was a
  byte-for-byte copy of `market_data/domain/serialization.py`, duplicated
  specifically to avoid a package dependency.
- **A `common` package** holding five unrelated concerns — errors, config
  loading, an HTTP response cache, cloud-spend guardrails, and date-window
  resolution — each of which had a real owner elsewhere.
- **Import-time task registration.** Importing `trading_platform` mutated a
  module-level `TASK_REGISTRY` as a side effect, so what the application could do
  depended on import order.
- **Three pipeline config shapes** — `ingest`/`transform`/`strategy` sections
  plus two shorthand forms inside `transform`.
- **An empty package**, `strategy_sdk`, whose entire content was a docstring
  saying it was intentionally empty.

## What it is now

One distribution, one namespace, one CLI, one config shape, one lockfile.

```
src/kinetic/
  bootstrap.py    the composition root — the complete list of what this can do
  core/           pipeline runtime, config, artifacts, provenance
  data/           canonical schemas, catalog, storage
  ingestion/      gdelt, alpaca, bigquery, caching, cost
  processing/     deterministic transformation
  ml/             relevance benchmark and evaluation
  research/       event studies (including an event-vs-control contrast),
                  research datasets, reports
  interface/      the CLI, and nothing else
```

156 source files, all covered by mypy (previously 94, with news_data tasks,
reporting and gdelt excluded from the type-checked scope entirely).

Plus, outside the distribution: `projects/semiconductor_case_study/` (two
studies' configs, results and conclusions), `tools/` (the annotation
workstation, batch runner, fixture generators), and `warehouse/` (all generated
data, git-ignored).

## This attempt's history — read this before trusting a stale reference

This refactor was attempted once before, on branch
`claude/kinetic-trading-refactor-j7ljx4`, against an earlier baseline
(`cf5c9dd`). That attempt completed all six phases and reached 2222 passed / 4
skipped with a verified wheel build — but it ran in an ephemeral container whose
git credentials were read-only for the entire session. Every push attempt
returned `403`, and the container was gone before push access could be restored.
**None of that work is recoverable**; no bundle or patch was saved before the
container disappeared.

Two things happened on `origin/main` while that was going on:

- PR #6 added a real historical research study — semiconductor news-attention
  event-vs-control analysis — to `research_data`, including a new
  `report.py`, and extended `event_study.py`, `stats.py`, `validation.py` and
  `task.py`.
- PR #7 independently fixed the exact Streamlit AppTest relative-path bug that
  the lost attempt's baseline had recorded as its only pre-existing failure.

This attempt restarted from scratch against the resulting `aec8131`, in a
dedicated git worktree on a machine with working push credentials, and pushed
the branch **before any file was touched** — specifically so the branch could
never again exist only inside one container. Every subsequent checkpoint was
pushed immediately after validation, not batched.

The lost attempt's documents were used as a blueprint for the target
architecture, which needed no structural changes. Every claim about the *actual*
repository was re-verified against `aec8131` rather than assumed from memory; the
differences that re-audit found — mainly the new PR #6 material — are recorded in
[`migration-map.md`](migration-map.md).

## By the numbers

| | Before | After |
| --- | --- | --- |
| Installable distributions | 7 (+1 that packaged nothing) | 1 |
| Editable installs to develop | 7 | 1 |
| `pyproject.toml` files | 9 | 1 |
| Lockfiles | 0 | 1 (`uv.lock`) |
| Console scripts | `trading-platform` | `kinetic` |
| Pipeline config shapes | 3 | 1 |
| Import-time side effects | task registry populated on import | none |
| mypy coverage | 94 files (allow-list) | 156 files (whole package) |
| black coverage | ~60-entry allow-list | whole tree |
| Import boundary checks | none | 6 contracts, enforced |
| Dependency analysis | none | deptry, clean |
| Tests | 2146 passed, 0 failed, 14 skipped (baseline at aec8131) | 2254 passed, 4 skipped |

## The classifications that mattered

Most of the migration was mechanical. The decisions worth knowing:

**`common` was dissolved into four different subsystems, not renamed.** Errors
and config loading are platform mechanics (`core`). The response cache, spend
guardrails and date-window resolution exist *only* to serve provider calls, so
they went to `ingestion`. No replacement `utils` package was created.

**The generic BigQuery client was separated from GDELT's SQL.**
`SafeBigQueryClient`, the SQL guardrails and the result cache are not
GDELT-specific; they went to `ingestion/warehouse/bigquery/`. GDELT's queries,
count normalization and theme bundles stayed with the GDELT adapter.

**Deterministic scoring stayed out of `ml`.** Theme association scoring is
contingency-table arithmetic and theme classification is a rule table. Both
produce scores; neither involves a fitted model. They are in `processing`. The
relevance benchmark went to `ml` not because it scores things but because it is
the measurement apparatus for a model: sampling design, annotator calibration,
adjudication, agreement statistics, evaluation metrics.

**The new event-vs-control study (PR #6) needed one real classification
decision.** `research_data/report.py` — a new, top-level module distinct from
the existing `research_data/relevance/report.py` — turns an event-study summary
into a deterministic Markdown report with a fixed, non-overclaiming
interpretation vocabulary. It moved to `research/reports/study_report.py`,
alongside the existing `theme_review.py` report generator, because both are the
same *kind* of thing: a research report, not a schema, not a task, not ML. The
event-vs-control contrast math itself (`welch_two_sample_p`,
`two_sample_session_block_bootstrap_difference_ci`) stayed in `processing/stats.py`
next to the other lag-only estimators — it is pure two-sample arithmetic, not a
fitted model.

**`entity/matching.py` became `processing/news/entity_linking.py`.** The old name
understated it — this is the deterministic half of entity linking, and naming it
so makes clear where a statistical entity linker would eventually sit beside it.

**The BI reporting layer went to `ingestion`, not `research`.** It creates and
exports BigQuery views behind the same cost guardrails. It is a warehouse
operation, not a research method.

## Behavior changes

The refactor is behavior-preserving in its algorithms. Four externally visible
things changed:

1. **The console command** is `kinetic`, not `trading-platform`. No alias.
2. **Default output paths** moved into `warehouse/`: runs from `experiments/` to
   `warehouse/runs/`, stored articles and bars from `data/processed/` to
   `warehouse/normalized/`, article features to `warehouse/features/news/`, the
   cost ledger from `data/cost/` to `warehouse/cost/`. Private inputs under
   `data/real_corpus/` and `data/local_only/` are unchanged, deliberately —
   moving them would break existing local corpora.
3. **`run_plan` / `run_plan_from_file` are now `run_pipeline` /
   `run_pipeline_from_file`**, and the registry argument is required rather than
   defaulting to a global.
4. **A run's group directory is allocated from the plan name** in
   `core/artifacts.py` rather than inside the runner. Same naming, same
   `slug_2` collision behavior.

Compatibility for old task names and the old config shape is described in
[compatibility.md](../reference/compatibility.md), with a 0.4.0 removal date.

## Validation

| Check | Baseline (`aec8131`, this attempt) | Now |
| --- | --- | --- |
| `ruff check .` | pass | pass |
| `lint-imports` | did not exist | 6 contracts kept, 0 broken |
| `black --check` | pass (scoped) | pass (whole tree) |
| `mypy` | pass (95 files, scoped) | pass (156 files, whole package) |
| `deptry .` | did not exist | no issues |
| `pytest -q` | 2146 passed, 0 failed, 14 skipped | 2254 passed, 4 skipped |
| wheel build + isolated smoke | pass (6 wheels) | pass (1 wheel, plus CLI and package-data checks) |

Unlike the lost attempt's baseline (`cf5c9dd`, 6 failed), this attempt's baseline
at `aec8131` had **zero pre-existing failures** — PR #7 had already fixed the
Streamlit AppTest issue upstream. The skip count rose from 14 (at baseline) to 4
because most of the baseline's 14 skips were a parametrized guard correctly
skipping the semiconductor configs that are not BigQuery-GDELT configs; after the
case-study configs moved to `projects/`, that guard has fewer matching files to
skip in the general `configs/research/` sweep. The remaining 4 skips are the
gated live-Alpaca test plus tests that specifically require Streamlit's optional
runtime, all expected.

Test count rose net (+108, from 2146 to 2254) because reorganizing tests
mechanically preserved every existing test while phase 6 added two new suites:
`tests/unit/interface/test_cli.py` (the Typer CLI surface, config validation
errors, config migration, and parametrized validation of every checked-in
config including the new PR #6 semiconductor study configs) and
`tests/e2e/test_offline_pipeline.py` (a complete CLI-to-artifacts pipeline run,
a reproducibility check that tolerates only wall-clock-timestamp differences,
and a regression test proving a deprecated task name still runs a real
pipeline).

## Still open

**Semiconductor reference data inside the platform.**
`DEFAULT_SEMICONDUCTOR_SEEDS` (`data/catalog/seeds.py`) and
`default_semiconductor_entities()` (`data/catalog/entities.py`) are case-specific
data consumed as *defaults* by `ml/relevance/baselines.py` and the offline
fixtures. Moving them to `projects/` would make `kinetic.ml` depend on
`projects/`. The clean fix is to make the baselines take their seed vocabulary as
a required argument — an API change deliberately not bundled into a structural
refactor.

**Semiconductor vocabulary inside a rule set.** The bucket rules in
`processing/news/themes/classification.py` are written around chip and
semiconductor terms even though the surrounding machinery is topic-agnostic.
Extracting the vocabulary to config is straightforward and not done here.

**`processing` sits below `ingestion` in the declared layering.** This is not the
reading order in the docs, and it deserves the explanation it carries in
`pyproject.toml`: an adapter reuses deterministic normalization when mapping a
payload into a canonical record, while no processing module imports a provider.
The contract still forbids the dangerous direction.

**`kinetic.trading` does not exist**, so the contract forbidding
`research -> trading` passes vacuously. It starts doing real work the day the
package appears.

**The semiconductor news-attention study has no completed, recorded result.**
Running it for real requires live BigQuery and Alpaca credentials and a manual
theme-discovery review step. The methodology, the offline dry-run path, and the
fixed non-overclaiming report vocabulary all exist and are tested; the actual
historical finding does not exist in this repository yet.

## Where to look next

Five files, in order:

1. `src/kinetic/bootstrap.py`
2. `src/kinetic/core/pipeline/runner.py`
3. `src/kinetic/core/pipeline/plan.py`
4. `src/kinetic/interface/cli/app.py`
5. [`execution-flow.md`](execution-flow.md)

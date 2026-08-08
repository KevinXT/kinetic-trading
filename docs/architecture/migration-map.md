# Migration map

Every package, directory and significant module in the pre-refactor repository
at baseline commit `aec8131`, where it is going, and why it is classified that
way.

The "why" column is the point of this document. Several modules do **not** go
where their old name suggests.

This map was originally drafted against an earlier commit (`cf5c9dd`) in a
session whose work was lost before it could be pushed (see
[`refactor-progress.md`](refactor-progress.md) for that history). It has been
re-audited against the actual repository at `aec8131`, which additionally
includes PR #6 (the semiconductor event-vs-control attention study) and PR #7
(a Streamlit AppTest path fix). The re-audit found the original classifications
for everything in `packages/common`, `packages/pipeline_core`,
`packages/market_data`, `packages/news_data`, `packages/strategy_sdk`,
`apps/trading_platform` and `apps/relevance_annotation_ui` still hold exactly —
none of those paths changed between the two commits. Only `packages/research_data`
and its associated configs/docs/scripts/tests gained new material, captured
below.

## Distributions

| Before | After |
| --- | --- |
| `kinetic-trading-workspace` (root, dev tools only, `packages = []`) | `kinetic` — the one authoritative, installable distribution |
| `kinetic-common` (`packages/common`) | dissolved |
| `kinetic-pipeline-core` (`packages/pipeline_core`) | `kinetic.core` |
| `kinetic-news-data` (`packages/news_data`) | split across `data`, `ingestion`, `processing`, `research` |
| `kinetic-market-data` (`packages/market_data`) | split across `data`, `ingestion` |
| `kinetic-research-data` (`packages/research_data`) | split across `data`, `processing`, `ml`, `research` |
| `kinetic-strategy-sdk` (`packages/strategy_sdk`) | **deleted** |
| `kinetic-trading-platform` (`apps/trading_platform`) | `kinetic.interface.cli` + `kinetic.bootstrap` |
| `kinetic-relevance-annotation-ui` (`apps/relevance_annotation_ui`) | `tools/annotation/` (repo-only, not a distribution) |

Seven editable installs become one. The console script `trading-platform`
becomes `kinetic`.

## `packages/pipeline_core` → `kinetic.core`

| Before | After | Why |
| --- | --- | --- |
| `engine/runner.py` | `core/pipeline/runner.py` | the execution loop |
| `engine/parser.py` | `core/pipeline/plan.py` | renamed: it produces a `Plan`, and "parser" said nothing about what it parsed |
| `engine/context.py` | `core/pipeline/context.py` | |
| `engine/hooks.py` | `core/pipeline/hooks.py` | |
| `tasks/base.py` | `core/pipeline/task.py` | the task contract |
| `tasks/registry.py` | `core/pipeline/registry.py` | rewritten: `TaskRegistry` becomes an instance, not a module-level dict |
| `engine/run_metadata.py` — `write_resolved_config`, `write_run_metadata` | `core/artifacts.py` | writing run outputs is artifact handling |
| `engine/run_metadata.py` — `utc_now_iso`, `git_head_sha`, `find_git_root`, `git_head_sha_near` | `core/provenance.py` | recording *where a run came from* is provenance, not artifact I/O. The old module mixed the two |
| `engine/run_paths.py` | `core/artifacts.py` | run-directory naming and allocation is artifact placement |

## `packages/common` → dissolved

`common` is the classic shared-utilities package: five unrelated concerns kept
together only because more than one caller needs them. Each module moves to the
subsystem that actually owns it.

| Before | After | Why |
| --- | --- | --- |
| `common/errors.py` | `core/errors.py` | platform-level exception hierarchy |
| `common/config_builder.py` | `core/config.py` | configuration loading is platform mechanics |
| `common/cache.py` | `ingestion/caching.py` | a cache-aside store for **HTTP/JSON provider responses**. Its only callers are the Alpaca provider, the GDELT DOC task and the BigQuery result cache. It is not general-purpose caching |
| `common/cost/{policy,estimate,ledger,report}.py` | `ingestion/cost/` | guardrails on **external cloud spend**: dry-run before executing, cap bytes billed, log every decision. Nothing in the platform costs money except talking to a provider |
| `common/date_windows.py` | `ingestion/windows.py` | turns a config `window:` block into a concrete date range for a **provider query**. Its only callers are BigQuery query builders and tasks |

No `kinetic.common`, `kinetic.shared`, `kinetic.utils` or `kinetic.misc` is
created to replace it.

## `packages/market_data`

| Before | After | Why |
| --- | --- | --- |
| `domain/models.py` — `Instrument`, `normalize_cik` | `data/schemas/instruments.py` | canonical instrument identity |
| `domain/models.py` — `PriceBar`, `MarketEvent`, `DEFAULT_PRICE_BAR_CURRENCY` | `data/schemas/market.py` | canonical market records |
| `domain/models.py` — `MacroObservation` | `data/schemas/macro.py` | canonical macro records; the target file exists before any macro provider does |
| `domain/models.py` — `FilingEvent`, `FinancialFact` | `data/schemas/fundamentals.py` | fundamentals are neither market nor macro |
| `domain/serialization.py` | `data/serialization.py` | deterministic JSON for canonical records |
| `domain/requests.py` | `ingestion/requests.py` | `BarsRequest` and friends describe **what to ask a provider for**. They are provider-independent, but they are ingestion contracts, not canonical data |
| `instruments.py` (`InMemoryInstrumentResolver`) | `data/catalog/instruments.py` | resolving a symbol against a curated master is catalog metadata, not a schema |
| `providers/protocols.py` | `ingestion/protocols.py` | the provider interface |
| `providers/registry.py` | `ingestion/registry.py` | the provider factory registry |
| `providers/alpaca/*` | `ingestion/market/alpaca/*` | provider adapter, visibly isolated |
| `providers/alpaca/task.py` | `ingestion/market/alpaca/tasks.py` | provider-specific ingestion task |
| `storage/{base,errors,jsonl}.py` | `data/storage/` | storage interfaces and implementations for canonical data |

## `packages/news_data`

| Before | After | Why |
| --- | --- | --- |
| `gdelt/*` (client, config, schemas, normalize, endpoints, parsing, query) | `ingestion/news/gdelt/` | the GDELT DOC API adapter |
| `task/gdelt_docs.py` | `ingestion/news/gdelt/tasks.py` | provider-specific ingestion task |
| `article/models.py` (`ArticleTextRecordV1`) | `data/schemas/news.py` | canonical news record |
| `article/normalize.py` | `processing/news/normalize.py` | deterministic URL/text normalization and stable id derivation — same input, same output, no model |
| `article/serialize.py` | **deleted** | byte-for-byte duplicate of `market_data/domain/serialization.py`, duplicated only to avoid a package dependency that no longer exists. Callers use `data/serialization.py` |
| `article/corpus.py` | `ingestion/news/local_corpus.py` | imports article text from a local rights-cleared corpus — an ingestion source that happens not to use HTTP |
| `entity/models.py` (`EntityReferenceV1`) | `data/schemas/entities.py` | canonical entity record |
| `entity/matching.py` | `processing/news/entity_linking.py` | **deterministic** alias matching with token boundaries. Renamed because "matching" understates what it is: this is the rules-based half of entity linking |
| `entity/reference.py` | `data/catalog/entities.py` | loads an entity reference set; see the limitation note in `dependency-rules.md` |
| `dedupe/{exact,near}.py` | `processing/news/dedupe/` | deterministic clustering |
| `bigquery/client.py`, `sql_guardrails.py`, `cache.py` | `ingestion/warehouse/bigquery/` | a **generic** cost-guarded BigQuery client. Nothing about it is GDELT-specific |
| `bigquery/gdelt_queries.py` | `ingestion/news/gdelt/bigquery/queries.py` | GDELT-specific SQL — belongs with the GDELT adapter, not with the BigQuery client |
| `bigquery/normalize_counts.py` | `ingestion/news/gdelt/bigquery/normalize_counts.py` | provider response → canonical mapping |
| `bigquery/theme_bundles.py` | `ingestion/news/gdelt/bigquery/theme_bundles.py` | GDELT theme vocabulary used to build queries |
| `bigquery/seed_matching.py` — seed vocabulary (`SeedTerm`, kinds, normalization, `DEFAULT_SEMICONDUCTOR_SEEDS`) | `data/catalog/seeds.py` | what a seed *is*, plus the sets shipped. Reference data, so `data` can own it without importing anything |
| `bigquery/seed_matching.py` — SQL predicate generation | `ingestion/news/gdelt/bigquery/seed_predicates.py` | it emits BigQuery SQL, so it belongs with the query builder that is its only consumer. Splitting it from the vocabulary keeps `data` from sitting underneath `processing` |
| `bigquery/theme_classification.py` | `processing/news/themes/classification.py` | a transparent rule table, no fitted model |
| `bigquery/theme_scoring.py` | `processing/news/themes/scoring.py` | pure contingency-table arithmetic. Producing a score does not make it ML |
| `bigquery/theme_review.py` | `research/reports/theme_review.py` | generates a human-review worksheet — a research report artifact |
| `task/bigquery_gdelt_counts.py` | `ingestion/news/gdelt/bigquery/tasks/counts.py` | |
| `task/bigquery_gdelt_theme_discovery.py` | `ingestion/news/gdelt/bigquery/tasks/theme_discovery.py` | |
| `task/bigquery_gdelt_seeded_theme_discovery.py` | `ingestion/news/gdelt/bigquery/tasks/seeded_theme_discovery.py` | |
| `task/bigquery_gdelt_seeded_theme_scoring/` | `ingestion/news/gdelt/bigquery/tasks/seeded_theme_scoring/` | |
| `task/{filter,dedupe,tag}_articles.py`, `aggregate_article_features.py`, `store_{articles,features}.py` | `processing/news/tasks/` | deterministic transformation steps |
| `reporting/` | `ingestion/warehouse/bigquery/reporting/` | creates and exports **BigQuery views**. It is a warehouse operation behind the same cost guardrails, not a research method |

## `packages/research_data`

The old name covers four different responsibilities, and — as of PR #6 — a
fifth: a real historical event-vs-control study with its own report generator.

| Before | After | Why |
| --- | --- | --- |
| `models.py` | `data/schemas/research.py` | canonical research-layer records and the dataset manifest schema |
| `catalog.py` | `data/catalog/features.py` | the feature catalog is dataset metadata |
| `manifest.py` | `data/catalog/manifest.py` | manifest construction and reproducibility fingerprinting |
| `mappings.py` | `data/catalog/mappings.py` | versioned topic→instrument reference mappings |
| `stats.py` | `processing/stats.py` | dependency-free lag-only estimators, plus (as of PR #6) `welch_two_sample_p` and `two_sample_session_block_bootstrap_difference_ci` — pure two-sample statistical arithmetic with no fitted model, same classification as everything else in the file |
| `calendar.py` | `processing/market/calendar.py` | deterministic session calendar |
| `market_features.py` | `processing/market/features.py` | deterministic feature construction |
| `news_features.py` | `processing/news/features.py` | deterministic feature construction |
| `alignment.py` | `processing/cross_asset/alignment.py` | news-time → market-session alignment policy |
| `join.py` | `processing/cross_asset/join.py` | leakage-aware join |
| `builder.py` | `research/datasets/builder.py` | orchestrates the deterministic layers **and then runs the event study**. It cannot live in `processing` without making `processing` depend on `research`; it is the research-dataset builder, and always was |
| `validation.py` | `processing/cross_asset/validation.py` | leakage/availability audits, including (as of PR #6) `assert_required_bar_symbols` — a pre-build check that a study's required instrument/benchmark bars are present. Called *by* `research/tasks.py`, so it cannot live in `research` without inverting the dependency |
| `event_study.py` | `research/event_studies/event_study.py` | evaluation of an idea. As of PR #6 this module also builds the **event-versus-control contrast** — attention-group classification, session-date clustering, and the confirmatory two-sample test per response variable. Same classification: it is still evaluation machinery over a `NewsMarketObservation` sequence, deterministic given its inputs and a fixed seed |
| `report.py` **(new, PR #6)** | `research/reports/study_report.py` | turns an event-study summary into a deterministic Markdown study report plus flat CSV/JSON contrast exports, using a fixed interpretation vocabulary that cannot describe profitability or causation. This is a research report generator, the same class of thing as `theme_review.py` — both move to `research/reports/` |
| `task.py` | `research/tasks.py` | the `research.build_news_market_dataset` task. As of PR #6 it also writes the event-vs-control contrast artifacts and, when a `study_report_name` param is given, renders the human-readable study report — still one orchestration task, same classification |
| `relevance/*` | `ml/relevance/*` | see below |
| `relevance/models.py` | `ml/relevance/schemas.py` | renamed: they are annotation schemas, and `models.py` in an ML package reads as "trained models" |
| `relevance_benchmark_task.py` | `ml/relevance/benchmark_task.py` | |
| `real_corpus_pilot_task.py` | `ml/relevance/pilot_task.py` | |

`relevance/` moves to `ml` as a unit — `annotation.py`, `sampling.py`,
`splits.py`, `eligibility.py`, `provenance.py`, `content_controls.py`,
`duplicate_calibration.py`, `metrics.py`, `baselines.py`, `report.py`,
`reconciliation.py` and the six `pilot_*.py` modules. (Note: `relevance/report.py`
and the new top-level `research_data/report.py` from PR #6 are two different
files with the same basename in different subtrees — they move to different
destinations, `ml/relevance/report.py` vs `research/reports/study_report.py`,
and must not be confused during the move.) It is the measurement apparatus for a
relevance model: how examples are sampled, how annotators are calibrated and
adjudicated, how agreement is estimated, and how a predictor is scored.
`baselines.py` is deterministic, and stays with the metrics that score it rather
than being separated from its only consumer.

## Applications

| Before | After | Why |
| --- | --- | --- |
| `apps/trading_platform/.../__init__.py::_register_tasks()` (runs at import time) | `src/kinetic/bootstrap.py::build_default_registry()` (called explicitly) | importing a package must not mutate global state |
| `apps/trading_platform/.../__main__.py` | `interface/cli/app.py` + `interface/cli/commands/` | |
| `apps/relevance_annotation_ui/` | `tools/annotation/` | a local-only supporting application, not part of pipeline execution |

## Scripts

| Before | After | Why |
| --- | --- | --- |
| `scripts/run_collections.py` | `tools/run_collections.py` | batch runner over `configs/collections`, not part of a pipeline |
| `scripts/generate_research_fixtures.py`, `generate_relevance_pilot_fixtures.py` | `tools/fixtures/` | deterministic fixture generation for tests |
| `scripts/generate_semiconductor_study_fixtures.py` **(new, PR #6)** | `tools/fixtures/` | same category as the other two: a deterministic, offline generator that produces committed test fixtures (`tests/fixtures/research/semiconductor_study_bigquery_counts.json`, `semiconductor_study_market_bars.json`). It is named after its subject but its *role* is fixture generation, not case-study result analysis — kept consistent with the other two generators rather than filed under `projects/` |
| `scripts/analyze_seeded_theme_candidates.py`, `build_theme_review_worksheet.py` | `projects/semiconductor_case_study/scripts/` | case-specific offline analysis of that study's artifacts |
| `scripts/dev/*.sh` | unchanged in place | developer/release tooling, updated for the single distribution |

## Case study material

| Before | After |
| --- | --- |
| `configs/research/semiconductor*.yaml`, `semiconductors_*.yaml` (theme discovery/scoring) | `projects/semiconductor_case_study/configs/` |
| `configs/research/semiconductors_alpaca_bars.yaml` **(new, PR #6)** | `projects/semiconductor_case_study/configs/` |
| `configs/research/semiconductors_bigquery_1y_partition_{dryrun,execute}.yaml` **(new, PR #6)** | `projects/semiconductor_case_study/configs/` |
| `configs/research/semiconductors_news_market_study_{offline,v1}.yaml` **(new, PR #6)** | `projects/semiconductor_case_study/configs/` |
| `docs/research/semiconductor-theme-scoring/` (results, quality report, cost decisions, sample CSVs) | `projects/semiconductor_case_study/results/` |
| `docs/research/semiconductor_relevance_*.md` (annotation guidelines, benchmark design, pilot protocol) | `projects/semiconductor_case_study/docs/` |
| `docs/research/semiconductor_attention_study.md` **(new, PR #6)** | `projects/semiconductor_case_study/docs/` — this is the operational runbook for the event-vs-control study: instruments (AMD, NVDA, SMH), benchmark (QQQ), news/market windows, and the fixed pre-registered specification. It documents a real historical study, not fixture-only software validation, and its conclusions must be preserved exactly as recorded, same as the theme-scoring verdict |

The negative theme-scoring result is preserved verbatim. The event-vs-control
study's actual result (supported / inconclusive / no-difference, per response) is
whatever the study report says — it must not be summarized more favorably during
the move. See `projects/semiconductor_case_study/README.md`.

## Generated and preserved outputs

| Before | After |
| --- | --- |
| `experiments/` (git-ignored run outputs) | `warehouse/runs/` (git-ignored) |
| `data/processed/articles/`, `data/processed/market/` | `warehouse/normalized/news/`, `warehouse/normalized/market/` |
| `data/raw/` | `warehouse/raw/` |
| `data/cost/cost_ledger.jsonl` | `warehouse/cost/cost_ledger.jsonl` |
| `data/real_corpus/`, `data/local_only/` | unchanged — these are private *inputs*, not generated output, and moving them would break existing local corpora |
| `docs/research/semiconductor-theme-scoring/*` (tracked, real study results) | `projects/semiconductor_case_study/results/` — **tracked**, not treated as disposable |

## New test material to classify (PR #6)

| New file | Target | Why |
| --- | --- | --- |
| `tests/test_research_event_control.py` | `tests/unit/research/` | tests the event-vs-control contrast machinery in `event_study.py` — offline, deterministic, same placement as the existing `test_research_event_study.py` |
| `tests/test_research_study_report.py` | `tests/unit/research/` | tests `report.py` → `research/reports/study_report.py`; offline, deterministic |
| `tests/test_semiconductor_study_configs.py` | `tests/unit/research/` | validates the shape and values of the new semiconductor study configs, following the precedent already set by `test_semiconductor_theme_discovery_refined.py` (kept in `tests/unit/research/` rather than moved beside `projects/`, for consistency — the annotation-UI tests were kept under `tests/unit/tools/`/`tests/integration/` rather than beside `tools/annotation/` for the same reason) |
| `tests/test_research_event_study.py` (modified, +19/-? lines) | `tests/unit/research/` | already tracked in the original map; content extended for the new contrast fields, same destination |

New fixtures `tests/fixtures/research/semiconductor_study_bigquery_counts.json`
and `semiconductor_study_market_bars.json` stay under `tests/fixtures/research/`,
consistent with every other fixture in the repository — fixtures are not
relocated into `projects/`, regardless of subject matter.

## Deleted

| Path | Evidence |
| --- | --- |
| `packages/strategy_sdk/` | The entire package is one `__init__.py` containing a docstring that reads "Status: deferred. This package is intentionally empty and is not installed by CI, wheels-smoke, or the standard developer install path." No module imports it |
| `packages/news_data/src/news_data/article/serialize.py` | Byte-identical to `market_data/domain/serialization.py` minus two docstrings and `record_to_dict` |
| `packages/*/pyproject.toml`, `apps/*/pyproject.toml` | Consolidated into the root `pyproject.toml` after their dependencies are merged |
| `requirements.txt` | Contains no requirements — only a comment saying it is deprecated |

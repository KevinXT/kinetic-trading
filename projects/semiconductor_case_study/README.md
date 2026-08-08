# Semiconductor case study

Two related research programmes: can news about the semiconductor industry be
identified reliably enough to be useful, and — separately — is unusually high
semiconductor news coverage associated with larger price moves in semiconductor
names.

This directory holds everything that is **specific to these questions** — their
configs, their inputs, their preserved results, and their conclusions. The
reusable machinery each one produced lives in the platform, not here.

## What was learned

### 1. GDELT theme codes as an identity layer — **rejected**

The hypothesis: GDELT's GKG theme codes could act as a primary "this article is
about semiconductors" label, by finding themes statistically enriched in a
semiconductor-seeded subcorpus relative to a matched background corpus.

The result, over a real 30-day window (40,334 seeded records against 7,607,554
background records, 1,359 support-qualified candidate themes, BH-FDR over the
complete family):

> **`industry_core` classifications: 0. Themes promoted to the production bundle: 0.**

Many themes were statistically enriched around semiconductor organizations —
manufacturing, servers, storage, inflation, currencies, monetary policy. None
behaved as a reliable semiconductor *identity* label. The production
`semiconductors` theme bundle was left empty, and the task deliberately cannot
promote themes automatically.

Bounded conclusion, for this window and this corpus:

> Semiconductor relevance should be established through entity resolution and
> text-based classification. GDELT themes may remain useful as contextual
> features, not as the primary identity layer.

Full detail, including the cost artifacts and the exact statistical procedure, is
in [`docs/theme-scoring-study.md`](docs/theme-scoring-study.md) and
[`results/`](results/). **Do not rewrite these to imply the approach worked.**

### 2. A relevance benchmark and annotation pilot — **infrastructure complete, no model yet**

Following directly from that negative result, the second effort builds the
apparatus a relevance *model* would have to be measured against: a sampling
design with explicit probability and challenge strata, quantitative sample-size
planning, annotator calibration and adjudication, inter-annotator agreement with
bootstrap confidence intervals, duplicate-threshold calibration, and evaluation
metrics that return null with a reason rather than a silent zero.

What exists is the measurement apparatus and deterministic reference baselines.
**No trained relevance model exists**, and no claim is made about predictive
value or profitability.

### 3. Semiconductor news-attention event-vs-control study — **methodology built, no completed run recorded**

A separate, real historical research question: when semiconductor news coverage
is unusually high relative to the preceding 30 sessions, do AMD, NVDA and SMH
experience larger target-session price moves or range volatility than a QQQ
benchmark and non-event control sessions?

This is a complete pipeline — GDELT-over-BigQuery daily counts, Alpaca daily
bars, a leakage-aware join, and a confirmatory event-versus-control contrast
(Welch's test plus a session-block bootstrap CI, with Benjamini–Hochberg applied
only to the predeclared confirmatory family). Running it for real requires live
BigQuery and Alpaca credentials and a manual theme-discovery review step; that
has not been done inside this repository, so **no completed result is recorded
here yet**. The offline dry-run path exercises the exact same code deterministically
against synthetic fixtures, for software validation only — it proves nothing
about real markets.

The report generator's interpretation vocabulary is fixed to
`evidence supports an association` / `result is inconclusive` /
`no detectable difference` / `insufficient sample` — it structurally cannot claim
causation or profitability, whatever a future run finds. See
[`docs/semiconductor_attention_study.md`](docs/semiconductor_attention_study.md)
for the full specification, methodology and limitations.

## Layout

| Path | Contents |
| --- | --- |
| `configs/` | Case-specific pipeline configs: seeded theme discovery and scoring, the offline relevance benchmark, the real-corpus annotation pilot, the annotation UI, and the semiconductor news-attention study (theme discovery, one-year BigQuery counts, Alpaca bars, and the joined study build — offline and live variants) |
| `results/` | Preserved study outputs — **tracked**, not generated: the theme-scoring summary, the research quality report, BigQuery cost estimate and decision, and sample diagnostics |
| `docs/` | The theme-scoring study write-up, annotation guidelines, benchmark design, the real-corpus pilot protocol, and the attention-study runbook |
| `scripts/` | Offline analysis of this study's artifacts: candidate scoring analysis and theme review worksheet generation |

`results/` is deliberately outside `warehouse/`. Raw run directories are large,
may contain resolved local configuration, and are git-ignored; this is a curated,
sanitized snapshot of the real paired dry-run and live execution, and it is
tracked because a study's findings are not disposable output.

## Running it

The offline pieces need no credentials, no network and no cloud account:

```bash
kinetic run projects/semiconductor_case_study/configs/semiconductor_relevance_benchmark_offline.yaml \
  --run-id benchmark

kinetic run projects/semiconductor_case_study/configs/semiconductor_relevance_real_corpus_pilot_local.yaml \
  --run-id pilot

kinetic run projects/semiconductor_case_study/configs/semiconductors_news_market_study_offline.yaml \
  --run-id study_offline
```

The BigQuery and Alpaca configs cost real money and are guarded accordingly. The
`_dryrun` variants estimate bytes and cost and fetch nothing; the `_execute`
variants require a typed confirmation value in the config before they will run:

```bash
kinetic run projects/semiconductor_case_study/configs/semiconductors_seeded_theme_scoring_30d_dryrun.yaml
```

Set a real project id and Alpaca credentials in `configs/local.yaml` and the
environment (git-ignored) rather than editing the committed configs.

## What is reusable, and where it went

The studies' *methods* are platform code; only their *subject* is here.

| Reusable component | Now lives in |
| --- | --- |
| Seeded theme discovery and scoring SQL, cost guardrails | `kinetic.ingestion.news.gdelt.bigquery`, `kinetic.ingestion.warehouse.bigquery` |
| Association scoring, theme classification rules | `kinetic.processing.news.themes` |
| Exact and near-duplicate clustering, entity linking | `kinetic.processing.news` |
| Sampling, splits, annotation, adjudication, agreement, evaluation metrics | `kinetic.ml.relevance` |
| Human-review worksheet generation | `kinetic.research.reports.theme_review` |
| Event-vs-control contrast statistics (Welch's test, session-block bootstrap) | `kinetic.processing.stats`, `kinetic.research.event_studies.event_study` |
| The deterministic study-report generator and its fixed interpretation vocabulary | `kinetic.research.reports.study_report` |
| The annotation workstation | `tools/annotation` |

Two pieces of this study's *reference data* are still inside the platform:
`DEFAULT_SEMICONDUCTOR_SEEDS` in `kinetic/data/catalog/seeds.py` and
`default_semiconductor_entities()` in `kinetic/data/catalog/entities.py`. They are
consumed as defaults by the ML baselines and the offline fixtures, so moving them
here would invert the dependency direction. That limitation is recorded in
[`dependency-rules.md`](../../docs/architecture/dependency-rules.md), along with
the clean fix.

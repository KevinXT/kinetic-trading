# Semiconductor News-Attention Market Study (v1) — Runbook

This is the operational runbook for the first real historical research study in
the repository:

> When semiconductor news coverage is unusually high relative to the preceding
> 30 sessions, do AMD, NVDA, and SMH experience larger target-session price
> movements or range volatility?

It is a **research-data engineering** study. It builds a leakage-aware,
reproducible dataset and reports an explicit **event-versus-control** contrast.
It does not implement trading, execution, or any profitability claim.

## Study specification (fixed before inspecting outcomes)

| Item | Value |
| --- | --- |
| Topic | `semiconductors` |
| Instruments | AMD, NVDA, SMH |
| Benchmark | QQQ |
| News measurement window | 2025-07-01 … 2026-06-30 |
| Market-bar window | 2025-05-15 … 2026-07-10 |
| Market timeframe | 1Day |
| Alpaca feed / adjustment / currency | IEX / all / USD |
| News method | `bigquery_gdelt_counts` (daily approximation) |
| Alignment policy | `conservative_calendar_day_v2` |
| Primary responses | `target_session_absolute_return`, `target_session_parkinson_variance` |
| Secondary responses | `target_session_benchmark_adjusted_return`, `target_through_plus_4_cumulative_benchmark_adjusted_return` |
| Event threshold (lag-only) | `news_attention_zscore_30 >= 1.5` |
| Minimum inference sample | 15 independent session-date clusters per group |
| Bootstrap | unit = session date, block = 5, seed = 12345, iterations = 2000 |

The event definition is a **lag-only** rule fixed as a predeclaration. The
30-session attention z-score is computed only from strictly prior sessions, so an
event can never include the shock it classifies, and the threshold was not chosen
by inspecting which value maximizes any response.

## Event-versus-control methodology

The confirmatory H1 test is **not** a one-sample test of the event mean against
zero. That would not test whether attention-event sessions move more than
ordinary sessions. Instead, for each response and eligible grouping we compute an
explicit contrast:

- **Event group** — observations with a defined 30-session attention z-score at
  or above the threshold.
- **Control group** — observations with a defined 30-session attention z-score
  strictly below the threshold (ordinary sessions with valid attention history).
- Both groups share the same topic and news-measurement method, must have the
  response available (a complete target), and must fall inside the declared study
  sample. Observations without a defined 30-session history are neither event nor
  control and are excluded.

For every response and eligible grouping we report: event/control independent
session-date cluster counts, event/control row counts, event/control means, the
absolute difference (`event_mean - control_mean`), the ratio where valid, a
session-date moving-block bootstrap CI for the difference, an inferential status,
an uncorrected Welch p-value where defensible, and a BH-adjusted p-value only
within the predeclared H1 endpoint family.

- **Pooled** contrasts aggregate within session date first, so one session with
  AMD, NVDA, and SMH counts as one event, not three.
- **Symbol-specific** contrasts never mix symbols (event AMD is compared only to
  control AMD).
- Groups below the minimum sample receive descriptive results only:
  `inferential_status = insufficient_sample`, null formal p-values, and exclusion
  from BH-FDR.

The event-only distribution summaries are retained but clearly labelled
descriptive; they are not the hypothesis test.

## Theme discovery: live result, refined pass, and seeded fallback

Building the `semiconductors` theme bundle is a **human-review-gated** step. It is
not automated, and the bundle stays empty and non-executable until a reviewer
records validated codes. No one-year count execution (steps 4–5) may run while
the bundle is empty — `get_bundle_themes` raises by design.

### First live 90-day discovery (rejected)

The first live run of `semiconductors_theme_discovery_90d_execute` (run id
`semiconductors_theme_discovery_90d_execute`) scanned **26.9384 GiB** (estimated
**$0.16441915**, `maximum_bytes_billed` = 32,000,000,000) and returned exactly one
theme:

| theme | count | decision |
| --- | --- | --- |
| `TAX_FNCACT_FOUNDRYMAN` | 29 | **rejected — false positive** |

**Why it matched.** Name-based discovery matched patterns as case-insensitive
*substrings* of normalized theme codes (`LOWER(theme) LIKE '%foundry%'`). The bare
pattern `foundry` is a substring of `tax_fncact_foundryman`, so it matched the
functional-actor/occupation theme for a *foundryman* (a foundry worker) — not
semiconductor fabrication or chip foundries. It is recorded under
`excluded_ambiguous_theme_codes` in `configs/gdelt_theme_bundles.yaml` and is
**never** added to `themes`.

### Refined pass (safer, cheaper)

The discovery configs now:

- use **`match_mode: token`** — a pattern matches only a whole `_`-delimited
  segment of a theme code, so `foundry` no longer matches `FOUNDRYMAN`;
- drop the unsafe bare patterns `foundry`, `wafer`, and `lithography` (as
  standalone substrings they match occupations/materials unrelated to chips) in
  favor of a **targeted** set (multi-token `wafer_fabrication` / `photolithography`
  are safe under token matching);
- carry `known_rejected_theme_codes: [TAX_FNCACT_FOUNDRYMAN]`, so a reappearance
  is surfaced in `bigquery_summary.json` as `rejected_known_false_positive`.

`semiconductors_theme_discovery_refined_30d_*` runs the same safe patterns over a
`last_30_days` window (~1/3 the bytes) with a distinct run id, so the 90-day
artifacts are preserved. If it returns **zero** themes, the summary emits a
warning steering you to the seeded fallback rather than to broader substrings.

### Seeded (record-based) fallback

If theme-name discovery still finds nothing usable, GDELT may simply expose no
clean taxonomy code containing "semiconductor". `semiconductors_seeded_theme_discovery_30d_*`
takes the opposite approach: it finds GKG *records* mentioning semiconductor seed
terms (company/topic names such as `NVIDIA`, `TSMC`, `ASML`) in a free-text column
(`V2Organizations` by default), extracts the theme codes those records carry, and
ranks candidate themes by seeded-record count. It emits
`seeded_theme_candidates.{csv,jsonl}` and `seeded_theme_discovery_summary.json`
for review. Frequency of co-occurrence is a ranking signal, **not** proof of
semantic relevance, and this path **never** edits the bundle.

### Seeded-discovery audit and association scoring (seeded-v2)

The first seeded execute run (`semiconductors_seeded_theme_discovery_30d_execute`)
returned 200 candidates ranked by raw co-occurrence frequency. An audit found the
ranking dominated by ubiquitous generic themes (`TAX_ECON_PRICE`,
`EPU_ECONOMY_HISTORIC`, `LEADER`) with **no** semiconductor-specific code, plus
three query defects: unrestricted substring seed matching (`intel` matched
`intelligence`), `COUNT(*)` over unnested themes (within-record double counting),
and alphabetical sample sources. See the offline re-analysis under
`warehouse/runs/semiconductors_seeded_theme_candidate_analysis/` (run
`scripts/analyze_seeded_theme_candidates.py`) and the report it emits.

`seeded-v2` (`kinetic.ingestion.news.gdelt.bigquery.queries`, `kinetic.data.catalog.seeds`) fixes matching
(company aliases matched as whole tokens, industry phrases as substrings),
counts `COUNT(DISTINCT GKGRECORDID)` with per-record theme dedup, and samples
most-frequent sources. Raw frequency is still not relevance: the
`bigquery_gdelt_seeded_theme_scoring` task (configs
`semiconductors_seeded_theme_scoring_30d_{dryrun,execute}.yaml`) computes seeded
**and** background unique-record counts in one scan so lift / smoothed log-lift /
odds ratio are computable (`kinetic.processing.news.themes.scoring`). It emits
`candidate_scoring_sql.sql` + `theme_candidate_scores.{csv,jsonl}` for review and
**never** edits the bundle.

### Manual review process (required before any count execution)

1. Run the dry run; inspect estimated bytes and cost.
2. Execute (typed gate) once the estimate is safe.
3. Turn the discovery output into a review worksheet:

   ```bash
   python3 scripts/build_theme_review_worksheet.py \
     --input warehouse/runs/.../artifacts/theme_discovery.csv \
     --run-id semiconductors_theme_discovery_refined_30d_execute \
     --output theme_review_worksheet.csv
   ```

4. Review each candidate; **reject** occupation, geographic, generic-technology,
   and broad-economic themes (known false positives are pre-filled as `reject`).
5. Populate the `semiconductors` bundle in `configs/gdelt_theme_bundles.yaml`
   manually with the kept codes.
6. Record selection and exclusion rationales (`selected_theme_codes`,
   `excluded_ambiguous_theme_codes`, `source_discovery_run_id`, `review_date`).
7. Only then rerun the one-year count dry run (step 4).

### Theme-discovery commands

```bash
# Refined name-based discovery — DRY RUN (safe), then EXECUTION (typed gate).
kinetic run \
  configs/research/semiconductors_theme_discovery_refined_30d_dryrun.yaml \
  --run-id semiconductors_theme_discovery_refined_30d_dryrun

kinetic run \
  configs/research/semiconductors_theme_discovery_refined_30d_execute.yaml \
  --run-id semiconductors_theme_discovery_refined_30d_execute

# Seeded record-based fallback — DRY RUN (safe), then EXECUTION (typed gate).
kinetic run \
  configs/research/semiconductors_seeded_theme_discovery_30d_dryrun.yaml \
  --run-id semiconductors_seeded_theme_discovery_30d_dryrun

kinetic run \
  configs/research/semiconductors_seeded_theme_discovery_30d_execute.yaml \
  --run-id semiconductors_seeded_theme_discovery_30d_execute
```

## Fixed run IDs and artifact paths

Use these fixed `--run-id` values so artifact paths are predictable and the final
build config can consume them without editing:

| Step | Config | `--run-id` | Key artifact |
| --- | --- | --- | --- |
| Theme discovery (dry-run) | `configs/research/semiconductors_theme_discovery_90d_dryrun.yaml` | `semiconductors_theme_discovery_90d_dryrun` | `bigquery_dry_run_estimate.json` |
| Theme discovery (execute) | `configs/research/semiconductors_theme_discovery_90d_execute.yaml` | `semiconductors_theme_discovery_90d_execute` | `theme_discovery.csv` |
| Counts (dry-run) | `configs/research/semiconductors_bigquery_1y_partition_dryrun.yaml` | `semiconductors_bigquery_1y_dryrun` | `bigquery_dry_run_estimate.json` |
| Counts (execute) | `configs/research/semiconductors_bigquery_1y_partition_execute.yaml` | `semiconductors_bigquery_1y_execute` | `bigquery_daily_counts.jsonl` |
| Alpaca bars | `configs/research/semiconductors_alpaca_bars.yaml` | `semiconductors_alpaca_bars` | `warehouse/normalized/market/market_bars.jsonl` |
| Research build | `configs/research/semiconductors_news_market_study_v1.yaml` | `semiconductors_study_v1` | `semiconductor_attention_study.md` |

## Command sequence

Billable BigQuery and live Alpaca calls require credentials and the typed
execution gate. Do **not** run steps 2 and 5–6 without them.

```bash
# 1. Theme-discovery DRY RUN (safe; estimates cost, fetches nothing).
kinetic run \
  configs/research/semiconductors_theme_discovery_90d_dryrun.yaml \
  --run-id semiconductors_theme_discovery_90d_dryrun

# 2. Theme-discovery EXECUTION (billable; only after the dry-run estimate is safe).
kinetic run \
  configs/research/semiconductors_theme_discovery_90d_execute.yaml \
  --run-id semiconductors_theme_discovery_90d_execute

# 3. HUMAN REVIEW of discovered themes.
#    Open the execute run's artifacts/theme_discovery.csv and select only the
#    theme codes that unambiguously represent semiconductor coverage. Populate the
#    `semiconductors` bundle in configs/gdelt_theme_bundles.yaml: set `themes`, and
#    fill in bundle_version, review_date, source_discovery_run_id,
#    selected_theme_codes, excluded_ambiguous_theme_codes, and rationale.
#    Until this is done, steps 4-5 fail clearly (the bundle is a placeholder).

# 4. One-year count DRY RUN (safe).
kinetic run \
  configs/research/semiconductors_bigquery_1y_partition_dryrun.yaml \
  --run-id semiconductors_bigquery_1y_dryrun

# 5. One-year count EXECUTION (billable; only after the dry-run estimate is safe).
kinetic run \
  configs/research/semiconductors_bigquery_1y_partition_execute.yaml \
  --run-id semiconductors_bigquery_1y_execute

# 6. Alpaca bar ingestion (live; needs ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY).
kinetic run \
  configs/research/semiconductors_alpaca_bars.yaml \
  --run-id semiconductors_alpaca_bars

# 7. Offline joined research build + study report (no credentials, no network).
kinetic run \
  configs/research/semiconductors_news_market_study_v1.yaml \
  --run-id semiconductors_study_v1

# 8. STUDY-REPORT REVIEW.
#    Read warehouse/runs/semiconductors_news_market_study_v1/semiconductors_study_v1/
#    artifacts/semiconductor_attention_study.md and the machine-readable
#    event_control_contrasts.{json,csv}.
```

### Fully offline dry run (no credentials, no spend)

To exercise the entire join + contrast + report path against committed year-long
fixtures:

```bash
kinetic run \
  configs/research/semiconductors_news_market_study_offline.yaml \
  --run-id semiconductors_study_offline
```

Regenerate the offline fixtures with:

```bash
python3 scripts/generate_semiconductor_study_fixtures.py
```

## Interpretation vocabulary

Contrasts are summarized with a fixed vocabulary only: *evidence supports an
association*, *result is inconclusive*, *no detectable difference*, or
*insufficient sample*. The report never uses "profitable", "predictive alpha",
"causal", or "successful strategy".

## Limitations

- Media coverage is a news-attention proxy, not observed investor attention.
- BigQuery counts use a daily approximation; intra-day timing is not modeled.
- Theme selection is researcher-defined and shapes the measured coverage.
- Daily bars miss intraday reactions and price discovery.
- Repeated/overlapping events create dependence across nearby sessions.
- Benchmark adjustment is a simple benchmark difference, not factor-model alpha.
- No transaction costs, slippage, or execution are modeled; nothing is traded.

## Optional future overlay: recent narrow GDELT DOC sample (H2, descriptive)

This is **not** part of the primary study and must not block it. The DOC API and
BigQuery daily counts are **different measurement methods** and must be kept
separate — they are never merged into one rolling attention baseline.

A future, narrow, recent GDELT DOC ArtList sample (a small window, not a year)
could be ingested via the existing `gdelt_doc_artlist` path to examine coverage
*structure* rather than volume:

- unique-domain breadth per session;
- observed-copy concentration (effective domain count / HHI);
- duplicate ratio (syndication);
- H2 descriptive comparisons of broad vs heavily syndicated coverage.

Because DOC responses can be truncated by the record limit and only cover a
recent window, any DOC-based measure would be reported as a separate,
method-tagged, descriptive overlay (`news_measurement_method = gdelt_doc_artlist`)
alongside — never blended into — the BigQuery daily-count attention series.

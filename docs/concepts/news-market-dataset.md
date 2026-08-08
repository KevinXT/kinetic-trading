# News × Market Research Dataset — Design Memo

Status: design accepted for a **Core V1** implementation.
Scope owner: research-data layer (`kinetic.research`).

This memo is written before code. It defines *what* the dataset measures, *what*
the repository can actually support today, and *what is explicitly out of scope*.
It deliberately separates confirmatory from exploratory questions so that a large
feature library is not mistaken for a discovery.

This is a **research-data engineering** milestone. It builds a normalized,
reproducible, leakage-aware dataset that *aligns* GDELT news measurements with
Alpaca market bars. It does **not** implement trading, execution, portfolio
optimization, ML prediction, signal deployment, or any profitability claim.

---

## 1. Research question

> Around an unusual increase in news coverage for a topic, how do the *next*
> market sessions of instruments mapped to that topic behave, relative to a
> benchmark, in terms of volatility, range, volume, and return?

The dataset is built so this question (and a few close relatives) can be studied
without look-ahead leakage, and so effect sizes and confidence intervals — not
just p-values — can be reported.

We do **not** claim GDELT article counts *are* investor attention. They are a
**news-attention proxy / media-coverage measure / news-volume measure /
information-flow proxy**. We never observe whether any investor read a story.

## 2. Unit of observation (row grain)

The terminal research row (`NewsMarketObservation`) grain is:

```
one topic
+ one instrument (symbol under a fixed provider/feed/adjustment/currency/timeframe)
+ one aligned market session (session_date)
+ one feature cutoff (`target_session_open - cutoff_buffer_seconds`)
+ one alignment-policy version
+ one topic→instrument mapping version
+ one dataset version
```

Because one article may carry multiple topics, and one topic may map to multiple
instruments, rows fan out deliberately. Topic-level article counts are therefore
**not additive across topics** (documented, and enforced by keeping topic on the
row identity).

Intermediate normalized layers have their own, narrower grains (see §4).

## 3. Source datasets (what exists *today*)

Inspected before design. Two conceptually different news paths exist:

- **DOC / article-list path** (`kinetic.ingestion.news.gdelt`): article-level records with
  `title, url, domain, language, source_country, published_at (from seendate),
  ingested_at, query`; after `tag_articles`: `topics[], topic_matches{},
  primary_topic`; after `dedupe_articles`: `duplicate_groups` with
  `duplicate_count, total_seen, domains`. Deduplication is **title-based** and
  **scoped to a collection run**.
- **BigQuery / GKG counts path** (`kinetic.ingestion.news.gdelt.bigquery`): daily rows
  `{date, topic, article_count, source_count=None, avg_sentiment=None,
  coverage_share=None, query_terms[]}`. Breadth, syndication, per-domain
  frequency, and timestamps are **not** available on this path.

Market path (`kinetic.data.schemas`): `PriceBar(symbol, timestamp, timeframe, open, high,
low, close, volume, vwap?, trade_count?, provider, feed, adjustment, currency,
retrieved_at)` with OHLC invariants and currency-aware identity.

### Evidence table (candidate → reality)

| Candidate field | Actual source | Present today? | Reliable enough? | First version? | Limitation |
| --- | --- | ---: | ---: | ---: | --- |
| title-deduplicated article count | unique normalized titles in one DOC session/topic window | Yes (DOC); No (BQ) | Medium | Core V1 (DOC) | run/session scoped; not semantic or persistent identity |
| method-specific attention count | DOC title-deduplicated count or BQ provider daily count | Yes | Medium | Core V1 | method is part of identity; methods never share a baseline |
| observed copy count / duplicates | `duplicate_groups.total_seen/duplicate_count` | Yes (DOC only) | Medium | Core V1 (DOC) | run-scoped, title-based |
| unique domains | article `domain` | Yes (DOC) | Yes | Core V1 (DOC) | unavailable on BQ → null |
| per-domain frequency → HHI / entropy | article `domain` counts | Yes (DOC, rebuilt from articles) | Medium | V1 optional (DOC) | needs per-domain counts, not unique list |
| unique source countries | article `source_country` | Yes (DOC) | Low–Medium | Core V1 (DOC) | metadata proxy; not investor/event location |
| publication span / burstiness | article `published_at` | Yes (DOC) | Medium | span only in V1 | daily granularity; intraday timing partial |
| primary/multi-topic shares | `primary_topic`, `topics[]` | Yes (DOC) | Yes | Core V1 (DOC) | classification is keyword-rule dependent |
| query count / query hash | article `query` / config | Yes | Yes | Core V1 | shapes measured volume |
| lag-only attention z-score / percentile | derived from prior feature dates | Yes (DOC + BQ) | Yes | Core V1 | needs sufficient history |
| OHLC returns / gaps | `PriceBar` OHLC | Yes | Yes | Core V1 | daily; no intraday discovery |
| Parkinson / Garman–Klass / range vol | `PriceBar` OHLC | Yes | Yes | Parkinson Core V1; GK optional | daily estimators, not realized HF vol |
| trailing close-to-close vol | close series | Yes | Yes | Core V1 | rolling, lag-only |
| volume z-score / dollar volume | `volume`, `close` | Yes | Yes | Core V1 | proxy for dollar volume |
| Amihud illiquidity proxy | `abs(return)/dollar_volume` | Yes | Medium | Core V1 | not bid-ask/impact |
| volume per trade | `volume/trade_count` | Yes (when `trade_count`) | Medium | Core V1 optional | reported, not verified order size |
| VWAP deviation | `close`, `vwap` | Yes (when `vwap`) | Medium | Core V1 optional | provider-computed VWAP |
| benchmark / benchmark-adjusted return | benchmark `PriceBar` | Yes (if benchmark ingested) | Yes | Core V1 | simple difference, not factor alpha |
| rolling market-model beta / residual | instrument + benchmark returns | Yes (with history) | Medium | V1 optional | needs prior estimation window |
| tone / sentiment | none (BQ `avg_sentiment` is null; no DOC tone) | **No** | — | **Unsupported** | no reliable tone field |
| article body / semantic novelty | none | **No** | — | **Unsupported** | no body text/embeddings |
| bid-ask spread / order book | none | **No** | — | **Unsupported** | not in provider data |
| shares outstanding / market cap | none | **No** | — | **Unsupported** | no fundamentals |
| earnings dates / fundamentals | none | **No** | — | **Unsupported** | no SEC/fundamental feed yet |
| factor returns / risk-free rate | none | **No** | — | **Unsupported** | no FF factors / FRED yet |
| BQ breadth/syndication/timestamps | none on BQ path | **No** | — | **Unsupported (BQ)** | null + capability flag, never 0 |

## 4. Data architecture (layers preserved, not flattened)

```
GDELT provider responses          Alpaca provider responses
        ↓                                   ↓
normalized articles                normalized PriceBar records
        ↓                                   ↓
run-scoped title-deduplicated articles  market-session features (MarketSessionFeature)
        ↓                                   |
NewsTopicDailyFeature                       |
        ↓                                   |
SessionNewsFeature  --------------→ NewsMarketObservation ←-------------
                                    (derived product; never replaces sources)
```

Reference data (versioned): `TopicInstrumentMapping`, benchmark mappings, market
calendar, feature catalog, `DatasetManifest`. The joined dataset is a **derived
product**; it does not replace either normalized source dataset.

## 5. News measurement methods

`news_measurement_method ∈ {gdelt_doc_artlist, bigquery_gdelt_counts}` is a
first-class field. Feature capabilities differ by method and are recorded per
row via `feature_capabilities` and per-feature availability flags. A measured
zero and an unavailable measurement are never conflated (§9).

## 6. Information cutoff & alignment policy

Timestamps kept distinct: `published_at` (article), `ingested_at` (pipeline
observation of a modern historical collection), `feature_window_start/end`,
`feature_available_at`, `market_session_open/close`.

**Historical-availability caveat:** the first version uses the reported
publication timestamp as a proxy for historical information availability. It
does not model GDELT indexing delay, publisher timestamp revisions, API delivery
latency, or the time required for a live ingestion pipeline to observe the
article. `ingested_at` reflects when this modern collection ran and is never
treated as original historical availability.

Two alignment policies are designed; Core V1 implements **Policy B**
(session-information window) and can degrade to **Policy A**:

- **Policy A — conservative calendar-day**: news for date `D` → next valid
  session after `D`. Simple but maps Fri/Sat/Sun → the same Monday; when several
  feature dates share a session, that sharing is recorded (not silently
  duplicated).
- **Policy B — session-information window (DOC only)**: from the previous
  session's close to `feature_cutoff`, where
  `feature_cutoff = target_session_open - cutoff_buffer_seconds`. Core V1 uses a
  configurable 300-second default. Equality at the cutoff is allowed. The buffer
  is operational conservatism only; it does not model GDELT or publisher latency.
  This naturally folds weekend/holiday information into one decision interval.

Article-level DOC records support either policy. BigQuery daily counts do not
contain publication timestamps and therefore cannot claim an exact session
window. Exact-policy + BigQuery fails by default. An explicitly enabled downgrade
uses Policy A, records `alignment_precision=daily_approximation`,
`alignment_downgraded=true`, and emits a manifest warning. DOC exact rows record
`alignment_precision=article_timestamp`.

Timezone: `America/New_York` for session semantics; **UTC** for persisted
timestamps. `USEquitySessionCalendarV1` is a curated 2018–2035 ruleset for common
NYSE/Nasdaq cash-session holidays and common early closes, with DST via
`zoneinfo`. It is not an authoritative exchange calendar and does not model
unscheduled closures or historical schedule exceptions.

## 7. Target horizons

- Target session `S`: return from `close_{S-1}` to `close_S`, log return,
  benchmark-adjusted return, absolute return, high–low range, Parkinson variance.
- Target through plus four (`S..S+4`): cumulative return uses
  `close_{S+4}/close_{S-1}-1`; cumulative benchmark-adjusted return sums
  instrument-minus-benchmark returns over exactly those five sessions.
- Every target carries a completeness flag; horizons are never silently
  shortened. Targets require all future sessions to exist.

## 8. Hypotheses

Artifacts use controlled fields `hypothesis_class`, `hypothesis_family`, and
`hypothesis_id` under registry version `news-market-hypotheses-v2`.

### Confirmatory (predeclared, small)
- **H1 (primary): attention → volatility.** A large lag-only news-volume shock
  is associated with higher subsequent target-session absolute return / range-based
  volatility. Directional return is secondary.
- **H2: independent breadth vs repetition.** Coverage spread across more
  independent domains may associate with a different response than the same
  observed volume dominated by syndication. Sign not assumed. Core V1 emits
  descriptive subgroups but no formal H2 contrast or FDR test.
  Distinct domains are only a proxy for independent coverage; ownership,
  wire-service reuse, and editorial dependence are not observed.

### Exploratory (secondary; not discoveries)
- H3 stale/repeated coverage vs subsequent reversal/continuation.
- H4 attention effects conditioned on elevated prior volatility.
- H5 multi-topic ("spillover") articles and broader cross-instrument reaction.

### Unsupported (need data not present)
- Anything using sentiment/tone, article bodies, semantic novelty, bid-ask/order
  book, fundamentals, earnings, factor/risk-free returns. These are backlog.

## 9. Missingness & quality policy

Distinct statuses (never all mapped to `0`): `measured_zero`, `not_collected`,
`provider_no_records`, `provider_truncated`, `feature_unsupported`,
`insufficient_history`, `market_bar_missing`, `target_incomplete`,
`mapping_inactive`. Encoded via `news_coverage_status`, `market_coverage_status`,
`feature_supported`, `history_sufficient`, `target_complete`, `exclusion_reason`.
Missing market bars are never forward-filled or invented.

## 10. Statistical risks & discipline

Overlapping forward horizons, repeated events per topic/instrument, common market
shocks, cross-sectional dependence, serial correlation, small samples, and
multiple testing all bias naive inference. V1 reports descriptive effect sizes.
Formal H1 inference requires the configured minimum number of valid
session-date clusters and uses a deterministic moving-block bootstrap over
session-date means (`bootstrap_unit=session_date`, default block length 5).
Undersized groups have null confidence intervals and p-values. BH FDR is applied
only inside the predeclared H1 confirmatory endpoint family. H2 subgroup output
remains descriptive until a between-group contrast is predeclared; H3–H5 and
all subgroup analyses are exploratory. This does not fully solve multiway
cross-sectional dependence or serial correlation. No causal language.
Chronological development/validation/
holdout splits with an embargo are assigned so future modeling does not start
with leakage; the holdout is not inspected while choosing features/thresholds.

## 11. Core V1 scope (implemented here)

Models, feature catalog (+ artifact), US market calendar, session-window
alignment, article-derived + count-derived news features, market-session
features, versioned mappings, joined observations with leakage validation,
dataset manifest, deterministic offline event study, a `build_news_market_dataset`
pipeline task, committed offline fixtures, and tests. Advanced items (GK/RS/YZ
beyond Parkinson+one estimator, intraday burstiness, market-model beta, HAC SEs,
placebo/pre-trend batteries) are backlog unless trivially and safely supported.

### Pre-release schema revision

The milestone was still uncommitted when the final methodology review renamed
misleading fields. Catalog, builder, feature, and dataset versions were bumped to
V2. No transitional alias is emitted: there is no released V1 research schema or
deserializer to preserve, and retaining the misleading names would create a more
durable compatibility problem.

## 12. Emitted artifacts

The `build_news_market_dataset` task writes the following per-run artifacts
(under `warehouse/runs/<run>/`, not committed):

| Artifact | Contents |
| --- | --- |
| `news_topic_daily_features.jsonl` | `NewsTopicDailyFeature` rows (per topic per feature date) |
| `session_news_features.jsonl` | `SessionNewsFeature` rows (news aligned to a target session) |
| `market_session_features.jsonl` | `MarketSessionFeature` rows (per instrument per session) |
| `news_market_observations.{jsonl,csv}` | Terminal `NewsMarketObservation` rows with grouped inputs/contemporaneous/targets/quality/lineage |
| `feature_catalog.json` | Serialized feature catalog (category, formula, source, leakage class, hypothesis metadata) |
| `dataset_manifest.json` | `DatasetManifest`: versions, alignment policies, calendar name, cutoff buffer, availability assumptions, alignment-precision counts, bootstrap parameters, hypothesis-registry version, missingness counts, warnings |
| `join_summary.json` | Join/leakage diagnostics and split assignment counts |
| `event_study_events.jsonl` | Per-event attention records |
| `event_study_summary.{json,csv}` | Grouped effect sizes, CIs, and inferential status per hypothesis family |
| `research_limitations.md` | Generated limitations report shipped alongside every run |

Outputs are deterministic under a fixed clock, which the integration tests use
to assert byte-stable results.

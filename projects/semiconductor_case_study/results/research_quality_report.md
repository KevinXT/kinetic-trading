<!--
Curated snapshot from live run:
  dry-run id: semiconductors_seeded_theme_scoring_30d_dryrun_final_v6b
  live-run id: semiconductors_seeded_theme_scoring_30d_execute_final_v6b
  window: 2026-06-17 → 2026-07-16
This file is a sanitized copy of the generated research_quality_report.md.
Raw experiment directories under warehouse/runs/ remain gitignored.
-->

# Research quality report — semiconductors seeded theme scoring

Window `20260617`–`20260716` (5 weekly buckets). Human-review candidate screening only — **no candidate is approved and no theme bundle is modified by this task.** All claims below are bounded to this window, this organization-seeded corpus, and the queried GKG fields; they do not generalize to the full GDELT taxonomy or other periods.

## Candidate family, completeness & multiple testing

- Total support-qualified candidates (independent count): `1359`
- Candidate rows returned before presentation limit: `1359`
- Candidate family safety cap: `5000`
- Family cap reached: `False`
- Family complete: `True`
- **Statistical completion status: `complete`**
- BH skipped due to incomplete family: `False`
- Valid hypothesis-test count (defined p-values): `1359`
- Invalid / unavailable test count: `0`
- Multiple-testing method: `benjamini_hochberg_fdr`; BH family size (= hypotheses corrected): `1359`
- BH applied over the complete family before any presentation limit: `True`
- Presentation limit (review CSV / tables below): `500` (the full family is in `theme_candidate_scores.{csv,jsonl}`).

## Corpus & denominators

- Total seeded records (N1): `40334`
- Total whole-corpus records (descriptive denominator): `7647888`
- Total non-seed records (inferential denominator N0): `7607554`
- Candidate themes scored (>= min support): `1359`
- Total record-seed matches: `55561`
- Multi-seed records: `9939` (share of seed records: `0.246417415`)
- **Corpus scope:** organization-seeded only (seeds matched against `V2Organizations`). Industry *phrases* are matched in the same field, so phrase-only records that never name a company in `V2Organizations` are under-represented. This is a **primarily organization-seeded corpus**, not a full semiconductor-topic corpus.

## Statistical methodology

- **Descriptive** metrics (`*_vs_all_corpus`) compare the seeded corpus against *all* same-window records (seeds INCLUDED — nested groups); use them only for whole-corpus prevalence context.
- **Inferential** metrics (`*_vs_nonseed_background`, CI, p-value, adjusted p-value) use a DISJOINT 2x2: seeded records vs non-seed records (`c = all_corpus_theme_count - seed_record_count`, `N0 = total_corpus - total_seed`). Haldane–Anscombe 0.5 smoothing keeps odds ratios finite; honest nulls where a cell is undefined.
- **Sparse-aware test selection:** for each candidate the disjoint 2x2 table's expected cell counts are computed; a pooled two-proportion z-test is used only when every expected cell is >= `5.0`, otherwise a two-sided **Fisher exact test** (exact hypergeometric sum; no external dependency). Invalid tables emit a null p-value and are excluded from BH — never coerced to 1.
- The smoothed log-lift confidence interval is a SEPARATE **approximate screening interval** (log-ratio delta method, Haldane-Anscombe 0.5, asymptotic — not exact coverage); it is intentionally unchanged by the Fisher p-value selection.
- These are **screening approximations**: GKG records are not independent (syndication/republication, source and event clustering, theme co-occurrence), so ordinary CIs/p-values and BH-FDR understate dependence. Statistical significance does **not** establish semiconductor identity.

## Hypothesis-test selection

- `fisher_exact_two_sided`: `29`
- `two_proportion_z_test`: `1330`
- Candidates with adjusted p-value < 0.05 (if complete): `1201`

## Concentration coverage (exact vs approximate)

- Exact source concentration: `1359` / `1359`
- Exact day concentration: `1359` / `1359`
- Exact seed concentration: `1359` / `1359`
- Screened using approximate source fallback: `0`
- Selection rule: exact metrics are computed for every candidate (source/day via extra aggregation grains, seed via per-seed COUNTIF, all in the one base scan); screening prefers exact and only falls back to the APPROX_TOP_COUNT sketch when an exact value is unavailable.

## Lift distribution (non-seed inferential lift)

- lift <1: `646`
- lift 1-2: `267`
- lift 2-3: `132`
- lift >=3: `314`

## Classification distribution (all categories)

- ambiguous: `0`
- generic_noise: `495`
- industry_core: `0`
- manufacturing: `11`
- market_context: `90`
- policy_geopolitics: `60`
- requires_record_review: `663`
- supply_chain: `22`
- technology_infrastructure: `18`

Classification is produced by a **deterministic rule set**, not an ontology. `industry_core = 0` means none of the `1359` support-qualified candidates was classified as `industry_core` **by the current rules** — it does not establish that no semiconductor-specific GDELT theme exists outside this candidate family, date window, support threshold, queried fields, or rule set.

## Screening (advisory thresholds, not gates)

Thresholds: support >= 25, non-seed lift >= 3.0, periods_present >= 3, EXACT top-day share < 0.6, EXACT top-source share < 0.5, more than one matched seed, top-seed share < 0.9, not generic.

- Candidates passing all screening criteria: `185`
- Non-generic candidates passing screening: `185`
- Candidates whose concentration decision changed after exact validation: `0`

## Top 20 by raw statistical ranking (non-seed log-lift)

| rank | theme | lift | log-lift | support | periods | adj p | class |
|---|---|---|---|---|---|---|---|
| 1 | TAX_WORLDLANGUAGES_GOLDI | 930.495357 | 6.809649 | 74 | 1/5 | 0.0 | generic_noise |
| 2 | ECON_WORLDCURRENCIES_NEW_TAIWAN_DOLLARS | 522.97497 | 6.245212 | 61 | 3/5 | 0.0 | market_context |
| 3 | TAX_WORLDLANGUAGES_KUOYU | 403.243561 | 5.990466 | 62 | 3/5 | 0.0 | generic_noise |
| 4 | TAX_WORLDLANGUAGES_SADANA | 301.782278 | 5.705564 | 72 | 4/5 | 0.0 | generic_noise |
| 5 | TAX_WORLDLANGUAGES_KAYAN | 218.084849 | 5.383824 | 74 | 1/5 | 0.0 | generic_noise |
| 6 | ECON_DEVELOPMENTORGS_UNITED_NATIONS_CONFERENCE_ON_TRADE | 97.799812 | 4.586331 | 70 | 3/5 | 0.0 | supply_chain |
| 7 | ECON_WORLDCURRENCIES_SOUTH_KOREAN_WON | 96.231594 | 4.571607 | 50 | 5/5 | 0.0 | market_context |
| 8 | WB_1227_AUTOMOTIVE_VALUE_CHAIN | 88.887022 | 4.490563 | 82 | 3/5 | 0.0 | requires_record_review |
| 9 | ECON_WORLDCURRENCIES_JAPANESE_YEN | 83.27294 | 4.422351 | 1166 | 5/5 | 0.0 | market_context |
| 10 | TAX_FNCACT_AGRICULTURIST | 69.48934 | 4.250096 | 35 | 1/5 | 0.0 | generic_noise |
| 11 | TAX_WORLDLANGUAGES_RATHOD | 65.836936 | 4.191547 | 74 | 1/5 | 0.0 | generic_noise |
| 12 | WB_922_SERVICES_DELIVERY | 62.104585 | 4.141078 | 27 | 1/5 | 0.0 | requires_record_review |
| 13 | ECON_EARNINGSREPORT | 62.66806 | 4.1382 | 925 | 5/5 | 0.0 | market_context |
| 14 | TAX_FNCACT_CODER | 60.891109 | 4.117267 | 41 | 3/5 | 0.0 | generic_noise |
| 15 | TAX_WORLDLANGUAGES_GUMI | 57.665403 | 4.061828 | 48 | 5/5 | 0.0 | generic_noise |
| 16 | WB_2730_OLDER_WORKERS | 55.160676 | 4.012516 | 155 | 2/5 | 0.0 | requires_record_review |
| 17 | WB_1248_MARKET_DISCIPLINE | 53.166341 | 3.981898 | 42 | 3/5 | 0.0 | market_context |
| 18 | TAX_FNCACT_PORTFOLIO_MANAGER | 41.966015 | 3.737927 | 360 | 5/5 | 0.0 | generic_noise |
| 19 | EPU_POLICY_BANK_OF_KOREA | 37.339812 | 3.621515 | 273 | 5/5 | 0.0 | policy_geopolitics |
| 20 | WB_2384_APPLICATION_PROGRAMMING_INTERFACES | 36.786404 | 3.612394 | 55 | 5/5 | 0.0 | requires_record_review |

## Top 20 by research-adjusted ranking (composite)

| rank | theme | composite | lift | support | class | priority | screen |
|---|---|---|---|---|---|---|---|
| 1 | WB_1281_MANUFACTURING | 0.919987 | 10.207816 | 7899 | manufacturing | high | PASS |
| 2 | ECON_OILPRICE | 0.918449 | 8.489696 | 5046 | market_context | high | PASS |
| 3 | WB_442_INFLATION | 0.905862 | 7.223399 | 5425 | requires_record_review | high | PASS |
| 4 | ECON_INFLATION | 0.905655 | 7.143634 | 5587 | market_context | high | PASS |
| 5 | WB_671_STORAGE_MANAGEMENT | 0.9046 | 14.915079 | 3908 | requires_record_review | high | PASS |
| 6 | EPU_CATS_MONETARY_POLICY | 0.904359 | 7.877249 | 5030 | policy_geopolitics | high | PASS |
| 7 | WB_2944_SERVERS | 0.904109 | 15.620033 | 3860 | requires_record_review | high | PASS |
| 8 | WB_1150_VOLATILITY | 0.903748 | 9.878551 | 3913 | requires_record_review | high | PASS |
| 9 | WB_1174_WAREHOUSING_AND_STORAGE | 0.903398 | 4.779995 | 4893 | requires_record_review | high | PASS |
| 10 | WB_793_TRANSPORT_AND_LOGISTICS_SERVICES | 0.902833 | 4.717927 | 4894 | supply_chain | high | PASS |
| 11 | ECON_INTEREST_RATES | 0.898336 | 9.507862 | 3949 | market_context | high | PASS |
| 12 | WB_818_INDUSTRY_POLICY_AND_REAL_SECTORS | 0.89753 | 5.034742 | 8009 | manufacturing | high | PASS |
| 13 | WB_713_PUBLIC_FINANCE | 0.897002 | 8.74705 | 4157 | requires_record_review | high | PASS |
| 14 | WB_346_COMPETITIVE_INDUSTRIES | 0.896088 | 4.79328 | 8311 | manufacturing | high | PASS |
| 15 | WB_672_NETWORK_MANAGEMENT | 0.89573 | 6.965745 | 4654 | requires_record_review | high | PASS |
| 16 | ECON_WORLDCURRENCIES_DOLLAR | 0.895536 | 9.643775 | 3580 | market_context | high | PASS |
| 17 | WB_667_ICT_INFRASTRUCTURE | 0.8952 | 6.161 | 6604 | technology_infrastructure | high | PASS |
| 18 | EPU_POLICY_FEDERAL_RESERVE | 0.893356 | 25.807364 | 3118 | policy_geopolitics | high | PASS |
| 19 | EPU_POLICY_INTEREST_RATES | 0.892424 | 12.048861 | 3180 | policy_geopolitics | high | PASS |
| 20 | WB_1104_MACROECONOMIC_VULNERABILITY_AND_DEBT | 0.890359 | 5.470041 | 9446 | requires_record_review | high | PASS |

## Seed-composition diagnostics (two explicit denominators)

`share_of_unique_seed_records` uses total seed records as denominator and **overlaps** (a record can match several entities — it does NOT sum to 1). `share_of_record_seed_matches` uses total record-seed matches and sums to ~1.

| canonical seed | kind | unique records | share of unique seed records | share of record-seed matches |
|---|---|---|---|---|
| nvidia | company_alias | 17944 | 0.444885209 | 0.32296035 |
| samsung electronics | company_alias | 7926 | 0.196509149 | 0.14265402 |
| intel | company_alias | 7778 | 0.192839788 | 0.139990281 |
| semiconductor | industry_phrase | 6101 | 0.151261963 | 0.109807239 |
| micron | company_alias | 4475 | 0.110948579 | 0.080542107 |
| qualcomm | company_alias | 3740 | 0.09272574 | 0.067313403 |
| broadcom | company_alias | 3476 | 0.086180394 | 0.062561869 |
| tsmc | company_alias | 1635 | 0.04053652 | 0.029427116 |
| amd | company_alias | 1558 | 0.038627461 | 0.028041252 |
| semiconductors | industry_phrase | 852 | 0.021123618 | 0.015334497 |
| asml | company_alias | 50 | 0.001239649 | 0.000899912 |
| chipmaker | industry_phrase | 18 | 0.000446274 | 0.000323968 |
| microprocessor | industry_phrase | 8 | 0.000198344 | 0.000143986 |

## Representative record-level evidence (deterministic hash sample)

Records sampled by FARM_FINGERPRINT of GKGRECORDID (deterministic). Each row carries the GKG `DocumentIdentifier` (source URL) so it can be independently inspected; see `candidate_representative_evidence.{csv,jsonl}` for the full set. **The sampled record metadata and document identifiers indicate multi-entity and contextual co-occurrence. Because article bodies were not retrieved, semantic interpretation remains bounded to the available GKG metadata.** A human reviewer fills the interpretation columns.

Two structural patterns are visible in the sampled metadata. First, individual GKG records carry multiple themes, demonstrating substantial theme co-occurrence (GDELT themes are multi-label annotations). Second, similar document identifiers or paths across publishers are consistent with probable syndication or mirroring. Because article bodies and canonical content hashes were not collected, underlying-article duplication cannot be measured exactly.

| theme | record id | date | source | doc identifier avail. | matched entities | limitations |
|---|---|---|---|---|---|---|
| WB_1281_MANUFACTURING | 20260701011500-650 | 20260701 | econotimes.com | url | semiconductor;tsmc;nvidia;intel;broadcom | metadata-only |
| ECON_OILPRICE | 20260626160000-1234 | 20260626 | greeleytribune.com | url | micron;samsung electronics | metadata-only |
| WB_442_INFLATION | 20260630151500-1365 | 20260630 | yahoo.com | url | nvidia | metadata-only |
| ECON_INFLATION | 20260630151500-1365 | 20260630 | yahoo.com | url | nvidia | metadata-only |
| WB_671_STORAGE_MANAGEMENT | 20260701011500-650 | 20260701 | econotimes.com | url | semiconductor;tsmc;nvidia;intel;broadcom | metadata-only |
| EPU_CATS_MONETARY_POLICY | 20260710000000-T146 | 20260710 | now.com | url | intel | metadata-only |
| WB_2944_SERVERS | 20260701011500-650 | 20260701 | econotimes.com | url | semiconductor;tsmc;nvidia;intel;broadcom | metadata-only |
| WB_1150_VOLATILITY | 20260714204500-1600 | 20260714 | yahoo.com | url | samsung electronics | metadata-only |
| WB_1174_WAREHOUSING_AND_STORAGE | 20260703160000-581 | 20260703 | investegate.co.uk | url | semiconductor;semiconductors;nvidia;amd;broadcom;micron | metadata-only |
| WB_793_TRANSPORT_AND_LOGISTICS_SERVICES | 20260703160000-581 | 20260703 | investegate.co.uk | url | semiconductor;semiconductors;nvidia;amd;broadcom;micron | metadata-only |
| ECON_INTEREST_RATES | 20260710000000-T146 | 20260710 | now.com | url | intel | metadata-only |
| WB_818_INDUSTRY_POLICY_AND_REAL_SECTORS | 20260701011500-650 | 20260701 | econotimes.com | url | semiconductor;tsmc;nvidia;intel;broadcom | metadata-only |

## Confounding & limitations

- The whole-corpus background is *all* GKG records in the window: business-news concentration, English-language and Western-publisher skew, company popularity, and syndication all confound the descriptive lift.
- **Theme co-occurrence, not record/article duplication.** A single GKGRECORDID carrying several theme codes demonstrates that GDELT themes are multi-label annotations (often broad or overlapping); it is *not* evidence of duplicate GKG records or duplicate underlying articles.
- **Probable syndication or mirroring (not proven).** Similar document identifiers or URL paths appearing under multiple publishers/domains are *consistent with* probable syndication or mirroring. Because article bodies and canonical content hashes were not collected, underlying-article duplication cannot be measured exactly; the counts are GKG records, not distinct articles, and article-level uniqueness is not claimed.
- Classification is a lexical aid, not ground truth; no predictive-return validation is performed here, and no causal or predictive claim is made.
- Absence of a semiconductor-specific theme among these support-qualified candidates does **not** prove none exists below the support threshold or in other windows/taxonomies.


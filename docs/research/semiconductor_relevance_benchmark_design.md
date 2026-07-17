# Semiconductor Relevance Benchmark — Design Memo

Status: design for foundational implementation.
Scope owners: `news_data` (article text, entities, dedupe) and `research_data`
(benchmark sampling, labels, splits, baselines, metrics).

> This phase constructs benchmark infrastructure. It does not implement or
> validate a language model, sentiment model, trading signal, or market edge.

It also does not continue tuning GDELT theme classifications. The prior
semiconductor GKG theme-scoring experiment rejected themes as a primary identity
layer ([theme scoring snapshot](semiconductor-theme-scoring/README.md)).

---

## 1. Research question

> Can transparent deterministic entity-matching rules distinguish genuinely
> semiconductor-relevant articles from unrelated or incidental company mentions
> on a deduplicated, chronologically held-out, human-labeled article set?

This phase builds the data contract, offline corpus interface, entity reference
fixture, exact/near-duplicate analysis, human annotation workflow,
leakage-resistant splits, deterministic baselines, and offline metrics needed to
answer that question scientifically. It does **not** answer it with a live
production corpus.

---

## 2. Unit of observation

Primary research grain for labeling and evaluation:

```
one human-labeled article (article_id)
+ its exact-duplicate cluster (duplicate_cluster_id)
+ guideline_version
+ annotation_version
```

Sampling and split assignment operate on **exact-duplicate clusters**, not raw
provider rows. One underlying story must not contribute multiple independent
votes through syndicated copies.

Entity matches and baseline predictions are article-level diagnostics attached
to `article_id`. They are not labels.

---

## 3. Data sources and limitations

| Source | Role in this phase | Limitation |
| --- | --- | --- |
| Local JSONL article corpus (fixtures / user-supplied) | Offline article-text import | Not a live licensed feed; bodies may be absent |
| Committed entity-reference fixture | Alias and ticker/CIK stubs for seed companies | Not a complete industry universe |
| SEC `company_tickers_exchange.json` / EDGAR | Preferred future US ticker/exchange/CIK source | Not downloaded live in offline tests; provenance retained |
| GDELT DOC / GKG | Prior experiment context only | A GKG record ≠ unique article; org mention ≠ centrality; theme co-occurrence ≠ semantic relevance |
| Human annotators | Ordinal relevance labels | Coverage and agreement are limited until a production corpus is labeled |

No paid BigQuery, Alpaca, scraping, or network article retrieval is performed by
the offline benchmark path.

---

## 4. Article identity versus provider-record identity

| Concept | Meaning |
| --- | --- |
| `article_id` | Kinetic stable research identity for one imported article-text record |
| `provider` + `provider_record_id` | Upstream record identity (e.g. a DOC/GKG key when present) |
| `normalized_url` | Deterministic URL identity after conservative normalization |
| `title_sha256` / `body_sha256` | Exact content fingerprints of normalized comparison text |
| `duplicate_cluster_id` | Exact-duplicate cluster assigned by strong deterministic evidence |

Different publisher domains are **not** assumed to be independent journalism.
Exact clustering and near-duplicate review exist specifically because syndication
is common.

---

## 5. Entity identity

`EntityReferenceV1` records a research entity with:

- durable `entity_id`
- legal/display names
- aliases and ambiguous aliases
- optional ticker / exchange / CIK
- validity windows
- `reference_source` + `reference_version`

The committed semiconductor fixture covers companies already used in the theme
experiment (NVIDIA, Intel, AMD, TSMC, Micron, Qualcomm, Broadcom, ASML, Samsung
Electronics). It is a **fixture**, not a complete industry universe.

Alias matching reuses the token-safe rules developed for theme seeding
(`intel` ≠ `intelligence`, `amd` ≠ substrings inside larger words,
`micron` ≠ `omicron`). A match records entity id, alias, field, span, rule
version, and ambiguity status. A match alone does **not** imply company
centrality.

---

## 6. Exact duplicates versus possible near duplicates

### Level A — exact duplicate grouping (automatic)

Strong deterministic evidence may cluster records:

- same stable provider record identity
- same normalized URL
- same exact normalized body hash
- same exact normalized title **plus** another corroborating identity field

Every grouping records the responsible rule. Domain equality alone never
clusters.

### Level B — possible near-duplicate candidates (human review)

Transparent Jaccard resemblance over word shingles (bodies) or title tokens
(when bodies are unavailable), blocked by compatible language and configurable
publication-time distance.

The similarity threshold is a **candidate-generation engineering choice**, not a
scientifically validated duplicate threshold (Broder 1997; Lee et al. 2022).
Near-duplicate pairs are never auto-merged in this phase.

Exact clusters and near-duplicate candidates remain distinguishable artifacts.

---

## 7. Human annotation unit

Annotators label one article (or its cluster representative) under versioned
guidelines. The blind annotation CSV must not contain baseline predictions,
theme-ranking scores, expected labels, or post-publication market returns
(anchoring / outcome leakage).

Raw annotator decisions are immutable. Adjudicated labels are stored separately
and never overwrite originals (Oortwijn et al. 2021).

---

## 8. Label definitions

Ordinal relevance scale (`SemiconductorRelevanceAnnotationV1`):

| Code | Name | Meaning |
| ---: | --- | --- |
| 0 | unrelated | False positive or no meaningful semiconductor relevance |
| 1 | incidental | Semiconductor company/term mentioned but not important to the main information |
| 2 | meaningful_secondary | Semiconductor activity is a meaningful part of the article, not necessarily primary |
| 3 | primary_topic | Semiconductor companies, technology, manufacturing, supply chain, regulation, or markets are central |

Binary evaluation target (derived, not stored alone):

```
binary_relevant = relevance_label >= 2
```

No sentiment, event direction, impact, or expected-return labels in this phase.
First establish whether relevance can be annotated consistently
(Loughran & McDonald 2011 caution applies to future sentiment work; Boudoukh et
al. 2013 distinguishes relevance from tone).

---

## 9. Sampling strategy

Sampling occurs **after** exact duplicate clustering. Stable hash-based sampling
makes a fixed input + config produce the same sample.

Configurable per-entity maxima / stratum targets prevent NVIDIA-heavy corpora
from silently dominating the benchmark. These limits are **benchmark-design
choices**, not laws of statistics.

Sampling categories (not assumed labels) include:

- strict company match in title
- strict company match only outside the title
- multiple semiconductor entities
- industry-phrase matches
- ambiguous-alias cases
- macro/general-market articles mentioning one semiconductor company
- records likely to be unrelated substring false positives
- metadata-only records
- possible syndicated copies

Records are never called negatives before human annotation. Sample size is
configurable; a number such as 500 does **not** guarantee statistical power.
Class counts are reported only after labels exist.

---

## 10. Split strategy

Development / validation / sealed holdout assignments:

- assigned only after exact duplicate clustering
- all members of a duplicate cluster stay in the same split
- chronological ordering (not random row-level splits)
- date boundaries and any embargo are explicit in config and artifacts
- no hidden embargo default
- near-duplicate pairs that cross splits are contamination **warnings**, not
  silent merges
- holdout metrics require `evaluate_holdout: "ENABLE"`; otherwise only
  development and validation metrics are reported
- do not rebalance the holdout after viewing its labels
- do not inspect holdout performance while changing match rules or thresholds

(Elangovan et al. 2021; Lee et al. 2022.)

---

## 11. Deterministic baselines

Transparent non-ML baselines only:

| ID | Purpose |
| --- | --- |
| A — naive substring audit | Demonstrate unrestricted substring failure modes; never a recommended production rule |
| B — token-safe entity matcher | Measure current safer company-matching rules |
| C — strict title entity matcher | Require a strict entity match in the title |

Optional: strict all-text matcher when body/description is available.

Predictions include baseline id/version, binary prediction, abstention, matched
entities/aliases/fields, and a rule explanation. Ambiguous aliases may abstain.
No fake probabilities. No “confidence” unless an explicitly defined
non-probabilistic score is documented.

---

## 12. Metrics

Evaluate against **adjudicated** labels. Primary binary target:
`relevance_label >= 2`.

Report raw counts before percentages. For each baseline and split:

- TP, FP, TN, FN
- precision, recall, F1, specificity, balanced accuracy
- coverage, abstention rate, accuracy on covered cases
- total evaluated records and duplicate clusters

Undefined metrics return `null` plus a reason (never silent zero).

For precision, recall, specificity, and accuracy, report 95% Wilson score
intervals when denominators are valid. Intervals do **not** prove generalization
beyond the selected time period, publishers, companies, guidelines, and fields.

F1: point estimate in this phase, or a documented duplicate-cluster bootstrap
with minimum cluster count and seed. No ordinary row bootstrap that splits
within-cluster dependence.

No calibration metrics (baselines lack validated probabilities). No
multiple-hypothesis testing merely because multiple metrics exist. No p-values
without a predeclared comparison and defensible sampling unit.

---

## 13. Confidence intervals

Wilson score intervals for binomial proportions on the appropriate denominators
(e.g. precision uses TP+FP). Cluster dependence is acknowledged: article-level
CIs assume independence that may be violated by residual near-duplicates; the
contamination report makes that residual visible.

---

## 14. Missingness

Typed content statuses — never invent zeros for unavailable text:

- `available`
- `metadata_only`
- `not_collected`
- `provider_unavailable`
- `retrieval_failed`
- `rights_restricted`
- `unsupported`

Missing labels remain missing. Missing bodies yield null `body_sha256`.

---

## 15. Leakage risks

| Risk | Mitigation |
| --- | --- |
| Exact duplicates across splits | Cluster-aware chronological assignment |
| Near-duplicate contamination | Candidate pairs reported; cross-split warnings |
| Annotator anchoring | Blind annotation batch without predictions/returns |
| Outcome leakage | No post-publication returns in annotation artifacts |
| Holdout peeking | Explicit `evaluate_holdout: "ENABLE"` gate |
| Theme-score leakage into labels | Theme scores excluded from annotation CSV |

---

## 16. Timestamp semantics

| Field | Meaning |
| --- | --- |
| `published_at` | Timestamp reported for publication |
| `ingested_at` | When Kinetic observed/imported the record |
| `retrieved_at` | When optional article content was obtained |

None of these automatically proves the exact historical time a live trading
system could have consumed the information (MacKinlay 1997; prior news×market
design). Never substitute `ingested_at` for `published_at`. Persist UTC while
preserving source timezone information when available.

This phase does **not** run a new market event study; schemas preserve timestamps
for later work.

---

## 17. Human-review boundary

Automatic:

- validation, normalization, exact clustering, near-duplicate **candidate**
  generation, sampling, split assignment, baseline predictions, metric tables

Human required:

- ordinal relevance labels
- adjudication of disagreements
- near-duplicate threshold calibration / merges
- any future production entity or topic promotion

---

## 18. Artifacts

A successful offline run writes (among others):

- `article_text_records.jsonl`, `article_import_rejections.jsonl`
- `entity_matches.jsonl`
- `exact_duplicate_clusters.jsonl`, `near_duplicate_candidates.csv`,
  `dedupe_summary.json`
- `benchmark_candidates.jsonl`, `benchmark_sampling_manifest.json`
- `annotation_batch.csv`, `annotation_batch_manifest.json`
- `raw_annotations.jsonl`, `annotation_disagreements.csv`,
  `adjudicated_annotations.jsonl`, `annotation_agreement.json`,
  `annotation_quality_report.md` (when labels supplied)
- `benchmark_split_assignments.csv`, `split_manifest.json`,
  `split_contamination_report.json`
- `baseline_predictions.csv`, `benchmark_metrics.json`, `benchmark_report.md`
- `research_limitations.md`, `config_resolved.yaml`, `run_metadata.json`

Versions for schema, rules, guidelines, normalizers, entity references, dedupe,
sampling, and splits are recorded on artifacts. Outputs are deterministic under
a fixed clock and fixed input ordering.

---

## 19. Explicitly unsupported claims

This phase does **not** claim:

- that a text classifier, LLM, embedding model, or FinBERT exists or works
- that sentiment transfers from general language to finance
- that media content causes returns (Tetlock 2007 measures association)
- that GDELT themes are semiconductor identity labels
- that fixture metrics are real-world model performance
- that the entity fixture is a complete company universe
- that near-duplicate thresholds are scientifically calibrated
- that any trading system consumes these outputs
- predictive edge, profitability, or causality

---

## 20. Package ownership

| Package | Owns |
| --- | --- |
| `news_data` | Article-text records, URL/text normalization, local corpus import, entity references, deterministic alias matching, exact/near duplicate analysis |
| `research_data` | Benchmark sampling, annotation schemas/agreement/adjudication, chronological splits, baselines, metrics, reports |
| `trading_platform` | Task registration (`build_semiconductor_relevance_benchmark`), config-driven composition, artifact wiring |

Research evaluation logic does not live inside provider adapters.

---

## 21. Research references

1. Loughran, T. and McDonald, B. (2011), “When Is a Liability Not a Liability?
   Textual Analysis, Dictionaries, and 10-Ks,” *The Journal of Finance*, 66,
   35–65. DOI: [10.1111/j.1540-6261.2010.01625.x](https://doi.org/10.1111/j.1540-6261.2010.01625.x)
   — General-language sentiment can misclassify financial terms; future
   sentiment work needs financial-context evaluation. **Sentiment is not
   implemented here.**

2. Tetlock, P. C. (2007), “Giving Content to Investor Sentiment: The Role of
   Media in the Stock Market,” *The Journal of Finance*, 62, 1139–1168. DOI:
   [10.1111/j.1540-6261.2007.01232.x](https://doi.org/10.1111/j.1540-6261.2007.01232.x)
   — Media content can be measured and related to markets; association is not
   causal proof.

3. Boudoukh, J., Feldman, R., Kogan, S., and Richardson, M. (2013), “Which News
   Moves Stock Prices? A Textual Analysis,” NBER Working Paper 18725. DOI:
   [10.3386/w18725](https://doi.org/10.3386/w18725)
   — Relevance identification is distinct from tone. Working paper; not
   unquestionable ground truth.

4. MacKinlay, A. C. (1997), “Event Studies in Economics and Finance,” *Journal
   of Economic Literature*, 35, 13–39. — Event definitions and timing must be
   explicit; publication, ingestion, and market windows stay separate.

5. Broder, A. Z. (1997), “On the Resemblance and Containment of Documents.” DOI:
   [10.1109/SEQUEN.1997.666900](https://doi.org/10.1109/SEQUEN.1997.666900)
   — Token shingles and set similarity as a transparent resemblance foundation.

6. Lee, K. et al. (2022), “Deduplicating Training Data Makes Language Models
   Better,” ACL 2022. DOI:
   [10.18653/v1/2022.acl-long.577](https://doi.org/10.18653/v1/2022.acl-long.577)
   — Duplicates can distort distributions and contaminate evaluation splits.

7. Elangovan, A., He, J., and Verspoor, K. (2021), “Memorization vs.
   Generalization: Quantifying Data Leakage in NLP Performance Evaluation,”
   EACL 2021. DOI:
   [10.18653/v1/2021.eacl-main.113](https://doi.org/10.18653/v1/2021.eacl-main.113)
   — Dev/eval overlap can inflate apparent performance.

8. Oortwijn, Y., Ossenkoppele, T., and Betti, A. (2021), “Interrater
   Disagreement Resolution: A Systematic Procedure to Reach Consensus in
   Annotation Tasks,” HumEval 2021. DOI:
   [10.18653/v1/2021.humeval-1.15](https://doi.org/10.18653/v1/2021.humeval-1.15)
   — Explicit guidelines; preserve raw disagreement; adjudication must not
   overwrite original decisions.

9. U.S. Securities and Exchange Commission — official
   `company_tickers_exchange.json` / EDGAR company metadata as the preferred
   future source for US ticker, exchange, and CIK associations.

10. GDELT 2.0 Global Knowledge Graph documentation — field semantics for GKG;
    do not equate GKG records with unique articles or theme co-occurrence with
    semantic relevance.

# Real-Corpus Semiconductor Relevance Annotation Pilot Protocol

**Protocol version:** `real-corpus-pilot-protocol-v1`  
**Status:** Engineering pilot design — not a final benchmark freeze  
**Related foundation:** Semiconductor Relevance Benchmark Foundation (local working tree)

---

## 1. Research objectives

This pilot answers an operational and measurement question:

> Can Kinetic Trading construct a rights-cleared, deduplicated, reproducibly sampled,
> consistently annotated semiconductor-news corpus whose uncertainty, sampling bias,
> duplicate contamination, and annotation disagreements are explicitly measured?

Three distinct purposes are kept separate:

| Purpose | Question | Not the same as |
|---|---|---|
| **Operational feasibility** | Can rights-cleared articles be imported and annotated with usable schemas and workflows? | Population inference |
| **Measurement validation** | Can annotators distinguish ordinal relevance classes, and how uncertain are agreement / baseline estimates? | Classifier deployment |
| **Design calibration** | Which duplicate thresholds, strata, and guideline wording need revision before a final benchmark? | Trading edge |

This pilot does **not** demonstrate a usable classifier, market signal, predictive return,
causal relationship, or trading edge.

Methodological constraints (cited accurately; none proves profitability or causality):

1. Loughran & McDonald (2011), *Journal of Finance* — financial language needs domain-specific validation; general-language assumptions do not transfer automatically. Sentiment is out of scope here.
2. Tetlock (2007), *Journal of Finance* — media content can be studied quantitatively; association is not causation.
3. Boudoukh et al. (2013), NBER WP 18725 — company-news relevance and textual tone are different tasks; establish relevance before market-impact analysis.
4. MacKinlay (1997), *JEL* — publication time and event definition must be explicit; preserve timestamps for later event studies (not performed here).
5. Broder (1997) — token shingles / Jaccard resemblance are transparent similarity foundations; similarity is an estimate, not semantic proof.
6. Lee et al. (2022), ACL — duplicate material can distort empirical distributions and contaminate evaluation.
7. Elangovan et al. (2021), EACL — overlap across development and evaluation can inflate apparent generalization; exact-duplicate clusters must not cross future splits.
8. Cohen (1968) — ordinal labels warrant weighted kappa; weight choices must be explicit.
9. Oortwijn et al. (2021), HumEval — preserve raw disagreement; adjudicate without overwriting originals; revise guidelines systematically.
10. Flack et al. (1988); Rotondi & Donner (2012) — agreement sample size should relate to interval precision and rating distribution; no universal double-annotation count.
11. Newcombe (1998) — prefer Wilson score intervals for unweighted binomial proportions; avoid ordinary Wald intervals as default.
12. Buderer (1996) — by analogy, precision of recall/specificity depends on positive/negative denominators, not only total \(n\).
13. Davis & Goadrich (2006); Saito & Rehmsmeier (2015) — under imbalance, precision/recall/coverage/prevalence remain primary; accuracy/ROC can mislead.
14. SEC ticker/EDGAR metadata — preferred future US issuer identity sources, not guaranteed complete ground truth.
15. GDELT GKG/DOC docs — do not equate one GKG record with one unique article, domain with publisher independence, mention with centrality, or theme co-occurrence with relevance.

---

## 2. Estimands

Every reported number states its population or sample.

### 2.1 Eligible-corpus relevance prevalence

- \(N\) = number of eligible exact-duplicate clusters in the local corpus.
- \(Y_i = 1\) when the adjudicated relevance label for cluster \(i\) is \(\ge 2\); else \(0\).

\[
\pi = \frac{1}{N}\sum_{i=1}^{N} Y_i
\]

This is prevalence among the defined eligible cluster population only — not all financial news, all GDELT, all semiconductor news, future periods, other languages, or other publishers.

Under stratified unequal-probability sampling with inclusion probability \(\pi_i\) and weight \(w_i = 1/\pi_i\), a Hájek-style estimator may be reported:

\[
\hat{\pi}_w = \frac{\sum_i w_i Y_i}{\sum_i w_i}
\]

Unweighted prevalence from a purposive challenge set must never be reported as corpus prevalence.

### 2.2 Ordinal relevance distribution

Estimate proportions in labels \(0\) (unrelated), \(1\) (incidental), \(2\) (meaningful secondary), \(3\) (primary topic). Preserve the full ordinal distribution.

### 2.3 Annotation agreement

Estimate exact raw agreement, adjacent agreement (\(|A-B|\le 1\)), large-disagreement rate (\(|A-B|\ge 2\)), binary agreement under `label >= 2`, linearly weighted Cohen’s kappa, annotator marginals, confusion matrix, and disagreement categories.

Kappa is never interpreted without sample size, prevalence, rater marginals, CI, raw agreement, and disagreement-distance distribution.

### 2.4 Duplicate-candidate precision

Within the reviewed near-duplicate candidate set at threshold \(t\):

\[
\text{candidate precision}(t) =
\frac{\text{reviewed pairs at }t\text{ labeled same underlying story}}
{\text{all reviewed pairs at }t\text{ with determinate labels}}
\]

### 2.5 Duplicate-candidate recall within a blocked universe

Recall is estimated only within the configured language, publication-time, and comparison blocks, using below-threshold audits. It is not recall over all possible article pairs unless that universe is estimable.

### 2.6 Deterministic baseline performance

Against adjudicated binary labels: precision, recall, specificity, F1, balanced accuracy, coverage, abstention rate, accuracy on covered records — reported separately for representative, challenge, and (clearly labeled) combined samples.

---

## 3. Sampling frame and units

| Unit | Definition |
|---|---|
| **Target population** | Eligible exact-duplicate clusters in the rights-cleared local corpus under `PilotCorpusEligibilityV1` |
| **Sampling frame** | Exact-duplicate clusters that pass eligibility after schema validation and rights checks |
| **Unit of observation** | Canonical article representing an exact-duplicate cluster (for relevance) |
| **Unit of sampling (relevance)** | Exact-duplicate cluster |
| **Unit of annotation** | Exact-duplicate cluster (one annotation unit; multi-role membership allowed) |
| **Unit of independence (agreement bootstrap)** | Exact-duplicate cluster (paired labels stay together) |
| **Unit of sampling (duplicate review)** | Article pair within the blocked comparison universe |

---

## 4. Eligibility, rights, and provenance

Eligibility requires an explicit acceptable rights status in `ArticleContentProvenanceV1`.
“Publicly reachable webpage” is **not** permission to commit or redistribute article text.

Exclusion reason codes are retained for every ineligible record. Records are never silently dropped.

Flow (counts reconciled at each step):

```text
local input
  → schema-valid
  → rights-eligible
  → date/language/text eligible
  → exact clusters
  → canonical sample frame
  → representative sample
  → challenge sample
```

---

## 5. Samples A / B / C

### Sample A — Representative probability sample

- Purpose: estimate eligible-corpus prevalence and real-world deterministic baseline behavior.
- Exact-duplicate clusters; known inclusion probabilities; design weights \(w_{hi}=N_h/n_h\).
- No post-selection purposive hard cases; no rebalancing on labels.

### Sample B — Challenge sample

- Purpose: stress-test guidelines and match rules.
- Purposive; reason codes required; no population weights; reported separately.

### Sample C — Duplicate-pair calibration sample

- Purpose: exact-cluster audits, candidate precision, below-threshold false-negative audits.
- Distinct from article relevance annotation.
- Threshold recommendation status defaults to `HUMAN_REVIEW_REQUIRED`.

---

## 6. Calibration vs formal pilot

1. Freeze guideline version `pilot-guidelines-v1-calibration`.
2. Double-annotate calibration set (operational default: 40 clusters).
3. Agreement + structured disagreement review; revise guidelines if needed; version bump.
4. Do not silently reinterpret earlier labels under a new version.
5. Formal pilot requires typed gate: `calibration_approval: "ENABLE_FORMAL_PILOT"`.
6. Calibration set is excluded from future sealed benchmark evaluation unless explicitly relabeled under the formal guideline version.

---

## 7. Statistical planning assumptions

- Confidence level and half-widths are configurable (defaults: 95%, prevalence half-width 0.07).
- Wilson score intervals for unweighted binomial proportions; numerical \(n\) solver.
- Positive/negative denominator scenarios via Buderer-style planning.
- Finite-population correction reported with and without application when \(N\) is known.
- Challenge and double-annotation counts may be operational conveniences — labeled as such.
- Underfilled populations emit typed underpowered / underfilled status; targets are not quietly reduced.

---

## 8. Confidence intervals and agreement measures

- Wilson 95% for unweighted simple binomial proportions (raw agreement, large-disagreement rate, unweighted precision/recall/specificity/covered accuracy).
- Design-weighted estimates: no Wilson on fractional pseudo-counts; survey bootstrap or stratified estimator; if unavailable, mark uncertainty unavailable.
- Linearly weighted kappa with agreement weights \(w_{ij}=1-|i-j|/(K-1)\); cluster bootstrap (default 2,000 replicates, configurable).
- F1: always report point estimate; optional design-aware bootstrap; never ordinary row bootstrap that ignores cluster/survey design.

---

## 9. Duplicate-threshold evaluation

Evaluate a threshold grid (title and body separate). Sample pair-score strata above and below the current candidate threshold. Blocking universe must be stated. Default:

`duplicate_threshold_recommendation_status: HUMAN_REVIEW_REQUIRED`

Do not auto-select a threshold by F1 alone.

---

## 10. Missing-data handling

Report by sample role/stratum: missing body/description/publication time, cannot-determine, rights restricted, incomplete annotation, unavailable evidence spans, unresolved entities. Compare label distributions for body-available vs metadata-only without causal claims. Cannot-determine is never encoded as label 0 and is not silently dropped from denominators without reporting.

---

## 11. Leakage controls

- Exact-duplicate clusters never cross future benchmark splits.
- Annotators do not see baselines, theme scores, model predictions, expected labels, other annotator labels, adjudications, returns, sample role (when possible), or sampling weights.
- Challenge reason codes that reveal expected error type are hidden from annotation sheets.
- Sampling does not use annotation labels, market returns, or baseline correctness.

---

## 12. Stopping rules and human approval

Typed pilot states replace silent continuation. Missing annotations are incomplete states, not success.

Final benchmark construction requires:

```yaml
pilot_human_approval: "APPROVE_FINAL_BENCHMARK_CONSTRUCTION"
```

Without it, the highest readiness status is `REVIEW_REQUIRED`.

Do not freeze a permanent model holdout until calibration, eligibility, duplicate policy review, entity-reference versions, formal pilot labels, disagreement causes, and this human gate are complete.

---

## 13. Unsupported claims

This protocol does **not** support claims of:

- Classifier readiness for production
- Sentiment or tone effects
- Predictive returns or trading edges
- Causal media→price relationships
- Universal agreement or sample-size thresholds
- Representativeness of all global semiconductor news
- Completeness of SEC ticker associations as corporate universe truth
- Equivalence of GDELT records with unique independent articles

---

## 14. Purpose separation (do not collapse)

| Activity | Valid inference |
|---|---|
| Population inference | Only Sample A (design-weighted when required) over the eligible local cluster population |
| Engineering stress testing | Sample B; not prevalence |
| Annotation calibration | Calibration round; may exclude from formal estimates |
| Software validation | Synthetic fixtures; never presented as real pilot findings |

There is no single undefined “pilot score.”

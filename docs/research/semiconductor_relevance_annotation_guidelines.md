# Semiconductor Relevance Annotation Guidelines

Guideline version: `semiconductor-relevance-guidelines-v1`

These guidelines define how human annotators assign ordinal semiconductor
**relevance** labels. They do not ask for sentiment, impact direction, or
expected returns.

Annotators must not see baseline predictions, theme scores, model outputs, or
post-publication market returns while labeling.

---

## 1. Task

For each article (title, and body/description when available), decide how
central semiconductor companies, technology, manufacturing, supply chain,
regulation, or markets are to the article’s main information.

---

## 2. Ordinal scale

| Label | Name | Use when |
| ---: | --- | --- |
| **0** | unrelated | The match is a false positive, or there is no meaningful semiconductor relevance. |
| **1** | incidental | A semiconductor company or term appears, but it is not important to the article’s main information. |
| **2** | meaningful_secondary | Semiconductor activity is a meaningful part of the article, but not necessarily the primary subject. |
| **3** | primary_topic | Semiconductor companies, technology, manufacturing, supply chain, regulation, or markets are central. |

Binary evaluation (computed later; do not store only this):

```
binary_relevant = relevance_label >= 2
```

Preserve the ordinal label.

---

## 3. What to read

1. Title (always).
2. Description, if present.
3. Body, if `content_status = available`.
4. If the body is unavailable, judge from title/description only and set
   `uncertain = true` when the missing body would reasonably change the label.

Do not invent missing text.

---

## 4. Central entities

List `central_entity_ids` for entities that are **central or meaningfully
secondary** to the article under your label (typically labels 2–3). Leave empty
for labels 0–1 unless an entity is named but still incidental (then do not list
it as central).

Entity mentions alone do not imply centrality.

---

## 5. Evidence

When possible, copy a short `evidence_text` span that justified the label and
record character offsets into the concatenated annotation text
(`title\n\ndescription\n\nbody` with empty sections omitted) as
`evidence_start` / `evidence_end`. Offsets are optional when the article is
metadata-only.

---

## 6. Uncertainty

Set `uncertain = true` when:

- body is missing and the title is ambiguous
- the article mixes multiple industries and semiconductor role is unclear
- language is non-English and you cannot confidently judge
- the only match looks like a possible false-positive alias

Provide `uncertainty_reason` when `uncertain` is true.

Disagreement is expected on boundary cases. Raw labels are preserved; a separate
adjudicator resolves conflicts without erasing originals.

---

## 7. Difficult examples

### 7.1 NVIDIA as an index constituent → typically **1**

Title: “Nasdaq rises; heavyweight tech stocks lead gains.”  
Body: lists NVIDIA among many index members with no chip-specific news.

Mentioned, not important to the article’s information → **incidental**.

### 7.2 “Intel” inside “intelligence” → typically **0**

Title: “Central intelligence agency briefing on inflation.”  
No semiconductor company. Token-safe matchers should avoid this; if a naive
substring match flagged it, label **unrelated**.

### 7.3 Inflation article mentioning one chip company → typically **1**

Title: “Inflation cools more than expected.”  
Body: one sentence notes Intel shares moved with the market.

Macro story; semiconductor firm is incidental → **1**. If semiconductor supply
or chip pricing is a substantial section, consider **2**.

### 7.4 TSMC fabrication expansion → typically **3**

Title: “TSMC to expand Arizona fab capacity.”  
Central semiconductor manufacturing news → **primary_topic**.

### 7.5 AI servers with NVIDIA and Broadcom as suppliers → typically **3** or **2**

If the article’s core subject is AI server / accelerator supply and NVIDIA and
Broadcom are central suppliers → **3**.  
If the core subject is a cloud provider’s earnings and chips are one meaningful
segment among several → **2**.

### 7.6 Samsung phones; semiconductor ops incidental → typically **1**

Title: “Samsung unveils new Galaxy phone lineup.”  
Body focuses on consumer devices; foundry/chip units mentioned in passing →
**incidental**. If the article is primarily about Samsung’s semiconductor
division results → **3**.

### 7.7 Supply-chain article without company in title → typically **2** or **3**

Title: “Export controls tighten on advanced chipmaking tools.”  
Body discusses lithography equipment and foundry capacity without naming a
company in the title. Semiconductor manufacturing/regulation is still the topic
→ **3** (or **2** if framed as broad geopolitics with chips as one thread).

### 7.8 Syndicated copy of the same report

If two rows are the same underlying story on different domains, label the
assigned article consistently. Exact-duplicate clustering should keep them
together; near-duplicates may still need human review. Do not treat different
domains as independent journalism by default.

### 7.9 Relevant title, body unavailable → often **2** or **3** with uncertainty

Title: “ASML bookings surge on EUV demand.”  
`content_status = metadata_only`. A manufacturing/equipment headline can support
**3**, but set `uncertain = true` and reason `body_unavailable`.

---

## 8. What not to annotate

Do not assign:

- positive / negative sentiment
- bullish / bearish impact
- event direction
- expected return
- trading action

Relevance consistency comes first.

---

## 9. Annotator notes

Use `annotator_notes` for brief rationale or edge-case flags. Do not paste
market prices or baseline outputs.

---

## 10. Adjudication (adjudicators only)

When two annotators disagree:

1. Preserve both raw labels.
2. Record a separate adjudicated label, adjudicator id, and reason.
3. Require an explicit reason for large disagreements (distance ≥ 2 on the
   ordinal scale).
4. Never silently invent a majority vote when only two annotators exist.

---

## 11. Versioning

Any material change to examples or decision rules requires a new
`guideline_version`. Annotation batches record the guideline version used.

# Data lifecycle

Kinetic Trading is a **point-in-time** research platform. That single word drives
every rule on this page: a research result is only trustworthy if it can be
reconstructed from what was actually knowable at the time, and that is only
possible if the raw provider payload is preserved and never overwritten.

## The stages

```
raw provider response
  → normalized canonical record
    → curated / processed dataset
      → feature dataset
        → model prediction
          → research result or trading input
```

Each stage has a directory under `warehouse/`, which is entirely git-ignored.

| Stage | Path | What it holds | Written by |
| --- | --- | --- | --- |
| raw | `warehouse/raw/` | Provider responses as received, keyed by a request fingerprint. Includes the cache-aside store used by the Alpaca and GDELT clients and the BigQuery result cache | `kinetic.ingestion` |
| normalized | `warehouse/normalized/` | Canonical records — `PriceBar`, `ArticleTextRecordV1` — with provider identity retained as a field, not as a directory | `kinetic.ingestion`, `kinetic.data.storage` |
| curated | `warehouse/curated/` | Cleaned, deduplicated, session-aligned datasets | `kinetic.processing` |
| features | `warehouse/features/` | Feature datasets described by the feature catalog | `kinetic.processing` |
| predictions | `warehouse/predictions/` | Model output. Nothing writes here yet | `kinetic.ml` (future) |
| models | `warehouse/models/` | Trained model artifacts. Nothing writes here yet | `kinetic.ml` (future) |
| runs | `warehouse/runs/` | One directory per pipeline run: `config_resolved.yaml`, `run_metadata.json`, `artifacts/`, and `traceback.txt` on failure | `kinetic.core` |

`warehouse/cost/cost_ledger.jsonl` sits alongside these: an append-only record of
every cloud query decision (estimated bytes, estimated cost, allowed or blocked,
and why). It is operational metadata rather than a lifecycle stage.

Private, rights-restricted inputs stay **outside** `warehouse/`, under
`data/real_corpus/` and `data/local_only/`. They are inputs a human supplied, not
outputs the platform generated, and both paths are git-ignored with explicit
rules in `.gitignore` that refuse to track article bodies.

## Rules

**Raw data is never overwritten during normal ingestion.** A re-fetch with the
same request fingerprint reads the cache; a deliberate refresh writes a new
entry. Normalization reads raw and writes normalized — it does not mutate raw in
place. A bug found in a normalizer six months from now must be fixable by
re-running normalization over preserved raw payloads.

**Provider-specific raw data stays distinguishable from canonical data.** Raw
lives under `warehouse/raw/`, canonical under `warehouse/normalized/`, and every
canonical record carries the provider, feed and adjustment that produced it. You
can always answer "which provider said this, and what exactly did they send".

**Timestamps are preserved, never fabricated.** When a provider supplies them,
the platform keeps: event time, publication or release time, first-observed time,
ingestion time, and revision/vintage information. When a provider does *not*
supply one, the field is `None` with an explicit capability flag — a measured
zero is never invented for an unmeasurable field. The GDELT BigQuery counts path
is the worked example: it can measure `article_count` per date and nothing else,
so every richer news feature is emitted as `None` with its capability flag set to
`False` rather than as `0`.

**Leakage is a data-layer concern, not a modeling concern.** The
`processing/cross_asset/` join draws predictors strictly from information
knowable before the target session opens. Same-session descriptive fields are
labelled `contemporaneous` and are structurally forbidden as predictors of their
own session; forward outcomes are labelled `targets` with explicit completeness
flags. `processing/cross_asset/validation.py` checks these invariants
mechanically, and `research.build_news_market_dataset` fails loudly rather than
emitting a leaky dataset — the semiconductor event-vs-control study's
`require_symbols` pre-build check (`assert_required_bar_symbols`) follows the
same principle: a missing instrument or benchmark bar fails the build instead of
silently dropping targets downstream.

## Instrument identity — the extension point

`data/schemas/instruments.py` currently models what the equities-and-news code
actually needs: a provider-independent `instrument_id`, a symbol, a CIK, a
company name, aliases, and a `valid_from`/`valid_to` range.

The planned asset coverage — futures, options, forex, crypto — will require the
canonical instrument to also carry:

- asset class
- venue / exchange
- provider-independent ID and per-provider symbol mappings
- base and quote currency
- underlying instrument
- contract expiration
- option strike and call/put type
- contract multiplier
- tick size
- effective date range (already present)

None of this was implemented, because no code in the repository needs it today
and a speculative schema would be wrong in ways nobody could detect. What the
refactor established is the *place* it goes and the guarantee that adding it does
not ripple: `Instrument` is imported by the catalog resolver, the storage layer
and the research mappings, and none of them construct provider-specific identity
themselves. Adding fields is additive; the per-provider symbol mapping belongs in
`data/catalog/instruments.py`, next to the existing resolution logic.

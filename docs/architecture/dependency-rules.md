# Dependency rules

These rules exist so that a reader can predict what a module is allowed to do
from its location alone, and so that a mistake is caught by a test rather than by
a production incident.

They will be enforced mechanically by [Import Linter](https://import-linter.readthedocs.io/)
via the `[tool.importlinter]` section of `pyproject.toml`, added during
packaging consolidation. Run them with:

```
make lint-imports
```

`make validate` will run them too.

## The layering

```
interface        may call public services; owns no domain logic
    │
bootstrap        the only module allowed to import across every layer
    │
research         may use data, processing, ml
    │
ml               may use data, processing, core
    │
ingestion        may use data, core
    │
processing       may use data, core
    │
data              may use core only
    │
core             may use nothing else in kinetic
```

`kinetic.core` sits at the bottom and imports nothing from any other `kinetic`
subsystem. `kinetic.bootstrap` sits outside the layering by design: composition
requires knowing about the things being composed.

`processing` sits *below* `ingestion` in this layering, which is not the reading
order used elsewhere in the docs (ingestion is usually listed first because it is
"upstream" conceptually). The true dependency direction in the code is the other
way: a provider adapter reuses deterministic normalization when mapping a payload
into a canonical record (stable article ids, URL normalization, seed matching of
a query result), while no processing module imports a provider. The layering
reflects the code, not the conceptual reading order.

## Rule by rule

**`core` must not import provider, data, processing, ML, research, trading or
interface packages.** Core is platform mechanics: what a task is, how a plan is
parsed, how a run is executed and recorded. If a change to core requires
importing a schema or a provider, the change belongs somewhere else.

**`core` must not become a renamed `common`.** The `packages/common` package
contributes exactly four things to core: the exception hierarchy
(`core/errors.py`), config loading (`core/config.py`), run metadata writing
(`core/artifacts.py`) and git/timestamp provenance (`core/provenance.py`). Its
HTTP response cache goes to `ingestion/caching.py`, its cloud-spend guardrails to
`ingestion/cost/`, and its date-window resolution to `ingestion/windows.py` —
because all three exist to serve external provider calls.

**`data` must not import ingestion, processing, ML, research, trading or
interface packages.** Canonical schemas describe what a record *is*, independent
of who supplied it and what will be done with it.

**Provider request/response objects do not live in `data`.** Alpaca's raw JSON
shapes live in `ingestion/market/alpaca/raw_models.py`. The canonical `PriceBar`
lives in `data/schemas/market.py`. The function that turns one into the other
lives in `ingestion/market/alpaca/normalize.py`. This three-file pattern is the
template for every provider.

**`ingestion` may depend on `core` contracts and canonical `data` models.** It
may not depend on processing, ml, research or interface.

**`processing` may depend on `core` and `data`.** If the same input and
configuration always produce the same output, and no trained model is involved,
it belongs here — even if it produces a score. This includes the two-sample
statistical helpers (Welch's test, block-bootstrap CI) added for the
event-vs-control study: they are pure, dependency-free arithmetic, not a fitted
model.

**`ml` may depend on `core`, `data` and selected processing functions.** The test
for "is this ML?" is not "does it output a number" but "does its behavior depend
on data it was fitted to, or on a model artifact". The relevance package is in
`ml` because it is the measurement apparatus for a relevance model: sampling
design, annotation, adjudication, agreement statistics and evaluation metrics.
Its deterministic reference baselines live there too, next to the metrics that
score them.

**`research` may depend on `data`, `processing` and `ml`.** The dataset builder
(`research/datasets/builder.py`) is filed under `research`, not `processing`,
specifically because it runs the event study as its last step — if it lived
under `processing`, `processing` would depend on `research`.

**`interface` may call public services but must not own domain logic.** A CLI
command may parse arguments, call one function, and format the result. It may not
fetch from GDELT, compute a feature, or evaluate a model.

**`bootstrap` is the composition root.** It is the only module that imports
ingestion, processing, ml and research together, and the only place where the
mapping from task identifier to callable is written down.

## The trading boundary (not yet implemented)

There is no `kinetic.trading` package. When one is written, these rules apply:

- `trading` may depend on stable `data`, `processing` and `ml` interfaces.
- **`research` must never depend on `trading`.** A backtest, an event study or a
  notebook must not be able to reach an order-placing code path by importing a
  module. This is a safety boundary, not a style preference. The event-vs-control
  study is a good example of why this matters in practice: it deliberately
  restricts its report vocabulary to "association"/"difference" language and
  never touches anything that could place an order — that discipline should be
  enforced structurally, not just by convention, once `trading` exists.
- Anything that can place a real order must live under `trading/execution/`, must
  require explicit credentials, and must never be constructed as a side effect of
  importing anything.

The import-linter contract that forbids `research -> trading` will be written at
the same time the layering contract is, and will pass vacuously until the day the
package appears.

## Known limitations of the mechanical checks

Import Linter checks module-level dependency direction. It cannot express:

- **Semiconductor reference data inside the platform.** `data/catalog/seeds.py`
  will hold `DEFAULT_SEMICONDUCTOR_SEEDS`, and `data/catalog/entities.py` will
  hold `default_semiconductor_entities()`. Both are case-specific *reference
  data* that stay in the platform because `ml/relevance/baselines.py` and the
  offline benchmark fixtures consume them as defaults. The case study's configs,
  results and conclusions move to `projects/semiconductor_case_study/`; this
  reference data does not, because moving it would invert the dependency
  direction (`kinetic.ml` → `projects`). The clean fix is to make the baselines
  take their seed vocabulary as a required argument and to load it from
  case-study config. That is an API change, deliberately not bundled into a
  structural refactor.
- **Semiconductor vocabulary inside a rule set.** The bucket rules in
  `processing/news/themes/classification.py` are written around semiconductor and
  chip vocabulary, even though the surrounding machinery is topic-agnostic. The
  extraction to config is straightforward and is not done here.
- **Instrument symbols hard-coded in study configuration.** The event-vs-control
  study's instrument list (AMD, NVDA, SMH) and benchmark (QQQ) live in case-study
  config, correctly — but the *code* in `research/event_studies/event_study.py`
  that builds the contrast is fully generic over whatever symbols and responses a
  config supplies. No linter can verify that a future contributor keeps it that
  way; a code reviewer has to.
- **Runtime side effects.** No linter proves that importing `kinetic` does not
  register a task. `tests/unit/core/test_no_import_time_registration.py` (or the
  equivalent subprocess-based assertion in `tests/e2e/test_imports.py`) does.

# Repository rules

Permanent constraints for anyone — human or agent — working in this repository.
These are not style preferences. Each one exists because violating it silently
produces a system that looks fine and is wrong.

Read [docs/architecture/platform-overview.md](docs/architecture/platform-overview.md)
first if you have not.

## Structure

**Respect the package boundaries.** Every module under `src/kinetic/` belongs to
exactly one subsystem, and which one is decided by the question the code answers,
not by what is convenient to import. The table is in the platform overview; the
enforcement is `[tool.importlinter]` in `pyproject.toml`, run by
`make lint-imports`.

**Never place domain code in `core`.** `kinetic.core` is task contract, registry,
plan, context, runner, hooks, config loading, artifacts, provenance, errors. It
knows nothing about price bars, providers, features or research. If a change to
`core` requires importing a schema or a provider, the change belongs elsewhere.

**Never create a generic dumping ground.** No `kinetic.common`, `kinetic.shared`,
`kinetic.utils`, `kinetic.misc`, or `kinetic.helpers`. A `common` package existed
and was dissolved in 0.2 precisely because it accumulated five unrelated concerns
that each had a real owner. A small utility module *inside* the subsystem that
owns it is fine.

**Do not create empty packages to match a diagram.** `kinetic.trading`,
`kinetic.interface.terminal` and several `kinetic.ml` subpackages are named in
the target structure and deliberately do not exist. An empty package advertises a
capability that is not there and sends the next reader looking for it.

## Data

**Never place provider models in canonical schemas without mapping.** Alpaca's
raw JSON shape lives in `ingestion/market/alpaca/raw_models.py`. The canonical
`PriceBar` lives in `data/schemas/market.py`. A pure function in
`ingestion/market/alpaca/normalize.py` maps one to the other. Three files, always.
The moment a provider field appears in `kinetic.data` unmapped, canonical stops
meaning anything.

**Keep raw provider data distinguishable, and never overwrite it.** Raw payloads
go under `warehouse/raw/` and are preserved; normalization reads them and writes
`warehouse/normalized/`. A normalizer bug found six months from now must be
fixable by re-running normalization over what the provider actually sent.

**Never fabricate a timestamp or a measurement.** If a provider does not supply
event time, release time, first-observed time or vintage, the field is `None`
with an explicit capability flag. A missing measurement is never `0`, and an
observation date is never substituted for a release date. This platform is
point-in-time or it is worthless.

## Runtime

**Never use import-time registration.** Importing `kinetic`, or anything inside
it, must not mutate global state. `kinetic/bootstrap.py` is the single
composition root, and `build_default_registry()` is called explicitly. There is
no registration decorator and no module-level registry.
`tests/e2e/test_imports.py` asserts this in a subprocess — if you find yourself
wanting to relax that test, the design has drifted.

**Never put live execution inside research.** There is no `kinetic.trading` yet.
When there is, research and ML must not import it, and nothing that can place a
real order may be constructed as a side effect of importing anything. The
import-linter contract for this is already written and passes vacuously. This is
a safety boundary, not a layering preference.

**Keep the interface thin.** A CLI command parses arguments, calls one service,
formats the result. It does not fetch, compute, evaluate or execute. The future
terminal UI is a second front end onto the same services; every piece of logic
that leaks into a command is logic the terminal would have to reimplement.

## Honesty

**Do not alter case-study conclusions to make results look positive.** The
semiconductor seeded-theme study did not produce a validated relevance signal.
That is written down in `projects/semiconductor_case_study/`, and it stays
written down. The event-vs-control attention study's own report vocabulary is
deliberately restricted (see `kinetic.research.reports.study_report`) so it can
never claim causation or profitability regardless of what a run finds. Negative
results, stated limitations and known-failed approaches are the most valuable
thing in a research repository — they are what stops the next person repeating
the work.

**Documentation must distinguish what exists from what is planned.** The README
has separate "what works", "what is experimental" and "what has not been built"
sections, and they are accurate. Keep them accurate. Do not describe a planned
capability in the present tense.

**Report validation honestly.** If a test fails, say so and show the output. If a
step was skipped, say it was skipped.

## Persistence

**Push validated checkpoints immediately.** An architecture refactor of this size
must never exist only inside one machine or one container — see
`docs/architecture/refactor-progress.md` for what happened the first time this
rule was not followed strictly enough. Commit at a coherent, validated boundary,
then push before continuing.

## Before finishing

```bash
make validate
```

Lint, import contracts, format, types, dependencies, tests, and a wheel build
plus an isolated import and CLI smoke test from outside the source tree. Run it.
Do not report work complete on the basis of a command you did not run.

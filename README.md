# kinetic-trading

Architecture-first Python monorepo for a trading research platform.

This project is in early development. The core pipeline engine, config system, and one data provider are implemented and tested. Other packages are intentional architectural placeholders — reserved boundaries for future work, not missing features.

---

## What's implemented

| Package | Role | Status |
|---------|------|--------|
| `packages/common` | YAML config loading (recursive includes, deep merge), shared error hierarchy | **implemented** |
| `packages/pipeline_core` | Linear pipeline engine: plan parser, task registry, step runner, lifecycle hooks, `RunContext` with artifact writing, run metadata | **implemented** |
| `packages/news_data` | GDELT DOC API client (query builder, config-driven requests, response parsing) | **implemented** |
| `packages/market_data` | Market data provider integrations | placeholder |
| `packages/strategy_sdk` | Trading-domain building blocks (signals, risk, portfolio logic) | placeholder |
| `apps/trading_platform` | CLI entrypoint, task registration, orchestration | **implemented** |

---

## Repo layout

```
packages/
  common/              Shared config loading, errors, utilities
  pipeline_core/       Pipeline engine (runner, parser, hooks, context, task registry)
  news_data/           News data providers (GDELT)
  market_data/         Market data providers (placeholder)
  strategy_sdk/        Trading-domain abstractions (placeholder)
apps/
  trading_platform/    CLI entrypoint and task registration
configs/               YAML plans and presets
tests/                 Workspace-level tests
docs/                  Product notes, technical guides, development TODO
experiments/           Run outputs (gitignored at runtime)
```

Each package has its own `pyproject.toml` and `src/<import_name>/` layout.

### Dependency direction

```
common           ← lowest layer, no internal deps
pipeline_core    ← depends on common
news_data        ← depends on common
market_data      ← (no deps yet)
strategy_sdk     ← (no deps yet)
trading_platform ← depends on pipeline_core, news_data (app layer)
```

Packages never depend upward on `apps/`. Lower layers never depend on higher ones.

---

## Quick start

> **Important:** Tests require editable-installing the packages first.
> Running `pytest` without installing will fail with import errors.

```bash
git clone <repo-url> && cd Kinetic_Trading

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e ./packages/common \
  -e ./packages/pipeline_core \
  -e ./packages/news_data \
  -e ./packages/market_data \
  -e ./packages/strategy_sdk \
  -e ./apps/trading_platform \
  -e ".[dev]"

pytest
```

The root `pip install -e ".[dev]"` installs workspace dev tools (pytest, black, ruff, mypy). It does not bundle any library code.

### Minimal install (core only)

If you only need the engine and news integration:

```bash
pip install -e ./packages/common \
  -e ./packages/pipeline_core \
  -e ./packages/news_data \
  -e ".[dev]"
```

The full test suite imports all packages, so use the full install above for `pytest` to pass completely.

---

## Tests

```bash
pytest
```

26 tests covering: config loading and merging, pipeline plan parsing, task registry, runner success/failure metadata, `RunContext` artifact writing, and import smoke tests for every package.

---

## Documentation

See [`docs/README.md`](docs/README.md) for product vision, technical guides, and development TODO.

---

## Packaging

This repo works as a root-installed monorepo: all internal packages are editable-installed into one virtualenv. Each package declares its own dependencies in `pyproject.toml`, so the structure can evolve toward independent publishing when needed. It is not yet a fully independent multi-package workspace with separate release pipelines.

# Kinetic Trading — development TODO

This checklist tracks project milestones: what's shipped and what's next.

---

## Milestone: Pipeline Engine v1 (Linear Runner) — complete

### `packages/pipeline_core` — engine

- [x] `RunContext`: cfg, run_name, run_id, run_dir, artifacts_dir, state, JSON/JSONL writers
- [x] `parse_plan` → ordered `Step` list (ingest → transform(s) → strategy)
- [x] `runner.run_plan` / `run_plan_from_file` with lifecycle hooks and run metadata
- [x] `TaskFn` contract and `TASK_REGISTRY` with `@register` decorator
- [x] Lifecycle hooks: `TimingHook`, `ErrorHook`, `MetadataHook`, `LoggingHook`

### `packages/common` — shared config + errors

- [x] YAML loader with `include:` / deep merge (`config_builder.py`)
- [x] Shared exception hierarchy (`errors.py`)
- [x] JSON cache-aside layer (`cache.py`)

### `packages/news_data` — GDELT integration

- [x] GDELT DOC API client with config-driven requests and response parsing
- [x] Article normalization (stable internal field names)
- [x] `gdelt_docs` pipeline task with caching
- [x] `filter_articles` pipeline task (language, source_country, domain)
- [x] Retry with backoff for 429s and timeouts

### `apps/trading_platform` — CLI + task registration

- [x] CLI entrypoint (`trading-platform` / `python -m trading_platform`)
- [x] Task registration (populates `TASK_REGISTRY` at import time)

### Working demo plan

- [x] `configs/demo.yaml` — GDELT ingest → filter → artifacts

---

## Milestone: Market data + Alpaca bars — complete

- [x] Provider-neutral domain contracts (`PriceBar`, filings, facts, macro, instruments)
- [x] JSONL financial store with logical keys, atomic writes, and writer locks
- [x] Alpaca historical bars client, cache, normalizer, task, and offline tests
- [x] Currency-aware bar identity; semantic upsert equality excluding `retrieved_at`
- [x] Cache identity includes normalized API origin (`alpaca-bars-v2`)
- [x] Strict Alpaca numeric/boolean config parsing

---

## Milestone: CI quality gates — complete

- [x] GitHub Actions: pytest, ruff, black, mypy, package build, import smoke
- [x] Python version matrix (3.11, 3.12)
- [x] Live Alpaca test remains opt-in / secret-dependent

---

## Milestone: Production hardening — in progress

See [`production-hardening.md`](production-hardening.md).

- [x] PR 1: repository/release hygiene (`make validate`, dirty-tree release gate, `make source-archive`, build-pollution checks)
- [ ] PR 2: source and environment manifests on runs
- [ ] PR 3: canonical artifact store
- [ ] PR 4: typed task contracts
- [ ] PR 5: refactor one vertical workflow
- [ ] PR 6: transactional cost ledger
- [ ] PR 7: application service and view models
- [ ] Dependency lockfile (deferred until an authoritative install workflow is chosen)

---

## Next up

- [ ] Flesh out `packages/strategy_sdk` (signals, risk, portfolio logic)
- [ ] SEC EDGAR and FRED adapters on existing contracts
- [ ] Time-aligned joins between news features and market bars
- [ ] Research-grade backtesting prototype
- [ ] End-to-end test with mocked HTTP for the demo pipeline
- [ ] Refresh technical guides under `docs/` as APIs evolve

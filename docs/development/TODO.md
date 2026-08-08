# Kinetic Trading — development TODO

This checklist tracks project milestones: what's shipped and what's next.

---

## Milestone: Pipeline Engine v1 (Linear Runner) — complete

### `kinetic.core` — engine

- [x] `RunContext`: cfg, run_name, run_id, run_dir, artifacts_dir, state, JSON/JSONL writers
- [x] `parse_plan` → ordered `Step` list (ingest → transform(s) → strategy)
- [x] `runner.run_plan` / `run_plan_from_file` with lifecycle hooks and run metadata
- [x] `TaskFn` contract and `TASK_REGISTRY` with `@register` decorator
- [x] Lifecycle hooks: `TimingHook`, `ErrorHook`, `MetadataHook`, `LoggingHook`

### `kinetic.core` — shared config + errors

- [x] YAML loader with `include:` / deep merge (`config_builder.py`)
- [x] Shared exception hierarchy (`errors.py`)
- [x] JSON cache-aside layer (`cache.py`)

### `kinetic.ingestion.news` — GDELT integration

- [x] GDELT DOC API client with config-driven requests and response parsing
- [x] Article normalization (stable internal field names)
- [x] `gdelt_docs` pipeline task with caching
- [x] `filter_articles` pipeline task (language, source_country, domain)
- [x] Retry with backoff for 429s and timeouts

### `kinetic.interface.cli` — CLI + task registration

- [x] CLI entrypoint (`kinetic`)
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

## Milestone: Production hardening foundation — complete

Merged to `main` via GitHub [PR #4](https://github.com/KevinXT/kinetic-trading/pull/4)
(2026-08-04). See [`production-hardening.md`](production-hardening.md).

- [x] Repository/release hygiene (`make validate`, dirty-tree release gate, `make source-archive`, build-pollution checks)
- [x] Price-only provider surface; deleted empty `strategy_sdk` in 0.2; seeded theme scoring module split
- [ ] Dependency lockfile (deferred until an authoritative install workflow is chosen)
- [ ] Optional follow-ons (run manifests, artifact store, typed task contracts, etc.) — only when a real pilot/ops need requires them

---

## Next up

- [ ] Flesh out `kinetic.trading` (not yet created) only when real strategy-domain code exists (package is deferred / not installed)
- [ ] SEC EDGAR and FRED adapters on existing contracts
- [x] Time-aligned joins between news features and market bars (`kinetic.research` / `build_news_market_dataset`)
- [ ] Research-grade backtesting prototype
- [ ] End-to-end test with mocked HTTP for the demo pipeline
- [ ] Refresh technical guides under `docs/` as APIs evolve

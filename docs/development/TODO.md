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

## Next up

- [ ] Add `packages/market_data` providers (bars, feeds)
- [ ] Flesh out `packages/strategy_sdk` (signals, risk, portfolio logic)
- [ ] Add CI (GitHub Actions: pytest + ruff + mypy)
- [ ] End-to-end test with mocked HTTP for the demo pipeline
- [ ] Refresh technical guides under `docs/` as APIs evolve

# Production hardening

Make the existing Kinetic Trading architecture safer to operate without
replacing it. The foundation for that work is **merged to `main`** via GitHub
[PR #4](https://github.com/KevinXT/kinetic-trading/pull/4) (2026-08-04).

## Principles

- Preserve `pipeline_core`, `common`, provider packages, `research_data`, and
  `trading_platform` ownership boundaries.
- Prefer boring engineering: one validation path, one archive path, explicit
  gates at system boundaries.
- One source of truth internally; redundant verification at boundaries.
- Do not merge large rewrite PRs; prefer small, reviewable steps driven by
  real pilot needs.

## Delivered foundation (merged)

GitHub PR #4 landed repository/release hygiene plus YAGNI simplifications that
were already true of the tree:

| Deliverable | Notes |
| --- | --- |
| Canonical `make validate` / release hygiene | Dirty-tree gate, source archives, build-pollution checks |
| Price-only market-data providers | Company/macro *provider* protocols deferred; domain models retained |
| Deferred `strategy_sdk` | Empty package boundary on disk; not installed by CI, wheels-smoke, or default install |
| Seeded theme scoring module split | Package under `news_data.task.bigquery_gdelt_seeded_theme_scoring/`; public task name unchanged |

### Commands

```bash
make validate              # pollution + lint + format + types + tests + wheels-smoke
make clean-build           # remove package/app build artifacts
make source-archive        # requires clean tree (or ALLOW_DIRTY_TREE=1)
make release-check         # dirty-tree gate + validate + source-archive
```

Ordinary development (`make test`, `make lint`) allows a dirty work tree.
Release archives do not, unless explicitly overridden.

Dependency locking (`uv.lock` or equivalent) remains **deferred**. Use editable
`pip install -e` as documented in the README and CI until an authoritative
install workflow is chosen in a dedicated change.

## Compatibility workflow configs

Representative verticals for later golden/compatibility checks (paths only):

1. Offline deterministic: `configs/research/news_market_dataset_demo.yaml`
2. BigQuery dry-run: `configs/research/semiconductors_seeded_theme_scoring_30d_dryrun.yaml`
3. BigQuery execute: `configs/research/semiconductors_seeded_theme_scoring_30d_execute.yaml`

## Deferred follow-ons

These remain **optional** and should be opened only when a concrete pilot or
ops need requires them. They are not in progress:

- Source and environment manifests recorded on runs
- Canonical artifact store (atomic write, hash, manifest)
- Typed task request/result contracts
- Decompose one vertical workflow behind stable behavior
- Transactional cost ledger reservations
- Application service + view models for run presentation

Do not treat this list as an active multi-PR schedule.

## Baseline freeze

See [`production-hardening/baseline/`](production-hardening/baseline/) for the
Phase 0 freeze notes recorded when the hardening branch was cut.

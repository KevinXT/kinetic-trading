# Production hardening

This document tracks the production-hardening effort: make the existing Kinetic
Trading architecture safer to operate without replacing it.

## Principles

- Preserve `pipeline_core`, `common`, provider packages, `research_data`, and
  `trading_platform` ownership boundaries.
- Prefer boring engineering: one validation path, one archive path, explicit
  gates at system boundaries.
- One source of truth internally; redundant verification at boundaries.
- Do not merge the effort as a single rewrite PR.

## PR sequence

| PR | Focus | Status |
| --- | --- | --- |
| 1 | Repository / release hygiene | In progress (this branch) |
| 2 | Source and environment manifests on runs | Planned |
| 3 | Canonical artifact store (atomic write, hash, manifest) | Planned |
| 4 | Typed task request/result contracts + legacy adapters | Planned |
| 5 | Decompose one vertical workflow behind stable behavior | Planned |
| 6 | Transactional cost ledger reservations | Planned |
| 7 | Application service + view models for run presentation | Planned |

Dependency locking (`uv.lock` or equivalent) is **deferred**. Introduce a
lockfile only in a dedicated PR that also adopts an authoritative install
workflow. Until then, use editable `pip install -e` as documented in the
README and CI.

## PR 1 — Repository and release hygiene

PR 1 does **not** change task contracts, artifact writers, run metadata, error
taxonomy, research methodology, or dependency management.

It adds:

- Canonical `make` targets mirroring CI (`make validate`)
- Dirty-tree rejection for release-oriented targets (`make source-archive`,
  `make release-check`), overridable with `ALLOW_DIRTY_TREE=1`
- Source-only archives via `git archive` plus SHA-256 checksums under `dist/`
- Detection and cleanup of stale `packages/*/build` and `apps/*/build` trees

### Commands

```bash
make validate              # pollution + lint + format + types + tests + wheels-smoke
make clean-build           # remove package/app build artifacts
make source-archive        # requires clean tree (or ALLOW_DIRTY_TREE=1)
make release-check         # dirty-tree gate + validate + source-archive
```

Ordinary development (`make test`, `make lint`) allows a dirty work tree.
Release archives do not, unless explicitly overridden.

## Compatibility workflows (later PRs)

These configs are the representative verticals for golden/compatibility checks
starting with workflow migrations (PR 5+). PR 1 only records their paths:

1. Offline deterministic: `configs/research/news_market_dataset_demo.yaml`
2. BigQuery dry-run: `configs/research/semiconductors_seeded_theme_scoring_30d_dryrun.yaml`
3. BigQuery execute: `configs/research/semiconductors_seeded_theme_scoring_30d_execute.yaml`

## Baseline freeze

See [`production-hardening/baseline/`](production-hardening/baseline/) for the
Phase 0 freeze notes on the accepted commit used to cut this branch.

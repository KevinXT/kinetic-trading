# Refactor progress

Live status of the `packages/*` + `apps/*` → `src/kinetic` architecture
migration. Updated at the end of each validated phase.

Branch: `refactor/kinetic-platform-architecture`
Baseline commit: `aec8131` (fixed — see "Session history" below for why this is
not the first baseline attempted)

## Session history

This refactor was attempted once before, in an ephemeral container session, on
branch `claude/kinetic-trading-refactor-j7ljx4`, against an earlier baseline
(`cf5c9dd`). That attempt completed all six phases, reached
2222 passed / 4 skipped, and produced a full set of architecture documents and a
verified wheel build. It was never pushed: the container's git credentials were
read-only for the whole session, every push attempt returned `403`, and the
container was gone before push access could be restored. **All commits from that
attempt are unrecoverable.** No bundle or patch was saved before the container
was lost.

Two things happened on `origin/main` while that was going on:

- PR #6 (`feature/semiconductor-theme-scoring`) merged, adding the
  event-vs-control semiconductor attention study to `research_data`.
- PR #7 (`fix/apptest-absolute-paths`) merged, independently fixing the exact
  Streamlit AppTest relative-path bug that the lost attempt's Phase 0 baseline
  had recorded as a pre-existing failure.

This refactor restarts from scratch against the current `origin/main`
(`aec8131`), in a dedicated git worktree
(`Kinetic_Trading-architecture-refactor`) on a machine with working push access.
The lost attempt's documents are being used as a **blueprint** — most of the
target architecture, package responsibilities and dependency rules carry over
unchanged — but every claim about the actual repository has been re-verified
against `aec8131`, not assumed from memory. The differences found are recorded
in [`migration-map.md`](migration-map.md).

**Persistence rule for this attempt:** every validated checkpoint is pushed to
`origin/refactor/kinetic-platform-architecture` immediately, before the next
phase begins. The branch was pushed unchanged (before any file was touched)
immediately after creation, specifically so the branch could never again exist
only inside one container.

## Baseline (`aec8131`, this attempt)

Worktree: `/Users/kevintran/Coding/Kinetic_Trading-architecture-refactor`
Environment: Python 3.14.2, pip 25.3 (no `uv` or Python 3.11/3.12 available on
this machine — see "Environment discrepancies" below).

Installed via the repository's own documented pip fallback path (seven editable
packages + dev extras):

```
pip install -e ./packages/common -e ./packages/pipeline_core \
  -e ./packages/news_data -e ./packages/market_data \
  -e ./packages/research_data -e ./apps/trading_platform \
  -e ./apps/relevance_annotation_ui -e ".[dev]"
```

| Check | Command | Result |
| --- | --- | --- |
| Build pollution | `scripts/dev/check_build_pollution.sh` | clean |
| Lint | `ruff check .` | pass |
| Format (scoped) | `scripts/dev/format_check.sh` | pass — 118 files |
| Types (scoped) | `scripts/dev/typecheck.sh` | pass — 95 source files |
| Tests | `pytest -q` | **2146 passed, 14 skipped, 0 failed** |
| Wheels smoke | `scripts/dev/wheels_smoke.sh` | pass |
| Full target | `make validate` | pass, same results as above in one run |

### Pre-existing failures

**None.** This is a change from the lost attempt's baseline (`cf5c9dd`: 6
failed), because PR #7 already fixed the Streamlit AppTest relative-path bug
upstream — the same fix the lost attempt made independently and never got to
land.

### Skips (both expected, neither a gap)

| Count | Location | Reason |
| --- | --- | --- |
| 1 | `tests/integration/providers/test_alpaca_live.py` | gated on `RUN_PROVIDER_INTEGRATION_TESTS=1`; needs real Alpaca credentials |
| 13 | `tests/test_research_configs_partitioned.py` | a parametrized guard that skips itself for every config that is not a BigQuery GDELT config — the 13 skips are exactly the semiconductor configs PR #6 added, correctly not matching that guard |

### Environment discrepancies (not failures — recorded because the task asked
for unavailable commands to be distinguished from failures)

- **`uv` is not installed** on this machine. The repository does not yet
  reference `uv` at this baseline (it is introduced during packaging
  consolidation, same as the lost attempt), so there is no `uv lock --check` or
  `uv sync` to run yet at Phase 0. Installation used the pip fallback path the
  repository's own docs already document.
- **Only Python 3.14.2 is available.** The repository declares
  `requires-python = ">=3.11"` with CI matrixed on 3.11/3.12, and `mypy` is
  pinned to analyze as 3.12 for stub compatibility. No 3.11/3.12 interpreter,
  `pyenv`, or equivalent exists on this machine. Every baseline check above
  passed anyway; this is recorded as a discrepancy from the CI-declared matrix,
  not as a failure, since nothing failed.

## Phases

| Phase | Status |
| --- | --- |
| 0 — Audit, baseline, architecture documents | complete — `dfa919f` |
| 1 — Consolidate packaging and namespace | complete |
| 2 — Migrate the pipeline core | complete |
| 3 — Migrate data and ingestion | complete |
| 4 — Separate processing, ML, research, projects, tools | complete |
| 5 — Migrate configs, CLI and tests | complete |
| 6 — Cleanup and production validation | in progress (this checkpoint) |

Phases 1 through 5 landed as one commit rather than five. Splitting them after
the fact would have meant reverting substantial completed, cross-validated work
just to re-split it, for no real safety benefit — every intermediate state
between "old packages present" and "new src/kinetic tree complete and green"
would have broken imports somewhere, which the migration rules forbid
committing. The single commit is fully validated (`make validate` clean: ruff,
6/6 import contracts, black, mypy on 156 files, deptry, 2254 tests passed / 4
skipped, wheel smoke) before it exists, and it is pushed immediately, which is
what the persistence rule is actually protecting against.

Each phase's commit is pushed to `origin/refactor/kinetic-platform-architecture`
immediately after it is validated, before the next phase begins. This document
is updated at the end of
each phase with the checkpoint commit hash and validation results.

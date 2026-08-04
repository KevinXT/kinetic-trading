# Phase 0 baseline freeze

Historical record of the freeze used to cut the hardening branch. The foundation
later merged to `main` via GitHub PR #4 (2026-08-04).

| Field | Value |
| --- | --- |
| Branch (at freeze) | `production-hardening` |
| Accepted commit | `7a5ba4f035017f0332ccba6d0a80693462b84206` |
| Commit subject | `fix(ui): harden annotation workflow integrity` |
| Isolation | Separate git worktree; semiconductor WIP left untouched |

## Representative workflow configs

Recorded for later compatibility suites (not executed as live runs in the
foundation PR):

| Kind | Config |
| --- | --- |
| Offline deterministic | `configs/research/news_market_dataset_demo.yaml` |
| BigQuery dry-run | `configs/research/semiconductors_seeded_theme_scoring_30d_dryrun.yaml` |
| BigQuery execute | `configs/research/semiconductors_seeded_theme_scoring_30d_execute.yaml` |

## Validation commands at freeze

Canonical commands after the hygiene foundation landed:

```bash
make validate
make source-archive
```

These wrap the same checks previously inlined in `.github/workflows/ci.yml`
(ruff, scoped black, scoped mypy, pytest, wheel builds, isolated import smoke)
plus build-pollution detection.

### Recorded results (worktree after hygiene foundation implementation)

| Check | Result |
| --- | --- |
| `pytest -q` | 2118 passed, 11 skipped |
| `ruff check .` | clean |
| scoped `black --check` | clean |
| scoped `mypy` | clean (with mypy `python_version = 3.12`) |
| `make wheels-smoke` | ok |
| `make check-build-pollution` | clean after wheels-smoke |
| `make check-dirty-tree` | rejects dirty tree; `ALLOW_DIRTY_TREE=1` overrides |
| `ALLOW_DIRTY_TREE=1 make source-archive` | writes `dist/*.tar.gz` + `.sha256`; checksum verifies |

## Notes

- Dependency lockfiles are deferred; baseline uses editable installs.
- Run metadata inside pipeline runs and a canonical artifact store remain deferred
  follow-ons (see [`production-hardening.md`](../production-hardening.md)).
- Local Phase 0 host used Python 3.14 for the worktree venv; CI remains 3.11/3.12.
  The foundation set mypy `python_version` to 3.12 so current NumPy stubs parse under that host.

# Development

## Setup

Requires Python 3.11 or 3.12 (CI runs both).

```bash
git clone <repo> && cd kinetic-trading
uv sync --all-extras --dev
uv pip install -e .
kinetic --version
```

Without `uv`:

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev,bigquery,annotation]"
```

There is one distribution. If you are following an old instruction that installs
seven editable packages from `packages/` and `apps/`, it predates 0.2 — see
[migration-map.md](../architecture/migration-map.md).

## Check it works

```bash
kinetic run configs/research/news_market_dataset_demo.yaml --run-id demo
```

No credentials, no network. Output lands in
`warehouse/runs/news_market_dataset_demo/demo/`.

## Validation

```bash
make validate
```

Runs, in order:

| Step | Command | What it protects |
| --- | --- | --- |
| build pollution | `scripts/dev/check_build_pollution.sh` | a stale `build/` tree shadowing the editable install |
| lint | `ruff check .` | style and unused imports |
| imports | `lint-imports` | the package boundaries in [dependency-rules.md](../architecture/dependency-rules.md) |
| format | `black --check` | formatting, whole tree |
| types | `mypy src` | the whole `kinetic` package |
| deps | `deptry .` | unused, undeclared and transitive dependencies |
| tests | `pytest -q` | everything else |
| wheel | `scripts/dev/wheel_smoke.sh` | the built wheel imports, exposes the CLI, and carries its package data — verified from outside the repo |

`make help` lists the targets individually. Run the individual target while
iterating; run `make validate` before you finish.

## Tests

```
tests/
├── unit/          per subsystem: core, data, ingestion, processing, ml, research,
│                  interface, tools. No network, no credentials, ever.
├── integration/   several internal subsystems together, against fixtures
├── e2e/           a complete pipeline from the command line; import smoke;
│                  checked-in config validation
└── fixtures/      recorded and synthetic provider payloads, and research inputs
```

Anchor fixture paths with `tests/conftest.py`'s `REPO_ROOT` / `FIXTURES` rather
than `Path(__file__).parent`, so a test keeps working if it is refiled.

The live Alpaca test in `tests/integration/providers/` is skipped unless
`RUN_PROVIDER_INTEGRATION_TESTS=1` is set and real credentials are present.

## Where does my code go?

Answer the question the code answers, then put it in that package. The table is
in [platform-overview.md](../architecture/platform-overview.md); the enforcement
is in `pyproject.toml`'s `[tool.importlinter]`.

Two rules catch most mistakes:

- **Producing a score does not make it ML.** If the same input and config always
  produce the same output with no fitted model, it is `processing`.
- **A new shared helper does not need a shared package.** `common` was dissolved
  for a reason. Put the helper in the subsystem that owns it; if two subsystems
  need it, one of them owns it and the other depends on that one.

## Adding a pipeline task

1. Write the function: `def my_task(ctx: RunContext, params: dict) -> None`
2. Put it in the subsystem that owns the work — a provider call goes in
   `ingestion/<domain>/<provider>/tasks.py`, a deterministic transform in
   `processing/<domain>/tasks/`
3. Register it in `src/kinetic/bootstrap.py` under a namespaced id
4. Add it to the expected list in `tests/e2e/test_imports.py`
5. Never register it at import time, and never add a registration decorator

## Adding a config

Use the current shape — `pipeline.steps` with `task` and `params`. Every
checked-in config is validated by `tests/unit/interface/test_cli.py`, which fails
if a config names a task the platform does not have or uses a deprecated name.

```bash
kinetic config validate configs/my_pipeline.yaml --strict-task-names
```

Put private values in `configs/local.yaml` (git-ignored, deep-merged over
whatever you run). Start from `configs/local.example.yaml`.

## Generated data

Everything generated goes under `warehouse/`, which is git-ignored in full. Never
commit run outputs, caches or ledgers. Private article corpora live in
`data/real_corpus/` and `data/local_only/`, also git-ignored, with explicit
`.gitignore` rules that refuse to track article bodies — those rules exist
because of a rights obligation, not tidiness.

Preserved *research results* are different: they are tracked, under
`projects/<study>/results/`.

## The annotation workstation

```bash
streamlit run tools/annotation/app.py
```

Local-only, and repo-only: it is not part of the installable distribution. It is
importable in tests through the `pythonpath` entry in `pyproject.toml`, which
mirrors the `sys.path` Streamlit gives it at runtime.

## Releasing

```bash
make release-check
```

Requires a clean work tree (`ALLOW_DIRTY_TREE=1` overrides), runs `make validate`,
and writes a source archive plus SHA-256 to `dist/`.

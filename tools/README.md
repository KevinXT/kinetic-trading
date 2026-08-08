# tools

Supporting applications that are **not** part of pipeline execution. Nothing here
ships in the `kinetic` wheel; it is repo-only, and it may import `kinetic` but
`kinetic` never imports it.

| Path | What it is | How to run |
| --- | --- | --- |
| `annotation/` | The local-only Streamlit annotation workstation: corpus preflight, article annotation, duplicate review, adjudication, and audit/export | `streamlit run tools/annotation/app.py` |
| `run_collections.py` | Batch runner over `configs/collections/`, shelling out to the CLI once per config with throttling so one failure cannot take the batch down | `python tools/run_collections.py configs/collections` |
| `fixtures/` | Deterministic generators for the committed research and relevance-pilot test fixtures | `python tools/fixtures/generate_research_fixtures.py` |

## The annotation workstation

It handles real, rights-restricted article text, so it enforces content controls:
full bodies may be displayed locally but never written to run artifacts, logs,
exports, or any git-tracked path. Those rules are configuration
(`content_controls` in the UI config), not convention, and the store enforces
them.

It is importable in tests through the `pythonpath` entry in `pyproject.toml`,
which mirrors the `sys.path` Streamlit gives the app at runtime — that is why its
modules import each other by bare name.

Install its dependency with the `annotation` extra:

```bash
uv pip install -e ".[annotation]"
```

# Local Relevance Annotation Workstation

Trusted local Streamlit workstation for semiconductor relevance annotation.

It is **not** a public web service, trading dashboard, or multi-tenant security boundary.

## What it does

- Corpus preflight over the existing pilot task
- Single selected pilot-run context shared across all pages
- Blind article relevance annotation bound to corpus/article hashes
- Blind duplicate-pair review
- Relationship-safe adjudication without overwriting raw labels
- Batch-scoped deterministic JSONL export into ignored local paths
- Per-export readiness (raw / duplicate / adjudication / formal analysis)

SQLite stores durable append-only events. The research CLI remains the reproducible engine.

> Do not ingest real article bodies until the content-safety regression tests pass and the remediation commit is checked out.

## Setup

From the repository root, with `kinetic` installed:

```bash
uv pip install -e ".[annotation]"
```

Streamlit is scoped to this extra, not a core platform dependency.

## Launch

```bash
streamlit run tools/annotation/app.py
```

Defaults to `127.0.0.1` with usage stats disabled (see `.streamlit/config.toml`).

## Configuration

`projects/semiconductor_case_study/configs/semiconductor_relevance_annotation_ui_local.yaml`

- Database: `data/local_only/relevance_annotation_ui.sqlite3` (gitignored)
- Exports: `data/local_only/relevance_exports/` (gitignored)
- Real corpus root: `data/real_corpus/` (gitignored)

## Modes

`preflight` · `annotator` · `duplicate_reviewer` · `adjudicator` · `audit`

Mode selection is workflow separation only — not authentication.

## Backup / reset

- Copy the SQLite file to back up local audit history.
- Deleting the SQLite database destroys local annotation event history.
- Synthetic test databases under `tmp/` or pytest `tmp_path` are disposable.

## Content-safety and provenance

- Article HTML/text is shown with Streamlit `st.text` (escaped plain text).
- No remote images, scripts, or embedded article HTML.
- Rejected import rows serialize only `SafeImportRejectionV1` summaries (hashes/lengths), never full bodies.
- Real bodies stay in ignored local storage; exports omit bodies by default.
- Assignments bind `source_run_id`, corpus hash, article title/body hashes, guideline version, batch, and sample roles.
- UI pages load articles only from the bound artifact of the selected pilot context.
- Hash mismatch blocks display and save (`SOURCE_ARTIFACT_MISMATCH`).
- Entity controls display names but store canonical entity IDs.
- Evidence uses generated candidate IDs; fabricated `field@offset` values are rejected.
- Form submissions use stable client tokens so double-click / rerun does not create duplicate events.
- Annotators do not see baselines, challenge reasons, weights, or other labels.

## Limitations

- No models, sentiment, GPU, AWS, or return prediction
- No validated duplicate threshold selection in the UI
- No real rights-cleared corpus is required for software tests
- Trusted local workstation only — not multi-user security
- Not production hosting
- The system has not completed a real annotation pilot

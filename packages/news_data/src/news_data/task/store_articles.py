from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_INPUT_STATE_KEYS = ("deduped_articles", "filtered_articles", "normalized_articles")

_DEFAULT_OUTPUT_PATH = "data/processed/articles/articles.jsonl"


def _article_key(article: Dict[str, Any]) -> str | None:
    """Return a dedup key for *article*, or ``None`` if no usable fields exist."""
    url = str(article.get("url", "")).strip()
    if url:
        return url

    title = str(article.get("title", "")).strip().lower()
    domain = str(article.get("domain", "")).strip().lower()
    if title or domain:
        return f"{title}|{domain}"

    return None


def _load_existing_keys(path: Path) -> set[str]:
    """Read an existing JSONL dataset and return all known dedup keys."""
    keys: set[str] = set()
    if not path.is_file():
        return keys
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = _article_key(record)
            if key is not None:
                keys.add(key)
    return keys


def store_articles_task(ctx, params: Dict[str, Any]) -> None:
    """
    Pipeline task: persist cleaned articles into a long-term local dataset.

    Reads from the highest-fidelity available state key
    (deduped > filtered > normalized), appends only new records to the
    output JSONL file, and writes a run-level summary artifact.

    YAML shape::

        - type: store_articles
          output_path: "data/processed/articles/articles.jsonl"
    """
    articles: List[Dict[str, Any]] | None = None
    input_state_used: str | None = None

    for key in _INPUT_STATE_KEYS:
        if key in ctx.state:
            articles = ctx.state[key]
            input_state_used = key
            break

    if articles is None:
        raise ValueError(
            "store_articles requires ctx.state to contain "
            "'deduped_articles', 'filtered_articles', or 'normalized_articles'. "
            "Ensure an ingest or transform task runs before store_articles."
        )

    output_path = Path(params.get("output_path", _DEFAULT_OUTPUT_PATH))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    existing_keys = _load_existing_keys(output_path)
    existing_count = len(existing_keys)

    appended: List[Dict[str, Any]] = []
    skipped = 0
    unkeyed = 0

    for article in articles:
        if not isinstance(article, dict):
            continue

        key = _article_key(article)

        if key is None:
            unkeyed += 1
            appended.append(article)
            continue

        if key in existing_keys:
            skipped += 1
            continue

        existing_keys.add(key)
        appended.append(article)

    with output_path.open("a", encoding="utf-8") as fh:
        for record in appended:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = {
        "input_articles": len(articles),
        "existing_records": existing_count,
        "appended_records": len(appended),
        "skipped_duplicates": skipped,
        "unkeyed_records": unkeyed,
        "output_path": str(output_path),
        "input_state_used": input_state_used,
    }

    ctx.state["stored_articles_summary"] = summary
    ctx.write_json("store_summary.json", summary)

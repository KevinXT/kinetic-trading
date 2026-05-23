from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT_PATH = "data/processed/features/article_features_daily.jsonl"


def _feature_key(row: Dict[str, Any]) -> str | None:
    """Build a deterministic dedupe key for a feature row.

    Key components: date, topic, earliest_published_at, latest_published_at.
    Returns None only if both date and topic are missing/empty.
    """
    date = str(row.get("date", "")).strip()
    topic = str(row.get("topic", "")).strip()
    earliest = str(row.get("earliest_published_at") or "").strip()
    latest = str(row.get("latest_published_at") or "").strip()

    if not date and not topic:
        return None

    return f"{date}|{topic}|{earliest}|{latest}"


def _load_existing_keys(path: Path) -> set[str]:
    """Read an existing JSONL feature store and return all known dedupe keys."""
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
            key = _feature_key(record)
            if key is not None:
                keys.add(key)
    return keys


def _append_rows(path: Path, rows: List[Dict[str, Any]]) -> None:
    """Append feature rows to the JSONL file."""
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def store_features_task(ctx, params: Dict[str, Any]) -> None:
    """
    Pipeline task: persist aggregated feature rows into a historical store.

    Reads from ``ctx.state["article_features_daily"]``, appends only new
    rows to the output JSONL file (deduplicated by date/topic/timestamps),
    and writes a run-level summary artifact.

    YAML shape::

        - type: store_features
          output_path: "data/processed/features/article_features_daily.jsonl"
    """
    rows: List[Dict[str, Any]] | None = ctx.state.get("article_features_daily")

    if rows is None:
        raise ValueError(
            "store_features requires ctx.state to contain "
            "'article_features_daily'. "
            "Ensure aggregate_article_features runs before store_features."
        )

    output_path = Path(params.get("output_path", _DEFAULT_OUTPUT_PATH))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    existing_keys = _load_existing_keys(output_path)
    existing_count = len(existing_keys)

    appended: List[Dict[str, Any]] = []
    skipped = 0

    for row in rows:
        if not isinstance(row, dict):
            continue

        key = _feature_key(row)

        if key is None:
            appended.append(row)
            continue

        if key in existing_keys:
            skipped += 1
            continue

        existing_keys.add(key)
        appended.append(row)

    _append_rows(output_path, appended)

    total_rows = existing_count + len(appended)

    ctx.state["stored_feature_rows"] = appended

    summary = {
        "input_rows": len(rows),
        "stored_rows": len(appended),
        "skipped_duplicates": skipped,
        "output_path": str(output_path),
        "total_rows_after_write": total_rows,
    }

    ctx.write_json("store_features_summary.json", summary)

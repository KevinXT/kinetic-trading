"""
Local result cache for BigQuery GDELT counts.

"Cache before cloud": a cache hit skips the dry-run *and* the billable query.
Only real result rows are ever cached — dry-run-only runs must not write a fake
cache entry.

Default layout::

    data/cache/bigquery_gdelt_counts/<cache_key>.jsonl
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

JsonDict = Dict[str, Any]

DEFAULT_CACHE_DIR = "data/cache/bigquery_gdelt_counts"


def build_cache_key(
    *,
    provider: str,
    table: str,
    topic: Optional[str],
    query_terms: Sequence[str],
    start_date: str,
    end_date: str,
    builder_version: str,
    search_columns: Optional[Sequence[str]] = None,
    partition_filter: Optional[Mapping[str, Any]] = None,
    query_name: Optional[str] = None,
    sql: Optional[str] = None,
) -> str:
    """
    Build a deterministic SHA256 cache key.

    Terms are normalized (stripped, lowercased, sorted) so logically equivalent
    term lists map to the same key regardless of order or casing. ``search_columns``
    is part of the key because scanning different columns can match different
    articles and thus produce different results. ``partition_filter`` is included
    because enabling/disabling partition pruning changes the generated SQL.

    ``query_name`` and ``sql`` are optional and only added to the payload when
    provided, so callers that omit them (e.g. the daily-count task) keep stable
    keys. When ``sql`` is given, its SHA256 is folded in so that *any* change to
    the generated query — patterns, limit, theme column, or builder shape — yields
    a fresh key and can never reuse a stale cached result.
    """
    norm_terms = sorted(t.strip().lower() for t in query_terms if isinstance(t, str) and t.strip())
    norm_columns = sorted(
        c.strip() for c in (search_columns or []) if isinstance(c, str) and c.strip()
    )
    pf = dict(partition_filter or {})
    norm_partition = {
        "enabled": bool(pf.get("enabled", False)),
        "column": str(pf.get("column", "")).strip(),
        "type": str(pf.get("type", "")).strip().lower(),
    }
    payload: Dict[str, Any] = {
        "provider": provider,
        "table": table,
        "topic": topic,
        "query_terms": norm_terms,
        "search_columns": norm_columns,
        "partition_filter": norm_partition,
        "start_date": start_date,
        "end_date": end_date,
        "builder_version": builder_version,
    }
    if query_name is not None:
        payload["query_name"] = str(query_name)
    if sql is not None:
        payload["sql_sha256"] = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def bigquery_cache_path(cache_key: str, *, base_dir: str | Path = DEFAULT_CACHE_DIR) -> Path:
    """Return the JSONL cache file path for ``cache_key``."""
    return Path(base_dir) / f"{cache_key}.jsonl"


def load_cached_rows(path: str | Path) -> Optional[List[JsonDict]]:
    """
    Load cached rows from ``path``.

    Returns ``None`` if the cache file does not exist (a miss). An empty-but-
    present cache file returns ``[]`` (a hit with zero rows).
    """
    p = Path(path)
    if not p.is_file():
        return None
    rows: List[JsonDict] = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def write_cached_rows(path: str | Path, rows: List[JsonDict]) -> Path:
    """Write ``rows`` to the cache file as JSONL (creating parent dirs)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return p

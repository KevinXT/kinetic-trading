"""
Normalize raw BigQuery GDELT daily-count rows into topic_daily_features rows.

Output rows are intentionally compatible with the local feature-store schema so
they flow into future ``store_topic_daily_features`` / ``detect_topic_trends``
tasks without translation. Fields that BigQuery counts cannot provide
(sentiment, source counts, coverage share) are present but null.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Sequence

JsonDict = Dict[str, Any]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_date(value: Any) -> str:
    """Normalize a BigQuery DATE value to ``YYYY-MM-DD``."""
    if isinstance(value, (datetime, date)):
        d = value.date() if isinstance(value, datetime) else value
        return d.isoformat()
    text = str(value).strip()
    if not text:
        return ""
    # Integer/compact form: 20260503
    compact = text.replace("-", "")
    if len(compact) == 8 and compact.isdigit():
        return f"{compact[0:4]}-{compact[4:6]}-{compact[6:8]}"
    # Already ISO-ish: take the leading date component.
    return text[:10]


def _to_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(float(value))


def normalize_gdelt_daily_counts(
    rows: List[JsonDict],
    topic: str,
    query_terms: Sequence[str],
    provider: str = "bigquery_gdelt",
    mode: str = "daily_counts",
) -> List[JsonDict]:
    """
    Normalize BigQuery rows (``{"date": ..., "article_count": ...}``) into
    topic_daily_features rows. Empty input returns an empty list.
    """
    ingested_at = _utc_now_iso()
    terms = [t for t in (query_terms or []) if isinstance(t, str) and t.strip()]

    out: List[JsonDict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "provider": provider,
                "mode": mode,
                "topic": topic,
                "date": _normalize_date(row.get("date")),
                "article_count": _to_int(row.get("article_count")),
                "source_count": None,
                "avg_sentiment": None,
                "coverage_share": None,
                "query_terms": list(terms),
                "ingested_at": ingested_at,
            }
        )
    return out

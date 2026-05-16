from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


_DEFAULT_BY: List[str] = ["url"]
_FALLBACK_BY: List[str] = ["title", "domain"]


def _build_key(article: Dict[str, Any], fields: Sequence[str]) -> Optional[str]:
    """
    Build a dedupe key from the requested fields.

    Returns None if every requested field is missing or empty, meaning
    no usable key could be constructed from this combination.
    """
    parts: List[str] = []
    for f in fields:
        val = str(article.get(f, "")).strip().lower()
        if val:
            parts.append(val)

    return "|".join(parts) if parts else None


def dedupe_articles_task(ctx, params: Dict[str, Any]) -> None:
    """
    Pipeline task: remove duplicate articles by configurable key fields.

    Reads from ``filtered_articles`` (preferred) or ``normalized_articles``.
    First occurrence wins; original order is preserved.

    YAML shape::

        - type: dedupe_articles
          by: ["url", "title"]
    """
    if "filtered_articles" in ctx.state:
        articles = ctx.state["filtered_articles"]
        input_state_used = "filtered_articles"
    elif "normalized_articles" in ctx.state:
        articles = ctx.state["normalized_articles"]
        input_state_used = "normalized_articles"
    else:
        raise ValueError(
            "dedupe_articles requires ctx.state to contain "
            "'filtered_articles' or 'normalized_articles'. "
            "Ensure an ingest or filter task runs before dedupe."
        )

    by: List[str] = params.get("by", _DEFAULT_BY)

    seen: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    unkeyed = 0

    for article in articles:
        if not isinstance(article, dict):
            continue

        key = _build_key(article, by)

        if key is None:
            key = _build_key(article, _FALLBACK_BY)

        if key is None:
            unkeyed += 1
            deduped.append(article)
            continue

        if key not in seen:
            seen.add(key)
            deduped.append(article)

    ctx.state["deduped_articles"] = deduped

    ctx.write_jsonl("deduped_articles.jsonl", deduped)
    ctx.write_json(
        "dedupe_summary.json",
        {
            "input_articles": len(articles),
            "deduped_articles": len(deduped),
            "duplicates_removed": len(articles) - len(deduped),
            "unkeyed_articles": unkeyed,
            "dedupe_fields_used": by,
            "input_state_used": input_state_used,
        },
    )

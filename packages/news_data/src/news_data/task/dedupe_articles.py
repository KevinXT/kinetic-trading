from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_VALID_STRATEGIES = ("url", "title", "title_domain")
_DEFAULT_STRATEGY = "title"


def _normalize(value: Any) -> str:
    """Lowercase, strip, and stringify a field value."""
    return str(value).strip().lower() if value else ""


def _build_key(article: Dict[str, Any], strategy: str) -> Optional[str]:
    """
    Build a dedupe key from the chosen strategy.

    Returns None when no usable key can be constructed.
    """
    if strategy == "url":
        url = _normalize(article.get("url"))
        return url or None

    if strategy == "title":
        title = _normalize(article.get("title"))
        return title or None

    if strategy == "title_domain":
        title = _normalize(article.get("title"))
        domain = _normalize(article.get("domain"))
        if title:
            return f"{title}|{domain}" if domain else title
        return None

    return None


def dedupe_articles_task(ctx, params: Dict[str, Any]) -> None:
    """
    Pipeline task: remove duplicate articles by configurable strategy.

    Reads from ``filtered_articles`` (preferred) or ``normalized_articles``.
    First occurrence wins; original order is preserved.  Duplicate group
    metadata is written as a separate artifact for syndication analysis.

    YAML shape::

        - type: dedupe_articles
          dedupe_strategy: "title"

    Supported strategies: ``url``, ``title``, ``title_domain``.

    Legacy ``by`` field is still accepted for backward compatibility and
    maps to the closest matching strategy.
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

    strategy = _resolve_strategy(params)

    # key -> list of all articles sharing that key (first = canonical)
    groups: Dict[str, List[Dict[str, Any]]] = {}
    group_order: List[str] = []

    deduped: List[Dict[str, Any]] = []
    unkeyed = 0

    for article in articles:
        if not isinstance(article, dict):
            continue

        key = _build_key(article, strategy)

        if key is None:
            unkeyed += 1
            deduped.append(article)
            continue

        if key not in groups:
            groups[key] = [article]
            group_order.append(key)
            deduped.append(article)
        else:
            groups[key].append(article)

    ctx.state["deduped_articles"] = deduped

    # Build duplicate-group metadata (only for groups with actual duplicates).
    dup_groups = _build_duplicate_groups(groups, group_order, strategy)
    ctx.state["duplicate_groups"] = dup_groups

    ctx.write_jsonl("deduped_articles.jsonl", deduped)

    if dup_groups:
        ctx.write_jsonl("duplicate_groups.jsonl", dup_groups)

    total_dup_articles = sum(g["duplicate_count"] for g in dup_groups)
    max_group_size = max((g["total_seen"] for g in dup_groups), default=0)

    ctx.write_json(
        "dedupe_summary.json",
        {
            "input_articles": len(articles),
            "deduped_articles": len(deduped),
            "duplicates_removed": len(articles) - len(deduped),
            "unique_articles": len(deduped) - unkeyed,
            "unkeyed_articles": unkeyed,
            "dedupe_strategy": strategy,
            "duplicate_groups": len(dup_groups),
            "max_duplicate_group_size": max_group_size,
            "total_duplicate_articles": total_dup_articles,
            "input_state_used": input_state_used,
        },
    )


def _build_duplicate_groups(
    groups: Dict[str, List[Dict[str, Any]]],
    group_order: List[str],
    strategy: str,
) -> List[Dict[str, Any]]:
    """Build duplicate-group records for keys that appeared more than once."""
    result: List[Dict[str, Any]] = []
    for key in group_order:
        members = groups[key]
        if len(members) < 2:
            continue

        canonical = members[0]
        duplicates = members[1:]

        all_domains: List[str] = []
        seen_domains: set[str] = set()
        for m in members:
            d = _normalize(m.get("domain"))
            if d and d not in seen_domains:
                seen_domains.add(d)
                all_domains.append(d)

        dup_urls = [str(d.get("url", "")) for d in duplicates]

        dup_titles_seen: set[str] = set()
        dup_titles: List[str] = []
        for d in duplicates:
            t = str(d.get("title", ""))
            normalized_t = t.strip().lower()
            if normalized_t and normalized_t not in dup_titles_seen:
                dup_titles_seen.add(normalized_t)
                dup_titles.append(t)

        result.append(
            {
                "canonical_title": str(canonical.get("title", "")),
                "canonical_url": str(canonical.get("url", "")),
                "dedupe_strategy": strategy,
                "dedupe_key": key,
                "duplicate_count": len(duplicates),
                "total_seen": len(members),
                "domains": all_domains,
                "duplicate_urls": dup_urls,
                "duplicate_titles": dup_titles,
            }
        )

    return result


def _resolve_strategy(params: Dict[str, Any]) -> str:
    """Return the dedupe strategy, supporting both new and legacy config keys."""
    if "dedupe_strategy" in params:
        strategy = params["dedupe_strategy"]
        if strategy not in _VALID_STRATEGIES:
            raise ValueError(
                f"Unknown dedupe_strategy '{strategy}'. "
                f"Valid options: {', '.join(_VALID_STRATEGIES)}"
            )
        return strategy

    if "by" in params:
        by: List[str] = params["by"]
        if by == ["url"]:
            return "url"
        if by == ["title"]:
            return "title"
        if set(by) == {"title", "domain"}:
            return "title_domain"
        logger.warning(
            "Legacy 'by' value %s does not map to a known strategy; "
            "falling back to '%s'.",
            by,
            _DEFAULT_STRATEGY,
        )

    return _DEFAULT_STRATEGY

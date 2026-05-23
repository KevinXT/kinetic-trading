from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_INPUT_STATE_KEYS = (
    "tagged_articles",
    "deduped_articles",
    "filtered_articles",
    "normalized_articles",
)

_REQUIRED_GROUP_BY = {"date", "topic"}


def _extract_date(article: Dict[str, Any]) -> str:
    """Pull YYYY-MM-DD from an ISO-8601 published_at field."""
    raw = article.get("published_at")
    if not raw or not isinstance(raw, str):
        return "unknown"
    try:
        return raw[:10]
    except Exception:
        return "unknown"


def _extract_topics(article: Dict[str, Any]) -> List[str]:
    """Return the list of topics for an article, falling back gracefully."""
    topics = article.get("topics")
    if isinstance(topics, list) and topics:
        return topics

    primary = article.get("primary_topic")
    if primary:
        return [primary]

    return ["untagged"]


def _match_dup_groups(
    dup_groups: List[Dict[str, Any]],
    group_articles: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return duplicate groups whose canonical article appears in group_articles.

    Matches on lowercased title or canonical_url to keep it simple.
    """
    titles = {str(a.get("title", "")).strip().lower() for a in group_articles}
    urls = {str(a.get("url", "")).strip().lower() for a in group_articles}

    matched: List[Dict[str, Any]] = []
    for dg in dup_groups:
        canon_title = str(dg.get("canonical_title", "")).strip().lower()
        canon_url = str(dg.get("canonical_url", "")).strip().lower()
        if (canon_title and canon_title in titles) or (canon_url and canon_url in urls):
            matched.append(dg)
    return matched


def aggregate_article_features_task(ctx, params: Dict[str, Any]) -> None:
    """
    Pipeline task: aggregate tagged articles into daily topic-level feature rows.

    Reads from the highest-fidelity available state key
    (tagged > deduped > filtered > normalized).  Groups articles by date
    and topic and computes volume, domain diversity, and optional
    amplification metrics from duplicate group state.

    YAML shape::

        - type: aggregate_article_features
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
            "aggregate_article_features requires ctx.state to contain "
            "'tagged_articles', 'deduped_articles', 'filtered_articles', "
            "or 'normalized_articles'. "
            "Ensure an ingest or transform task runs before aggregate_article_features."
        )

    group_by = params.get("group_by", ["date", "topic"])
    if set(group_by) != _REQUIRED_GROUP_BY:
        raise ValueError(
            f"aggregate_article_features currently supports group_by: "
            f"['date', 'topic'] only. Received: {group_by}"
        )

    dup_groups: Optional[List[Dict[str, Any]]] = ctx.state.get("duplicate_groups")

    # Bucket articles into (date, topic) groups.
    buckets: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
    bucket_order: List[tuple[str, str]] = []
    untagged_count = 0

    for article in articles:
        if not isinstance(article, dict):
            continue

        date = _extract_date(article)
        topics = _extract_topics(article)

        if topics == ["untagged"]:
            untagged_count += 1

        for topic in topics:
            key = (date, topic)
            if key not in buckets:
                buckets[key] = []
                bucket_order.append(key)
            buckets[key].append(article)

    rows: List[Dict[str, Any]] = []
    all_dates: set[str] = set()
    all_topics: set[str] = set()

    for key in sorted(bucket_order):
        date, topic = key
        members = buckets[key]
        all_dates.add(date)
        all_topics.add(topic)

        domains: List[str] = []
        seen_domains: set[str] = set()
        countries: List[str] = []
        seen_countries: set[str] = set()
        queries: List[str] = []
        seen_queries: set[str] = set()
        timestamps: List[str] = []
        primary_topic_ct = 0

        for a in members:
            d = str(a.get("domain", "")).strip().lower()
            if d and d not in seen_domains:
                seen_domains.add(d)
                domains.append(d)

            sc = str(a.get("source_country", "")).strip()
            sc_key = sc.lower()
            if sc and sc_key not in seen_countries:
                seen_countries.add(sc_key)
                countries.append(sc)

            q = str(a.get("query", "")).strip()
            q_key = q.lower()
            if q and q_key not in seen_queries:
                seen_queries.add(q_key)
                queries.append(q)

            pa = a.get("published_at")
            if pa and isinstance(pa, str):
                timestamps.append(pa)

            if a.get("primary_topic") == topic:
                primary_topic_ct += 1

        row: Dict[str, Any] = {
            "date": date,
            "topic": topic,
            "article_count": len(members),
            "unique_domains": len(domains),
            "unique_source_countries": len(countries),
            "domains": sorted(domains),
            "source_countries": sorted(countries),
            "primary_topic_count": primary_topic_ct,
            "untagged_count": len(members) if topic == "untagged" else 0,
            "queries": sorted(queries),
            "earliest_published_at": min(timestamps) if timestamps else None,
            "latest_published_at": max(timestamps) if timestamps else None,
        }

        if dup_groups is not None:
            matched = _match_dup_groups(dup_groups, members)
            row["duplicate_groups"] = len(matched)
            row["duplicate_articles_removed"] = sum(
                dg.get("duplicate_count", 0) for dg in matched
            )
            row["max_group_total_seen"] = max(
                (dg.get("total_seen", 0) for dg in matched), default=0
            )

        rows.append(row)

    ctx.state["article_features_daily"] = rows

    ctx.write_jsonl("article_features_daily.jsonl", rows)
    ctx.write_json(
        "article_features_summary.json",
        {
            "input_articles": len(articles),
            "feature_rows": len(rows),
            "dates": sorted(all_dates),
            "topics": sorted(all_topics),
            "input_state_used": input_state_used,
            "untagged_articles": untagged_count,
        },
    )

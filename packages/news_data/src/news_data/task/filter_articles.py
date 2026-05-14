from __future__ import annotations

from typing import Any, Dict, List


def filter_articles_task(ctx, params: Dict[str, Any]) -> None:
    articles = ctx.state.get("normalized_articles")

    if articles is None:
        raise ValueError("filter_articles must run after gdelt_docs.")

    if not isinstance(articles, list):
        raise TypeError("normalized_articles must be a list.")

    language = params.get("language")
    source_country = params.get("source_country")
    domain = params.get("domain")

    filtered: List[Dict[str, Any]] = []

    for article in articles:
        if not isinstance(article, dict):
            continue

        if language is not None and article.get("language") != language:
            continue

        if source_country is not None and article.get("source_country") != source_country:
            continue

        if domain is not None and article.get("domain") != domain:
            continue

        filtered.append(article)

    ctx.state["filtered_articles"] = filtered

    ctx.write_jsonl("filtered_articles.jsonl", filtered)
    ctx.write_json(
        "filter_summary.json",
        {
            "input_articles": len(articles),
            "filtered_articles": len(filtered),
            "filters": {
                "language": language,
                "source_country": source_country,
                "domain": domain,
            },
        },
    )
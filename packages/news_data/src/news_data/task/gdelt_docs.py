from __future__ import annotations

from typing import Any, Dict
from common.cache import get_or_fetch_json

from news_data.gdelt import GdeltDocClient, doc_request_from_config, extract_articles
from news_data.gdelt.normalize import normalize_articles


def gdelt_docs_task(ctx, params: Dict[str, Any]) -> None:
    """
    Pipeline task: fetch GDELT DOC articles and write raw + normalized artifacts.

    This task is the bridge between the pipeline runner and the reusable GDELT
    package. It should stay thin: read YAML params, call the GDELT client,
    normalize the article records, store state for downstream tasks, and write
    artifacts.

    YAML shape:
        pipeline:
          ingest:
            source: gdelt_docs
            query: "artificial intelligence"
            max_records: 10
    """
    query = str(params.get("query", "")).strip()
    if not query:
        raise ValueError("gdelt_docs requires a non-empty 'query' parameter.")

    max_records = int(params.get("max_records", 50))

    req = doc_request_from_config(ctx.cfg, query=query)

    # Let the YAML override the provider config default.
    req = type(req)(
        base_url=req.base_url,
        query=req.query,
        mode=req.mode,
        format=req.format,
        timespan=req.timespan,
        maxrecords=max_records,
        sort=req.sort,
    )

    cache_payload = {
        "provider": "gdelt",
        "endpoint": "doc",
        "query": req.query,
        "mode": req.mode,
        "format": req.format,
        "timespan": req.timespan,
        "maxrecords": req.maxrecords,
        "sort": req.sort,
    }

    client = GdeltDocClient.from_config(ctx.cfg)

    try:
        raw_response = get_or_fetch_json(
            namespace="gdelt_doc",
            payload=cache_payload,
            fetch_fn=lambda: client.fetch_doc(req),
        )
        articles = extract_articles(raw_response)
    finally:
        client.close()

    normalized_articles = normalize_articles(articles, query=query)

    ctx.state["gdelt_articles"] = articles
    ctx.state["normalized_articles"] = normalized_articles

    ctx.write_json("gdelt_response_raw.json", raw_response)
    ctx.write_jsonl("gdelt_articles.jsonl", articles)
    ctx.write_jsonl("normalized_articles.jsonl", normalized_articles)
    ctx.write_json(
        "gdelt_summary.json",
        {
            "query": query,
            "requested_max_records": max_records,
            "articles_returned": len(articles),
            "normalized_articles": len(normalized_articles),
        },
    )
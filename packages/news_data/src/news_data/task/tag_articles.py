from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_INPUT_STATE_KEYS = ("deduped_articles", "filtered_articles", "normalized_articles")

_DEFAULT_RULES: Dict[str, List[str]] = {
    "inflation_rates": [
        "inflation",
        "CPI inflation",
        "PCE inflation",
        "Federal Reserve",
        "interest rates",
        "Treasury yields",
    ],
    "labor_jobs": [
        "jobs report",
        "unemployment",
        "labor market",
        "layoffs",
        "wage growth",
        "hiring slowdown",
        "nonfarm payrolls",
    ],
    "growth_recession": [
        "economic slowdown",
        "recession",
        "GDP growth",
        "consumer confidence",
        "manufacturing PMI",
        "ISM manufacturing",
    ],
    "housing_real_estate": [
        "housing market",
        "mortgage rates",
        "home sales",
        "commercial real estate",
        "rent prices",
        "office vacancy",
    ],
    "consumer_spending": [
        "consumer spending",
        "retail sales",
        "credit card debt",
        "household debt",
        "discretionary spending",
    ],
    "government_debt": [
        "government debt",
        "treasury issuance",
        "fiscal deficit",
        "sovereign debt",
        "debt ceiling",
        "bond auction",
    ],
    "ai": [
        "artificial intelligence",
        "machine learning",
        "OpenAI",
        "large language models",
        "AI chips",
        "data centers",
    ],
    "semiconductors": [
        "semiconductor",
        "GPU",
        "Nvidia",
        "TSMC",
        "ASML",
        "chip supply",
        "export controls",
    ],
    "energy": [
        "oil prices",
        "crude oil",
        "OPEC",
        "natural gas",
        "energy markets",
        "refinery",
        "LNG exports",
    ],
    "financials": [
        "banking sector",
        "credit losses",
        "loan defaults",
        "net interest income",
        "commercial real estate exposure",
    ],
    "healthcare_biotech": [
        "biotech",
        "pharmaceutical",
        "healthcare sector",
        "FDA approval",
        "drug development",
        "clinical trial",
    ],
    "defense_aerospace": [
        "defense stocks",
        "military spending",
        "aerospace",
        "missiles",
        "drones",
        "defense contractors",
        "weapons systems",
    ],
    "autos_ev": [
        "electric vehicles",
        "EV market",
        "Tesla",
        "battery supply chain",
        "autonomous driving",
        "EV sales",
    ],
    "crypto_fintech": [
        "cryptocurrency",
        "Bitcoin",
        "Ethereum",
        "fintech",
        "digital payments",
        "stablecoins",
        "crypto regulation",
    ],
    "geopolitics": [
        "geopolitical risk",
        "sanctions",
        "military escalation",
        "trade tensions",
        "armed conflict",
        "shipping disruption",
    ],
    "banking_credit": [
        "bank failures",
        "liquidity crisis",
        "credit markets",
        "loan defaults",
        "regional banks",
        "commercial real estate loans",
    ],
    "supply_chain": [
        "supply chain",
        "logistics",
        "freight rates",
        "port disruption",
        "container rates",
        "shipping delays",
    ],
    "regulation_antitrust": [
        "antitrust",
        "tech regulation",
        "SEC investigation",
        "DOJ investigation",
        "regulatory crackdown",
        "competition lawsuit",
    ],
    "cybersecurity": [
        "cyberattack",
        "ransomware",
        "data breach",
        "cybersecurity",
        "critical infrastructure hack",
    ],
    "climate_disasters": [
        "hurricane",
        "drought",
        "wildfire",
        "flood",
        "climate disaster",
        "extreme weather",
        "crop damage",
    ],
    "equities": [
        "stock market",
        "Nasdaq",
        "S&P 500",
        "Dow Jones",
        "Wall Street",
        "equity markets",
    ],
    "bonds_yields": [
        "bond yields",
        "Treasury yields",
        "yield curve",
        "bond market",
        "fixed income",
        "10-year Treasury",
    ],
    "commodities": [
        "gold prices",
        "copper prices",
        "commodities market",
        "rare earth metals",
        "commodity trading",
        "industrial metals",
    ],
    "china": [
        "China economy",
        "Chinese markets",
        "yuan",
        "property crisis",
        "China exports",
        "Xi Jinping",
        "China stimulus",
    ],
    "currencies_dollar": [
        "US dollar",
        "dollar strength",
        "forex markets",
        "currency markets",
        "exchange rates",
        "yen carry trade",
    ],
}


def _match_topics(
    article: Dict[str, Any],
    rules: Dict[str, List[str]],
) -> tuple[List[str], Dict[str, List[str]], str | None]:
    """Match an article against topic rules.

    Returns (topics, topic_matches, primary_topic).
    """
    title = str(article.get("title", "")).lower()
    query = str(article.get("query", "")).lower()
    domain = str(article.get("domain", "")).lower()
    searchable = f"{title} | {query} | {domain}"

    topics: List[str] = []
    topic_matches: Dict[str, List[str]] = {}

    for topic, keywords in rules.items():
        matched: List[str] = []
        for kw in keywords:
            if kw.lower() in searchable:
                matched.append(kw)
        if matched:
            topics.append(topic)
            topic_matches[topic] = matched

    primary_topic = topics[0] if topics else None
    return topics, topic_matches, primary_topic


def tag_articles_task(ctx, params: Dict[str, Any]) -> None:
    """
    Pipeline task: enrich articles with deterministic topic tags.

    Reads from the highest-fidelity available state key
    (deduped > filtered > normalized).  Each article receives ``topics``,
    ``topic_matches``, and ``primary_topic`` fields based on keyword rules
    matched against the article title, query, and domain.

    YAML shape::

        - type: tag_articles
          rules:
            ai:
              - artificial intelligence
              - machine learning
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
            "tag_articles requires ctx.state to contain "
            "'deduped_articles', 'filtered_articles', or 'normalized_articles'. "
            "Ensure an ingest or transform task runs before tag_articles."
        )

    rules: Dict[str, List[str]] = params.get("rules", _DEFAULT_RULES)

    tagged: List[Dict[str, Any]] = []
    topic_counts: Dict[str, int] = {}
    untagged = 0

    for article in articles:
        if not isinstance(article, dict):
            continue

        topics, topic_matches, primary_topic = _match_topics(article, rules)

        enriched = {**article}
        enriched["topics"] = topics
        enriched["topic_matches"] = topic_matches
        enriched["primary_topic"] = primary_topic
        tagged.append(enriched)

        if topics:
            for t in topics:
                topic_counts[t] = topic_counts.get(t, 0) + 1
        else:
            untagged += 1

    ctx.state["tagged_articles"] = tagged

    ctx.write_jsonl("tagged_articles.jsonl", tagged)
    ctx.write_json(
        "tag_summary.json",
        {
            "input_articles": len(articles),
            "tagged_articles": len(tagged) - untagged,
            "untagged_articles": untagged,
            "topics_found": sorted(topic_counts.keys()),
            "topic_counts": dict(sorted(topic_counts.items())),
            "input_state_used": input_state_used,
        },
    )

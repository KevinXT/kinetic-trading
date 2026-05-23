from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

NormalizedArticle = Dict[str, Any]


def _clean_string(value: Any) -> str:
    """Return a stripped string, or an empty string for missing values."""
    if value is None:
        return ""
    return str(value).strip()


def _parse_gdelt_seen_date(value: Any) -> Optional[str]:
    """
    Convert GDELT seendate values into ISO-8601 UTC strings.

    GDELT DOC commonly returns dates like:
        20260508T000000Z

    The normalized pipeline format should be easier for downstream modules:
        2026-05-08T00:00:00Z
    """
    raw = _clean_string(value)
    if not raw:
        return None

    try:
        dt = datetime.strptime(raw, "%Y%m%dT%H%M%SZ")
    except ValueError:
        return None

    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_article(article: Dict[str, Any], *, query: str = "") -> NormalizedArticle:
    """
    Convert one raw GDELT article record into the internal article shape.

    This function intentionally does not know anything about pipeline execution,
    RunContext, artifacts, or tasks. It only translates GDELT field names into
    stable field names used by the rest of the project.
    """
    title = _clean_string(article.get("title"))
    url = _clean_string(article.get("url"))
    mobile_url = _clean_string(article.get("url_mobile"))
    domain = _clean_string(article.get("domain")).lower()
    language = _clean_string(article.get("language"))
    source_country = _clean_string(article.get("sourcecountry"))
    image_url = _clean_string(article.get("socialimage"))
    seen_date = _clean_string(article.get("seendate"))
    existing_ingested_at = _clean_string(article.get("ingested_at"))

    return {
        "provider": "gdelt",
        "query": _clean_string(query),
        "title": title,
        "url": url,
        "mobile_url": mobile_url,
        "domain": domain,
        "language": language,
        "source_country": source_country,
        "published_at": _parse_gdelt_seen_date(seen_date),
        "raw_seen_date": seen_date,
        "image_url": image_url,
        "ingested_at": (
            existing_ingested_at
            or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        ),
    }


def normalize_articles(
    articles: Iterable[Dict[str, Any]],
    *,
    query: str = "",
) -> List[NormalizedArticle]:
    """Normalize many raw GDELT article records."""
    normalized: List[NormalizedArticle] = []

    for article in articles:
        if not isinstance(article, dict):
            continue
        normalized.append(normalize_article(article, query=query))

    return normalized

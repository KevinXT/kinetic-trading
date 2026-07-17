"""Deterministic, versioned URL and text normalization for article research."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

NORMALIZER_VERSION = "article-normalizer-v1"

# Clearly recognized tracking parameters. Do not strip arbitrary query keys;
# some parameters identify different articles.
_TRACKING_QUERY_KEYS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
    }
)

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_url(url: str) -> str:
    """Conservative URL normalization for identity comparison.

    Handles hostname casing, default ports, fragments, common tracking
    parameters, and stable ordering of retained query parameters.
    """
    raw = str(url).strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"cannot normalize non-http(s) URL: {url!r}")

    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError(f"URL missing hostname: {url!r}")

    port = parsed.port
    if port is not None:
        if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
            netloc = hostname
        else:
            netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    path = parsed.path or ""
    # Preserve path; only collapse empty trailing fragment (handled by clearing).
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_QUERY_KEYS
    ]
    query_pairs.sort(key=lambda item: (item[0], item[1]))
    query = urlencode(query_pairs, doseq=True)

    return urlunparse((scheme, netloc, path, "", query, ""))


def normalize_text_for_hash(text: str) -> str:
    """Unicode + whitespace + case normalization for hashing / duplicate analysis.

    Preserves financially meaningful punctuation and numbers. The normalized
    comparison representation must not replace stored original text.
    """
    if text is None:
        raise ValueError("text must not be None")
    normalized = unicodedata.normalize("NFKC", str(text))
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    return normalized.casefold()


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_article_id(
    *,
    provider: str,
    provider_record_id: str,
    normalized_url: str,
    title_sha256: str,
) -> str:
    """Deterministic article_id from stable identity fields."""
    material = "|".join(
        [
            str(provider).strip().lower(),
            str(provider_record_id).strip(),
            str(normalized_url).strip(),
            str(title_sha256).strip(),
        ]
    )
    digest = sha256_hex(material)[:32]
    return f"art_{digest}"


def domain_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"URL missing hostname: {url!r}")
    return host


def parse_optional_datetime(value: Optional[object], field_name: str):
    """Parse ISO datetime text; reject naive values. Returns datetime | None."""
    from datetime import datetime, timezone

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        return value.astimezone(timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return dt.astimezone(timezone.utc)

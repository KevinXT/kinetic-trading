"""Versioned article-text research records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

from kinetic.data.serialization import to_json_value

SCHEMA_VERSION = "article-text-record-v1"

CONTENT_STATUSES = frozenset(
    {
        "available",
        "metadata_only",
        "not_collected",
        "provider_unavailable",
        "retrieval_failed",
        "rights_restricted",
        "unsupported",
    }
)

CONTENT_SOURCES = frozenset(
    {
        "local_corpus",
        "gdelt_doc_metadata",
        "user_supplied",
        "synthetic_fixture",
        "unknown",
    }
)


def _req_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _optional_utc(value: Optional[datetime], field_name: str) -> Optional[datetime]:
    if value is None:
        return None
    return _utc(value, field_name)


def _validate_url(url: str) -> str:
    cleaned = _req_text(url, "url")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"url must be an absolute http(s) URL, got {url!r}")
    return cleaned


@dataclass(frozen=True)
class ArticleTextRecordV1:
    """Article-level text/metadata record for offline relevance research.

    Timestamp meanings remain distinct:
    - published_at: timestamp reported for publication
    - ingested_at: when Kinetic observed or imported the record
    - retrieved_at: when optional article content was obtained

    None of these proves the exact historical time a live trading system could
    have consumed the information. Never substitute ingested_at for published_at.
    """

    article_id: str
    provider: str
    provider_record_id: str
    url: str
    normalized_url: str
    canonical_url: Optional[str]
    domain: str
    language: Optional[str]
    source_country: Optional[str]
    title: str
    description: Optional[str]
    body: Optional[str]
    published_at: Optional[datetime]
    ingested_at: datetime
    retrieved_at: Optional[datetime]
    content_status: str
    content_source: str
    title_sha256: str
    body_sha256: Optional[str]
    normalization_version: str
    schema_version: str = SCHEMA_VERSION
    published_at_source: Optional[str] = None
    published_timezone: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "article_id", _req_text(self.article_id, "article_id"))
        object.__setattr__(self, "provider", _req_text(self.provider, "provider"))
        object.__setattr__(
            self, "provider_record_id", _req_text(self.provider_record_id, "provider_record_id")
        )
        object.__setattr__(self, "url", _validate_url(self.url))
        object.__setattr__(self, "normalized_url", _req_text(self.normalized_url, "normalized_url"))
        if self.canonical_url is not None:
            object.__setattr__(self, "canonical_url", _validate_url(self.canonical_url))
        object.__setattr__(self, "domain", _req_text(self.domain, "domain").lower())
        title = _req_text(self.title, "title")
        object.__setattr__(self, "title", title)
        if self.description is not None:
            desc = str(self.description).strip()
            object.__setattr__(self, "description", desc if desc else None)
        if self.body is not None:
            body = str(self.body)
            if not body.strip():
                object.__setattr__(self, "body", None)
            else:
                object.__setattr__(self, "body", body)
        object.__setattr__(self, "published_at", _optional_utc(self.published_at, "published_at"))
        object.__setattr__(self, "ingested_at", _utc(self.ingested_at, "ingested_at"))
        object.__setattr__(self, "retrieved_at", _optional_utc(self.retrieved_at, "retrieved_at"))
        if self.content_status not in CONTENT_STATUSES:
            raise ValueError(
                f"unsupported content_status {self.content_status!r}; "
                f"expected one of {sorted(CONTENT_STATUSES)}"
            )
        if self.content_source not in CONTENT_SOURCES:
            raise ValueError(
                f"unsupported content_source {self.content_source!r}; "
                f"expected one of {sorted(CONTENT_SOURCES)}"
            )
        object.__setattr__(self, "title_sha256", _req_text(self.title_sha256, "title_sha256"))
        if self.body is None:
            if self.body_sha256 is not None:
                raise ValueError("body_sha256 must be null when body is null")
        else:
            if self.body_sha256 is None or not str(self.body_sha256).strip():
                raise ValueError("body_sha256 is required when body is present")
            object.__setattr__(self, "body_sha256", str(self.body_sha256).strip())
        object.__setattr__(
            self,
            "normalization_version",
            _req_text(self.normalization_version, "normalization_version"),
        )
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {SCHEMA_VERSION!r}, got {self.schema_version!r}"
            )
        if self.language is not None:
            lang = str(self.language).strip()
            object.__setattr__(self, "language", lang if lang else None)
        if self.source_country is not None:
            country = str(self.source_country).strip()
            object.__setattr__(self, "source_country", country if country else None)

    def to_dict(self) -> dict[str, Any]:
        payload = to_json_value(self)
        if not isinstance(payload, dict):
            raise TypeError("ArticleTextRecordV1 must serialize to a dict")
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ArticleTextRecordV1:
        def _dt(key: str) -> Optional[datetime]:
            raw = data.get(key)
            if raw is None or raw == "":
                return None
            if isinstance(raw, datetime):
                return raw
            text = str(raw).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                raise ValueError(f"{key} must be timezone-aware")
            return dt.astimezone(timezone.utc)

        ingested = _dt("ingested_at")
        if ingested is None:
            raise ValueError("ingested_at is required")
        return cls(
            article_id=str(data["article_id"]),
            provider=str(data["provider"]),
            provider_record_id=str(data["provider_record_id"]),
            url=str(data["url"]),
            normalized_url=str(data["normalized_url"]),
            canonical_url=(str(data["canonical_url"]) if data.get("canonical_url") else None),
            domain=str(data["domain"]),
            language=(str(data["language"]) if data.get("language") else None),
            source_country=(str(data["source_country"]) if data.get("source_country") else None),
            title=str(data["title"]),
            description=(str(data["description"]) if data.get("description") is not None else None),
            body=(str(data["body"]) if data.get("body") is not None else None),
            published_at=_dt("published_at"),
            ingested_at=ingested,
            retrieved_at=_dt("retrieved_at"),
            content_status=str(data["content_status"]),
            content_source=str(data.get("content_source", "unknown")),
            title_sha256=str(data["title_sha256"]),
            body_sha256=(str(data["body_sha256"]) if data.get("body_sha256") else None),
            normalization_version=str(data["normalization_version"]),
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
            published_at_source=(
                str(data["published_at_source"]) if data.get("published_at_source") else None
            ),
            published_timezone=(
                str(data["published_timezone"]) if data.get("published_timezone") else None
            ),
        )

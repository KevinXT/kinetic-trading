"""Offline article-corpus interface and local JSONL provider.

Future licensed or provider-supported article-content adapters should implement
``ArticleCorpusProvider``. This phase ships only ``LocalArticleCorpusProvider``,
which never performs network access.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Protocol, runtime_checkable

from kinetic.data.schemas.news import ArticleTextRecordV1
from kinetic.processing.news.normalize import (
    NORMALIZER_VERSION,
    domain_from_url,
    normalize_text_for_hash,
    normalize_url,
    parse_optional_datetime,
    sha256_hex,
    stable_article_id,
)

SAFE_REJECTION_SCHEMA_VERSION = "safe-import-rejection-v1"


@runtime_checkable
class ArticleCorpusProvider(Protocol):
    """Boundary for importing article text without tying research to a scraper."""

    def import_articles(self) -> "LocalCorpusImportResult":
        """Return validated records and explicit rejections."""
        ...


def _classify_reason_code(reason: str) -> str:
    text = reason.casefold()
    if text.startswith("invalid_json"):
        return "INVALID_JSON"
    if "row_must_be_object" in text:
        return "ROW_NOT_OBJECT"
    if "empty title" in text:
        return "EMPTY_TITLE"
    if "missing url" in text:
        return "MISSING_URL"
    if "cannot normalize" in text or "url missing hostname" in text:
        return "INVALID_URL"
    if "must be timezone-aware" in text or "naive" in text:
        return "NAIVE_TIMESTAMP"
    if "unsupported content_status" in text or "unsupported status" in text:
        return "UNSUPPORTED_STATUS"
    if "provenance" in text:
        return "INVALID_PROVENANCE"
    if text.startswith("duplicate_article_id"):
        return "DUPLICATE_ARTICLE_ID"
    return "VALIDATION_ERROR"


def build_safe_import_rejection(
    *,
    row_index: int,
    reason: str,
    raw: Mapping[str, Any],
) -> "SafeImportRejectionV1":
    """Build a versioned rejection summary that never embeds body/description text."""
    body_raw = raw.get("body")
    desc_raw = raw.get("description")
    title_raw = raw.get("title")
    url_raw = raw.get("url")
    title_sha: Optional[str] = None
    if isinstance(title_raw, str) and title_raw.strip():
        title_sha = sha256_hex(normalize_text_for_hash(title_raw))
    url_sha: Optional[str] = None
    if isinstance(url_raw, str) and url_raw.strip():
        try:
            url_sha = sha256_hex(normalize_url(str(url_raw).strip()))
        except (TypeError, ValueError):
            url_sha = sha256_hex(str(url_raw).strip())
    provider = str(raw["provider"]).strip() if raw.get("provider") else None
    provider_record_id = None
    if raw.get("provider_record_id") is not None:
        provider_record_id = str(raw["provider_record_id"]).strip() or None
    elif raw.get("id") is not None:
        provider_record_id = str(raw["id"]).strip() or None
    article_id = str(raw["article_id"]).strip() if raw.get("article_id") else None
    field_names = tuple(sorted(str(k) for k in raw.keys()))
    body_present = body_raw is not None and str(body_raw).strip() != ""
    desc_present = desc_raw is not None and str(desc_raw).strip() != ""
    return SafeImportRejectionV1(
        row_index=row_index,
        reason_code=_classify_reason_code(reason),
        reason=str(reason),
        provider=provider,
        provider_record_id=provider_record_id,
        article_id=article_id,
        url_sha256=url_sha,
        title_sha256=title_sha,
        raw_field_names=field_names,
        body_present=body_present,
        body_length=(len(str(body_raw)) if body_present else None),
        description_present=desc_present,
        description_length=(len(str(desc_raw)) if desc_present else None),
        published_at_raw_present=raw.get("published_at") is not None,
        schema_version=SAFE_REJECTION_SCHEMA_VERSION,
    )


@dataclass(frozen=True)
class SafeImportRejectionV1:
    """Safe, versioned rejection summary for research artifacts.

    Never serializes full body, description, title, URL, or arbitrary raw values.
    """

    row_index: int
    reason_code: str
    reason: str
    provider: Optional[str]
    provider_record_id: Optional[str]
    article_id: Optional[str]
    url_sha256: Optional[str]
    title_sha256: Optional[str]
    raw_field_names: tuple[str, ...]
    body_present: bool
    body_length: Optional[int]
    description_present: bool
    description_length: Optional[int]
    published_at_raw_present: bool
    schema_version: str = SAFE_REJECTION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_index": self.row_index,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "provider": self.provider,
            "provider_record_id": self.provider_record_id,
            "article_id": self.article_id,
            "url_sha256": self.url_sha256,
            "title_sha256": self.title_sha256,
            "raw_field_names": list(self.raw_field_names),
            "body_present": self.body_present,
            "body_length": self.body_length,
            "description_present": self.description_present,
            "description_length": self.description_length,
            "published_at_raw_present": self.published_at_raw_present,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class ImportRejection:
    """In-memory rejection with optional raw diagnostic payload.

    ``to_dict()`` emits only :class:`SafeImportRejectionV1`. The ``raw`` mapping
    is retained for trusted local debugging and must not be written by research
    tasks.
    """

    row_index: int
    reason: str
    raw: Mapping[str, Any]
    safe: SafeImportRejectionV1 = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "safe",
            build_safe_import_rejection(row_index=self.row_index, reason=self.reason, raw=self.raw),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the safe rejection summary only (never full raw input)."""
        return self.safe.to_dict()

    def diagnostic_raw(self) -> Mapping[str, Any]:
        """Trusted local debugging only — never serialize in research artifacts."""
        return self.raw


@dataclass
class LocalCorpusImportResult:
    records: list[ArticleTextRecordV1] = field(default_factory=list)
    rejections: list[ImportRejection] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_summary(self) -> dict[str, Any]:
        return {
            "accepted": len(self.records),
            "rejected": len(self.rejections),
            "provenance": dict(self.provenance),
            "rejection_reason_codes": sorted({r.safe.reason_code for r in self.rejections}),
        }


class LocalArticleCorpusProvider:
    """Read deterministic JSONL fixtures or user-provided local JSONL.

    Validates every input row, preserves provenance, reports rejected rows with
    explicit safe reasons, never invents missing article text, and produces
    deterministic normalized output. Idempotent under fixed inputs.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        default_provider: str = "local_corpus",
        default_content_source: str = "local_corpus",
        ingested_at: Optional[datetime] = None,
        provenance: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.path = Path(path)
        self.default_provider = default_provider
        self.default_content_source = default_content_source
        self.ingested_at = ingested_at
        self.provenance = dict(provenance or {})

    def import_articles(self) -> LocalCorpusImportResult:
        if not self.path.is_file():
            raise FileNotFoundError(f"local article corpus not found: {self.path}")

        ingested_at = self.ingested_at or datetime(1970, 1, 1, tzinfo=timezone.utc)
        if ingested_at.tzinfo is None:
            raise ValueError("ingested_at must be timezone-aware")

        records: list[ArticleTextRecordV1] = []
        rejections: list[ImportRejection] = []
        seen_ids: set[str] = set()

        lines = self.path.read_text(encoding="utf-8").splitlines()
        for row_index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                raw_obj = json.loads(line)
            except json.JSONDecodeError as exc:
                rejections.append(
                    ImportRejection(
                        row_index=row_index,
                        reason=f"invalid_json: {exc}",
                        raw={},
                    )
                )
                continue
            if not isinstance(raw_obj, dict):
                rejections.append(
                    ImportRejection(
                        row_index=row_index,
                        reason="row_must_be_object",
                        raw={"_type": type(raw_obj).__name__},
                    )
                )
                continue
            try:
                record = self._normalize_row(raw_obj, ingested_at=ingested_at)
            except (TypeError, ValueError) as exc:
                rejections.append(
                    ImportRejection(row_index=row_index, reason=str(exc), raw=raw_obj)
                )
                continue
            if record.article_id in seen_ids:
                rejections.append(
                    ImportRejection(
                        row_index=row_index,
                        reason=f"duplicate_article_id:{record.article_id}",
                        raw={
                            "article_id": record.article_id,
                            "provider": record.provider,
                            "provider_record_id": record.provider_record_id,
                        },
                    )
                )
                continue
            seen_ids.add(record.article_id)
            records.append(record)

        # Deterministic order by article_id for idempotent reruns.
        records.sort(key=lambda r: r.article_id)
        provenance = {
            "provider": "LocalArticleCorpusProvider",
            "path": str(self.path),
            "normalization_version": NORMALIZER_VERSION,
            "row_count_raw": len(lines),
            **self.provenance,
        }
        return LocalCorpusImportResult(
            records=records, rejections=rejections, provenance=provenance
        )

    def _normalize_row(
        self, raw: Mapping[str, Any], *, ingested_at: datetime
    ) -> ArticleTextRecordV1:
        title = str(raw.get("title") or "").strip()
        if not title:
            raise ValueError("empty title")

        url = str(raw.get("url") or "").strip()
        if not url:
            raise ValueError("missing url")
        normalized = normalize_url(url)
        canonical_raw = raw.get("canonical_url")
        canonical_url = normalize_url(str(canonical_raw)) if canonical_raw else None

        body_raw = raw.get("body")
        body = None if body_raw is None else str(body_raw)
        if body is not None and not body.strip():
            body = None

        description_raw = raw.get("description")
        description = None if description_raw is None else str(description_raw).strip() or None

        content_status = str(raw.get("content_status") or "").strip()
        if not content_status:
            content_status = "available" if body is not None else "metadata_only"

        title_hash = sha256_hex(normalize_text_for_hash(title))
        body_hash = sha256_hex(normalize_text_for_hash(body)) if body is not None else None

        provider = str(raw.get("provider") or self.default_provider).strip()
        provider_record_id = str(
            raw.get("provider_record_id") or raw.get("id") or normalized
        ).strip()
        article_id = str(raw.get("article_id") or "").strip() or stable_article_id(
            provider=provider,
            provider_record_id=provider_record_id,
            normalized_url=normalized,
            title_sha256=title_hash,
        )

        domain = str(raw.get("domain") or "").strip().lower() or domain_from_url(normalized)
        published_at = parse_optional_datetime(raw.get("published_at"), "published_at")
        row_ingested = parse_optional_datetime(raw.get("ingested_at"), "ingested_at") or ingested_at
        retrieved_at = parse_optional_datetime(raw.get("retrieved_at"), "retrieved_at")

        content_source = str(raw.get("content_source") or self.default_content_source).strip()

        return ArticleTextRecordV1(
            article_id=article_id,
            provider=provider,
            provider_record_id=provider_record_id,
            url=url,
            normalized_url=normalized,
            canonical_url=canonical_url,
            domain=domain,
            language=(str(raw["language"]).strip() if raw.get("language") else None),
            source_country=(
                str(raw["source_country"]).strip() if raw.get("source_country") else None
            ),
            title=title,
            description=description,
            body=body,
            published_at=published_at,
            ingested_at=row_ingested,
            retrieved_at=retrieved_at,
            content_status=content_status,
            content_source=content_source,
            title_sha256=title_hash,
            body_sha256=body_hash,
            normalization_version=NORMALIZER_VERSION,
            published_at_source=(
                str(raw["published_at_source"]) if raw.get("published_at_source") else None
            ),
            published_timezone=(
                str(raw["published_timezone"]) if raw.get("published_timezone") else None
            ),
        )


def records_from_iterable(
    rows: Iterable[Mapping[str, Any]],
    *,
    ingested_at: datetime,
) -> LocalCorpusImportResult:
    """Normalize an in-memory iterable of raw dicts (tests / fixtures)."""
    provider = LocalArticleCorpusProvider(
        path=Path("."),
        ingested_at=ingested_at,
    )
    records: list[ArticleTextRecordV1] = []
    rejections: list[ImportRejection] = []
    seen: set[str] = set()
    for row_index, raw in enumerate(rows):
        try:
            record = provider._normalize_row(raw, ingested_at=ingested_at)
        except (TypeError, ValueError) as exc:
            rejections.append(ImportRejection(row_index=row_index, reason=str(exc), raw=raw))
            continue
        if record.article_id in seen:
            rejections.append(
                ImportRejection(
                    row_index=row_index,
                    reason=f"duplicate_article_id:{record.article_id}",
                    raw={
                        "article_id": record.article_id,
                        "provider": record.provider,
                        "provider_record_id": record.provider_record_id,
                    },
                )
            )
            continue
        seen.add(record.article_id)
        records.append(record)
    records.sort(key=lambda r: r.article_id)
    return LocalCorpusImportResult(records=records, rejections=rejections)

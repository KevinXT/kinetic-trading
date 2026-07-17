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

from news_data.article.models import ArticleTextRecordV1
from news_data.article.normalize import (
    NORMALIZER_VERSION,
    domain_from_url,
    normalize_text_for_hash,
    normalize_url,
    parse_optional_datetime,
    sha256_hex,
    stable_article_id,
)


@runtime_checkable
class ArticleCorpusProvider(Protocol):
    """Boundary for importing article text without tying research to a scraper."""

    def import_articles(self) -> "LocalCorpusImportResult":
        """Return validated records and explicit rejections."""
        ...


@dataclass(frozen=True)
class ImportRejection:
    row_index: int
    reason: str
    raw: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_index": self.row_index,
            "reason": self.reason,
            "raw": dict(self.raw),
        }


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
        }


class LocalArticleCorpusProvider:
    """Read deterministic JSONL fixtures or user-provided local JSONL.

    Validates every input row, preserves provenance, reports rejected rows with
    explicit reasons, never invents missing article text, and produces
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
                    ImportRejection(row_index=row_index, reason=f"invalid_json: {exc}", raw={})
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
                        raw=raw_obj,
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
                    raw=raw,
                )
            )
            continue
        seen.add(record.article_id)
        records.append(record)
    records.sort(key=lambda r: r.article_id)
    return LocalCorpusImportResult(records=records, rejections=rejections)

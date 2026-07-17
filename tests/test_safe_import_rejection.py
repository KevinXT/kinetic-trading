"""Content-safety tests: rejected rows must not leak article bodies."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from news_data.article.corpus import (
    SAFE_REJECTION_SCHEMA_VERSION,
    LocalArticleCorpusProvider,
    SafeImportRejectionV1,
    build_safe_import_rejection,
    records_from_iterable,
)

FIXED = datetime(2026, 7, 17, 15, 0, 0, tzinfo=timezone.utc)
SENTINEL = "UNIQUE_REJECTED_BODY_SENTINEL_9f3c2a1b"


def _base_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "title": "Valid title for accepted row",
        "url": "https://example-tech.test/accepted/1",
        "body": "Accepted body text without sentinel.",
        "provider": "synthetic",
        "provider_record_id": "ok-1",
        "published_at": "2026-01-15T12:00:00Z",
        "content_status": "available",
        "content_source": "synthetic_fixture",
        "domain": "example-tech.test",
        "language": "en",
    }
    row.update(overrides)
    return row


def test_safe_rejection_excludes_body_and_is_deterministic() -> None:
    raw = {
        "title": "",
        "url": "https://example-tech.test/bad",
        "body": SENTINEL,
        "description": "secret description should not serialize",
        "provider": "synthetic",
        "provider_record_id": "rej-1",
    }
    safe = build_safe_import_rejection(row_index=3, reason="empty title", raw=raw)
    payload = safe.to_dict()
    blob = json.dumps(payload, sort_keys=True)
    assert SENTINEL not in blob
    assert "secret description" not in blob
    assert payload["reason_code"] == "EMPTY_TITLE"
    assert payload["body_present"] is True
    assert payload["body_length"] == len(SENTINEL)
    assert payload["schema_version"] == SAFE_REJECTION_SCHEMA_VERSION
    assert payload["raw_field_names"] == sorted(raw.keys())
    # Deterministic under fixed input.
    again = build_safe_import_rejection(row_index=3, reason="empty title", raw=raw)
    assert again.to_dict() == payload


def test_rejected_sentinel_absent_from_import_artifacts(tmp_path: Path) -> None:
    path = tmp_path / "articles.jsonl"
    rows = [
        _base_row(),
        _base_row(
            title="",
            url="https://example-tech.test/reject/empty-title",
            body=SENTINEL,
            provider_record_id="rej-empty-title",
        ),
        _base_row(
            title="Bad URL row",
            url="not-a-url",
            body=SENTINEL,
            provider_record_id="rej-bad-url",
        ),
        _base_row(
            title="Naive timestamp row",
            url="https://example-tech.test/reject/naive",
            body=SENTINEL,
            published_at="2026-01-15T12:00:00",
            provider_record_id="rej-naive",
        ),
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    imported = LocalArticleCorpusProvider(
        path=path,
        default_content_source="synthetic_fixture",
        ingested_at=FIXED,
    ).import_articles()
    assert len(imported.records) == 1
    assert len(imported.rejections) >= 2
    serialized = [json.dumps(r.to_dict(), sort_keys=True) for r in imported.rejections]
    joined = "\n".join(serialized)
    assert SENTINEL not in joined
    codes = {r.safe.reason_code for r in imported.rejections}
    assert "EMPTY_TITLE" in codes
    assert "INVALID_URL" in codes or "VALIDATION_ERROR" in codes
    # Diagnostic raw remains in-memory only.
    assert any(SENTINEL in str(r.diagnostic_raw().get("body", "")) for r in imported.rejections)
    summary = json.dumps(imported.to_summary(), sort_keys=True)
    assert SENTINEL not in summary


def test_rejection_reason_codes_cover_common_failures() -> None:
    cases = [
        ("empty title", "EMPTY_TITLE"),
        ("missing url", "MISSING_URL"),
        ("cannot normalize non-http(s) URL: 'ftp://x'", "INVALID_URL"),
        ("published_at must be timezone-aware", "NAIVE_TIMESTAMP"),
        ("duplicate_article_id:abc", "DUPLICATE_ARTICLE_ID"),
        ("invalid provenance token", "INVALID_PROVENANCE"),
    ]
    for reason, code in cases:
        safe = build_safe_import_rejection(row_index=0, reason=reason, raw={"title": "t"})
        assert safe.reason_code == code


def test_reversed_input_changes_row_index_only_as_provenance() -> None:
    a = _base_row(title="", body=SENTINEL, provider_record_id="r1")
    b = _base_row(
        title="ok",
        url="https://example-tech.test/ok2",
        provider_record_id="ok2",
        body="accepted",
    )
    forward = records_from_iterable([a, b], ingested_at=FIXED)
    reverse = records_from_iterable([b, a], ingested_at=FIXED)
    assert len(forward.records) == len(reverse.records) == 1
    assert forward.rejections[0].row_index == 0
    assert reverse.rejections[0].row_index == 1
    # Scientific accepted records match; row_index is provenance of source order.
    assert [r.article_id for r in forward.records] == [r.article_id for r in reverse.records]


def test_safe_import_rejection_type_roundtrip() -> None:
    safe = SafeImportRejectionV1(
        row_index=1,
        reason_code="EMPTY_TITLE",
        reason="empty title",
        provider="synthetic",
        provider_record_id="x",
        article_id=None,
        url_sha256="abc",
        title_sha256=None,
        raw_field_names=("body", "title", "url"),
        body_present=True,
        body_length=10,
        description_present=False,
        description_length=None,
        published_at_raw_present=False,
    )
    assert safe.to_dict()["schema_version"] == SAFE_REJECTION_SCHEMA_VERSION

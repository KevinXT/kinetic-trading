"""Article normalization and local corpus import tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from kinetic.data.schemas.news import ArticleTextRecordV1
from kinetic.data.serialization import stable_json_dumps
from kinetic.ingestion.news.local_corpus import LocalArticleCorpusProvider
from kinetic.processing.news.normalize import (
    NORMALIZER_VERSION,
    normalize_text_for_hash,
    normalize_url,
    sha256_hex,
    stable_article_id,
)


def test_url_normalization_tracking_and_meaningful_params() -> None:
    a = normalize_url("https://News.Example.com/path?utm_source=x&docid=1&b=2&a=1")
    b = normalize_url("https://news.example.com/path?a=1&b=2&docid=1")
    assert a == b
    assert "utm_source" not in a
    # Meaningful docid retained — different docs stay different.
    c = normalize_url("https://news.example.com/path?docid=2&a=1&b=2")
    assert a != c


def test_url_default_port_and_fragment() -> None:
    assert normalize_url("https://example.com:443/a#frag") == "https://example.com/a"
    assert normalize_url("http://example.com:80/a") == "http://example.com/a"


def test_unicode_and_hash_stability() -> None:
    t1 = normalize_text_for_hash("  Café\u00a0NVIDIA  ")
    t2 = normalize_text_for_hash("café nvidia")
    assert t1 == t2
    assert sha256_hex(t1) == sha256_hex(t2)


def test_stable_article_id_and_serialization() -> None:
    aid = stable_article_id(
        provider="fixture",
        provider_record_id="x",
        normalized_url="https://example.com/a",
        title_sha256="abc",
    )
    assert aid.startswith("art_")
    rec = ArticleTextRecordV1(
        article_id=aid,
        provider="fixture",
        provider_record_id="x",
        url="https://example.com/a",
        normalized_url="https://example.com/a",
        canonical_url=None,
        domain="example.com",
        language="English",
        source_country=None,
        title="Hello",
        description=None,
        body=None,
        published_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        ingested_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
        retrieved_at=None,
        content_status="metadata_only",
        content_source="synthetic_fixture",
        title_sha256=sha256_hex(normalize_text_for_hash("Hello")),
        body_sha256=None,
        normalization_version=NORMALIZER_VERSION,
    )
    assert stable_json_dumps(rec.to_dict()) == stable_json_dumps(rec.to_dict())


def test_rejects_naive_datetime_and_body_hash_without_body() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ArticleTextRecordV1(
            article_id="art_x",
            provider="fixture",
            provider_record_id="x",
            url="https://example.com/a",
            normalized_url="https://example.com/a",
            canonical_url=None,
            domain="example.com",
            language=None,
            source_country=None,
            title="Hello",
            description=None,
            body=None,
            published_at=datetime(2025, 1, 1),
            ingested_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            retrieved_at=None,
            content_status="metadata_only",
            content_source="synthetic_fixture",
            title_sha256="a" * 64,
            body_sha256=None,
            normalization_version=NORMALIZER_VERSION,
        )
    with pytest.raises(ValueError, match="body_sha256"):
        ArticleTextRecordV1(
            article_id="art_y",
            provider="fixture",
            provider_record_id="y",
            url="https://example.com/b",
            normalized_url="https://example.com/b",
            canonical_url=None,
            domain="example.com",
            language=None,
            source_country=None,
            title="Hello",
            description=None,
            body=None,
            published_at=None,
            ingested_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            retrieved_at=None,
            content_status="metadata_only",
            content_source="synthetic_fixture",
            title_sha256="a" * 64,
            body_sha256="b" * 64,
            normalization_version=NORMALIZER_VERSION,
        )


def test_local_corpus_rejects_invalid_and_is_deterministic(tmp_path: Path) -> None:
    fixture = Path("tests/fixtures/research/relevance_benchmark/articles.jsonl")
    clock = datetime(2026, 7, 17, tzinfo=timezone.utc)
    a = LocalArticleCorpusProvider(fixture, ingested_at=clock).import_articles()
    b = LocalArticleCorpusProvider(fixture, ingested_at=clock).import_articles()
    assert len(a.rejections) >= 3
    assert [r.article_id for r in a.records] == [r.article_id for r in b.records]
    assert [r.to_dict() for r in a.records] == [r.to_dict() for r in b.records]
    assert all(r.body_sha256 is None for r in a.records if r.body is None)

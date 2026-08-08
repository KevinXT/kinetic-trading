"""Token-safe entity matching tests."""

from __future__ import annotations

from datetime import datetime, timezone

from kinetic.data.catalog.entities import (
    SEMICONDUCTOR_FIXTURE_VERSION,
    default_semiconductor_entities,
)
from kinetic.data.schemas.news import ArticleTextRecordV1
from kinetic.processing.news.entity_linking import MATCHING_RULE_VERSION, match_entities
from kinetic.processing.news.normalize import (
    NORMALIZER_VERSION,
    normalize_text_for_hash,
    sha256_hex,
)


def _art(
    title: str, body: str | None = None, description: str | None = None
) -> ArticleTextRecordV1:
    return ArticleTextRecordV1(
        article_id="art_test",
        provider="fixture",
        provider_record_id="t",
        url="https://example.com/t",
        normalized_url="https://example.com/t",
        canonical_url=None,
        domain="example.com",
        language="English",
        source_country=None,
        title=title,
        description=description,
        body=body,
        published_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        ingested_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
        retrieved_at=None,
        content_status="available" if body else "metadata_only",
        content_source="synthetic_fixture",
        title_sha256=sha256_hex(normalize_text_for_hash(title)),
        body_sha256=(sha256_hex(normalize_text_for_hash(body)) if body else None),
        normalization_version=NORMALIZER_VERSION,
    )


def test_intel_not_intelligence_and_real_intel() -> None:
    ents = default_semiconductor_entities()
    assert all(e.reference_version == SEMICONDUCTOR_FIXTURE_VERSION for e in ents)
    fp = match_entities(_art("Central intelligence agency briefing"), ents)
    assert all(m.entity_id != "ent_intel" for m in fp)
    hit = match_entities(_art("Intel Corporation expands foundry"), ents)
    assert any(m.entity_id == "ent_intel" and m.match_field == "title" for m in hit)
    assert hit[0].matching_rule_version == MATCHING_RULE_VERSION


def test_amd_not_inside_hamden_or_camden() -> None:
    ents = default_semiconductor_entities()
    fp = match_entities(_art("Hamden council near Camden avenue"), ents)
    assert all(m.entity_id != "ent_amd" for m in fp)
    hit = match_entities(_art("AMD launches chips", "Advanced Micro Devices said"), ents)
    assert {m.entity_id for m in hit} == {"ent_amd"}


def test_micron_not_omicron() -> None:
    ents = default_semiconductor_entities()
    fp = match_entities(_art("Health officials monitor omicron subvariant"), ents)
    assert all(m.entity_id != "ent_micron" for m in fp)


def test_multiple_entities_spans_and_ambiguity() -> None:
    ents = default_semiconductor_entities()
    art = _art(
        "AI server boom lifts NVIDIA and Broadcom",
        "NVIDIA GPUs and Broadcom networking silicon are central.",
    )
    matches = match_entities(art, ents)
    assert {"ent_nvidia", "ent_broadcom"} <= {m.entity_id for m in matches}
    for m in matches:
        assert m.end > m.start
        assert m.match_field in {"title", "description", "body"}
    # Bare amd is ambiguous.
    amb = match_entities(_art("Local blog mentions amd once"), ents)
    assert any(m.ambiguity_status == "ambiguous_alias" for m in amb)

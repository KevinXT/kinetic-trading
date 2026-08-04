"""Blind UI view models — never include prohibited leakage fields."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

PROHIBITED_BLIND_FIELDS = frozenset(
    {
        "baseline",
        "baseline_prediction",
        "expected_label",
        "challenge_reason",
        "challenge_reason_codes",
        "sample_role",
        "sample_roles",
        "inclusion_probability",
        "design_weight",
        "theme_score",
        "similarity_score",
        "title_similarity",
        "body_similarity",
        "candidate_at_current_threshold",
        "other_annotator",
        "adjudicated_label",
        "market_return",
        "abnormal_return",
    }
)

EVIDENCE_NO_EXACT_SPAN_REASONS = frozenset(
    {
        "PARAPHRASED_EVIDENCE",
        "NO_SINGLE_EXACT_SPAN",
        "METADATA_ONLY",
        "OTHER",
    }
)

UNRESOLVED_ENTITY_ID = "UNRESOLVED_ENTITY"


@dataclass(frozen=True)
class EntityOption:
    """UI display option that stores a canonical entity ID."""

    entity_id: str
    display_name: str
    ticker: Optional[str]
    entity_type: str

    def label(self) -> str:
        if self.ticker:
            return f"{self.display_name} ({self.ticker})"
        return self.display_name


@dataclass(frozen=True)
class EvidenceCandidate:
    """Immutable evidence match candidate generated from bound article text."""

    candidate_id: str
    field: str
    start: int
    end: int
    exact_text: str
    occurrence_index: int
    article_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "field": self.field,
            "start": self.start,
            "end": self.end,
            "exact_text": self.exact_text,
            "occurrence_index": self.occurrence_index,
            "article_hash": self.article_hash,
        }


@dataclass(frozen=True)
class BlindArticleView:
    assignment_id: str
    article_id: str
    duplicate_cluster_id: str
    title: str
    domain: str
    published_at: Optional[str]
    published_at_precision: str
    language: Optional[str]
    description: str
    body: str
    content_status: str
    body_truncated: bool
    entity_options: tuple[EntityOption, ...]
    guideline_version: str
    annotator_id: str
    queue_index: int
    queue_total: int
    saved_revision: int
    last_saved_at: Optional[str]
    article_title_sha256: str
    article_body_sha256: Optional[str]
    source_run_id: str
    source_corpus_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignment_id": self.assignment_id,
            "article_id": self.article_id,
            "duplicate_cluster_id": self.duplicate_cluster_id,
            "title": self.title,
            "domain": self.domain,
            "published_at": self.published_at,
            "published_at_precision": self.published_at_precision,
            "language": self.language,
            "description": self.description,
            "body": self.body,
            "content_status": self.content_status,
            "body_truncated": self.body_truncated,
            "entity_options": [
                {
                    "entity_id": e.entity_id,
                    "display_name": e.display_name,
                    "ticker": e.ticker,
                    "entity_type": e.entity_type,
                }
                for e in self.entity_options
            ],
            "guideline_version": self.guideline_version,
            "annotator_id": self.annotator_id,
            "queue_index": self.queue_index,
            "queue_total": self.queue_total,
            "saved_revision": self.saved_revision,
            "last_saved_at": self.last_saved_at,
            "article_title_sha256": self.article_title_sha256,
            "article_body_sha256": self.article_body_sha256,
            "source_run_id": self.source_run_id,
            "source_corpus_sha256": self.source_corpus_sha256,
        }

    @property
    def entity_reference_names(self) -> tuple[str, ...]:
        """Backward-compatible display names (not stored IDs)."""
        return tuple(e.display_name for e in self.entity_options)


@dataclass(frozen=True)
class BlindDuplicatePairView:
    assignment_id: str
    pair_id: str
    article_a: Mapping[str, str]
    article_b: Mapping[str, str]
    reviewer_id: str
    queue_index: int
    queue_total: int
    source_run_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignment_id": self.assignment_id,
            "pair_id": self.pair_id,
            "article_a": dict(self.article_a),
            "article_b": dict(self.article_b),
            "reviewer_id": self.reviewer_id,
            "queue_index": self.queue_index,
            "queue_total": self.queue_total,
            "source_run_id": self.source_run_id,
        }


def assert_no_prohibited_fields(payload: Mapping[str, Any]) -> None:
    forbidden = {
        str(k)
        for k in payload
        if str(k).casefold() in {p.casefold() for p in PROHIBITED_BLIND_FIELDS}
    }
    if forbidden:
        raise ValueError(f"blind view contains prohibited fields: {sorted(forbidden)}")
    # Nested maps (duplicate pair sides) must also stay blind.
    for value in payload.values():
        if isinstance(value, Mapping):
            nested = {
                str(k)
                for k in value
                if str(k).casefold() in {p.casefold() for p in PROHIBITED_BLIND_FIELDS}
            }
            if nested:
                raise ValueError(f"blind view nested prohibited fields: {sorted(nested)}")


def article_content_hash(
    *,
    title: str,
    description: str,
    body: str,
    title_sha256: str,
    body_sha256: Optional[str],
) -> str:
    """Stable hash binding displayed text to assignment provenance hashes."""
    raw = f"{title_sha256}|{body_sha256 or ''}|{len(title)}|{len(description)}|{len(body)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_evidence_candidates(
    *,
    excerpt: str,
    title: str,
    description: str,
    body: str,
    article_hash: str,
) -> list[EvidenceCandidate]:
    """Generate immutable candidates for every exact occurrence of excerpt."""
    if not excerpt:
        return []
    fields = (
        ("title", title or ""),
        ("description", description or ""),
        ("body", body or ""),
    )
    out: list[EvidenceCandidate] = []
    occurrence = 0
    for field_name, text in fields:
        start_at = 0
        while True:
            idx = text.find(excerpt, start_at)
            if idx < 0:
                break
            end = idx + len(excerpt)
            candidate_id = hashlib.sha256(
                f"{article_hash}|{field_name}|{idx}|{end}|{occurrence}|{excerpt}".encode("utf-8")
            ).hexdigest()[:24]
            out.append(
                EvidenceCandidate(
                    candidate_id=candidate_id,
                    field=field_name,
                    start=idx,
                    end=end,
                    exact_text=excerpt,
                    occurrence_index=occurrence,
                    article_hash=article_hash,
                )
            )
            occurrence += 1
            start_at = idx + 1
    return out


def find_evidence_offsets(
    *,
    excerpt: str,
    title: str,
    description: str,
    body: str,
    article_hash: str = "",
) -> tuple[Optional[int], Optional[int], Optional[str], list[str]]:
    """Compatibility wrapper returning (start, end, field, candidate_id list).

    Offsets are field-local (not concatenated blob offsets). Callers must select
    a generated ``candidate_id`` when multiple matches exist.
    """
    candidates = generate_evidence_candidates(
        excerpt=excerpt,
        title=title,
        description=description,
        body=body,
        article_hash=article_hash or "unbound",
    )
    if not candidates:
        return None, None, None, []
    if len(candidates) == 1:
        c = candidates[0]
        return c.start, c.end, c.field, [c.candidate_id]
    return None, None, None, [c.candidate_id for c in candidates]


def resolve_evidence_candidate(
    *,
    excerpt: str,
    title: str,
    description: str,
    body: str,
    article_hash: str,
    chosen_candidate_id: Optional[str],
    no_exact_span_reason: Optional[str] = None,
) -> tuple[Optional[str], Optional[int], Optional[int], Optional[str], Optional[str]]:
    """Return (evidence_text, start, end, field, candidate_id) after verification.

    Fabricated field@offset values are rejected. Paraphrased evidence requires an
    explicit typed reason and stores no invented offsets.
    """
    if not excerpt or not excerpt.strip():
        return None, None, None, None, None
    candidates = generate_evidence_candidates(
        excerpt=excerpt,
        title=title,
        description=description,
        body=body,
        article_hash=article_hash,
    )
    if not candidates:
        if no_exact_span_reason not in EVIDENCE_NO_EXACT_SPAN_REASONS:
            raise ValueError(
                "evidence excerpt not found in article text; provide a typed "
                f"no-exact-span reason in {sorted(EVIDENCE_NO_EXACT_SPAN_REASONS)}"
            )
        return excerpt, None, None, None, None
    if len(candidates) == 1:
        chosen = candidates[0]
    else:
        if not chosen_candidate_id:
            raise ValueError(
                "evidence excerpt occurs multiple times; select a generated candidate_id: "
                + ", ".join(c.candidate_id for c in candidates)
            )
        by_id = {c.candidate_id: c for c in candidates}
        if chosen_candidate_id not in by_id:
            raise ValueError("fabricated or unknown evidence candidate_id rejected")
        chosen = by_id[chosen_candidate_id]
    if chosen.article_hash != article_hash:
        raise ValueError("evidence candidate article_hash mismatch")
    field_text = {"title": title or "", "description": description or "", "body": body or ""}[
        chosen.field
    ]
    if field_text[chosen.start : chosen.end] != chosen.exact_text:
        raise ValueError("evidence offsets do not match exact text in bound article")
    if chosen.exact_text != excerpt:
        raise ValueError("evidence excerpt does not match selected candidate exact_text")
    return chosen.exact_text, chosen.start, chosen.end, chosen.field, chosen.candidate_id


def validate_entity_id_sets(
    *,
    central_entity_ids: Sequence[str],
    secondary_entity_ids: Sequence[str],
    allowed_entity_ids: Sequence[str],
    allow_unresolved: bool = True,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    allowed = set(allowed_entity_ids)
    if allow_unresolved:
        allowed.add(UNRESOLVED_ENTITY_ID)
    central = list(central_entity_ids)
    secondary = list(secondary_entity_ids)
    if len(central) != len(set(central)):
        raise ValueError("duplicate central entity IDs are not allowed")
    if len(secondary) != len(set(secondary)):
        raise ValueError("duplicate secondary entity IDs are not allowed")
    for eid in central + secondary:
        if eid not in allowed:
            raise ValueError(f"unknown entity_id {eid!r}; store canonical IDs, not display names")
    overlap = set(central) & set(secondary)
    if overlap:
        raise ValueError(f"central and secondary entity sets overlap: {sorted(overlap)}")
    return tuple(sorted(central)), tuple(sorted(secondary))

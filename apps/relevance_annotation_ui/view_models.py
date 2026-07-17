"""Blind UI view models — never include prohibited leakage fields."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

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
    entity_reference_names: tuple[str, ...]
    guideline_version: str
    annotator_id: str
    queue_index: int
    queue_total: int
    saved_revision: int
    last_saved_at: Optional[str]

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
            "entity_reference_names": list(self.entity_reference_names),
            "guideline_version": self.guideline_version,
            "annotator_id": self.annotator_id,
            "queue_index": self.queue_index,
            "queue_total": self.queue_total,
            "saved_revision": self.saved_revision,
            "last_saved_at": self.last_saved_at,
        }


@dataclass(frozen=True)
class BlindDuplicatePairView:
    assignment_id: str
    pair_id: str
    article_a: Mapping[str, str]
    article_b: Mapping[str, str]
    reviewer_id: str
    queue_index: int
    queue_total: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignment_id": self.assignment_id,
            "pair_id": self.pair_id,
            "article_a": dict(self.article_a),
            "article_b": dict(self.article_b),
            "reviewer_id": self.reviewer_id,
            "queue_index": self.queue_index,
            "queue_total": self.queue_total,
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


def find_evidence_offsets(
    *,
    excerpt: str,
    title: str,
    description: str,
    body: str,
) -> tuple[Optional[int], Optional[int], Optional[str], list[str]]:
    """Return (start, end, field, candidates) for an exact excerpt match.

    Concatenation scheme matches annotation guidelines: title + blank + description + blank + body.
    """
    if not excerpt:
        return None, None, None, []
    parts = [
        ("title", title or ""),
        ("description", description or ""),
        ("body", body or ""),
    ]
    blob_parts: list[str] = []
    field_ranges: list[tuple[str, int, int]] = []
    cursor = 0
    for i, (name, text) in enumerate(parts):
        if i > 0:
            blob_parts.append("\n\n")
            cursor += 2
        start = cursor
        blob_parts.append(text)
        cursor += len(text)
        field_ranges.append((name, start, cursor))
    blob = "".join(blob_parts)
    matches: list[tuple[int, int, str]] = []
    start_at = 0
    while True:
        idx = blob.find(excerpt, start_at)
        if idx < 0:
            break
        end = idx + len(excerpt)
        field = "body"
        for name, f0, f1 in field_ranges:
            if idx >= f0 and end <= f1:
                field = name
                break
        matches.append((idx, end, field))
        start_at = idx + 1
    if not matches:
        return None, None, None, []
    if len(matches) == 1:
        s, e, field = matches[0]
        return s, e, field, [field]
    return None, None, None, [f"{m[2]}@{m[0]}" for m in matches]

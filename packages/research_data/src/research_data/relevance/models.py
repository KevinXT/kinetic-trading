"""Annotation and benchmark candidate schemas for semiconductor relevance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from market_data.domain.serialization import to_json_value

ANNOTATION_SCHEMA_VERSION = "semiconductor-relevance-annotation-v1"
BINARY_RELEVANT_THRESHOLD = 2
RELEVANCE_LABELS = frozenset({0, 1, 2, 3})
RELEVANCE_LABEL_NAMES = {
    0: "unrelated",
    1: "incidental",
    2: "meaningful_secondary",
    3: "primary_topic",
}


def binary_relevant(relevance_label: int) -> bool:
    if relevance_label not in RELEVANCE_LABELS:
        raise ValueError(f"relevance_label must be in {sorted(RELEVANCE_LABELS)}")
    return relevance_label >= BINARY_RELEVANT_THRESHOLD


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


@dataclass(frozen=True)
class SemiconductorRelevanceAnnotationV1:
    article_id: str
    duplicate_cluster_id: str
    annotator_id: str
    guideline_version: str
    relevance_label: int
    central_entity_ids: tuple[str, ...]
    evidence_text: Optional[str]
    evidence_start: Optional[int]
    evidence_end: Optional[int]
    uncertain: bool
    uncertainty_reason: Optional[str]
    annotator_notes: Optional[str]
    annotated_at: datetime
    annotation_version: str
    schema_version: str = ANNOTATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "article_id", _req_text(self.article_id, "article_id"))
        object.__setattr__(
            self,
            "duplicate_cluster_id",
            _req_text(self.duplicate_cluster_id, "duplicate_cluster_id"),
        )
        object.__setattr__(self, "annotator_id", _req_text(self.annotator_id, "annotator_id"))
        object.__setattr__(
            self, "guideline_version", _req_text(self.guideline_version, "guideline_version")
        )
        if self.relevance_label not in RELEVANCE_LABELS:
            raise ValueError(
                f"relevance_label must be in {sorted(RELEVANCE_LABELS)}, "
                f"got {self.relevance_label!r}"
            )
        ids = tuple(str(x).strip() for x in self.central_entity_ids if str(x).strip())
        object.__setattr__(self, "central_entity_ids", ids)
        if self.evidence_text is not None:
            object.__setattr__(self, "evidence_text", str(self.evidence_text))
        if (self.evidence_start is None) ^ (self.evidence_end is None):
            raise ValueError("evidence_start and evidence_end must both be set or both null")
        if self.evidence_start is not None and self.evidence_end is not None:
            if self.evidence_start < 0 or self.evidence_end < self.evidence_start:
                raise ValueError("invalid evidence span")
            if self.evidence_text is not None:
                span_len = self.evidence_end - self.evidence_start
                if span_len != len(self.evidence_text):
                    # Allow evidence_text to be the selected substring without
                    # forcing offsets into a particular concatenation scheme when
                    # lengths disagree — require consistency when both present.
                    raise ValueError(
                        "evidence_text length must equal evidence_end - evidence_start"
                    )
        if self.uncertain and not (
            self.uncertainty_reason and str(self.uncertainty_reason).strip()
        ):
            raise ValueError("uncertainty_reason is required when uncertain is true")
        if not self.uncertain:
            object.__setattr__(self, "uncertainty_reason", None)
        object.__setattr__(self, "annotated_at", _utc(self.annotated_at, "annotated_at"))
        object.__setattr__(
            self, "annotation_version", _req_text(self.annotation_version, "annotation_version")
        )
        if self.schema_version != ANNOTATION_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {ANNOTATION_SCHEMA_VERSION!r}, "
                f"got {self.schema_version!r}"
            )

    @property
    def binary_relevant(self) -> bool:
        return binary_relevant(self.relevance_label)

    def to_dict(self) -> dict[str, Any]:
        payload = to_json_value(self)
        assert isinstance(payload, dict)
        payload["binary_relevant"] = self.binary_relevant
        payload["relevance_label_name"] = RELEVANCE_LABEL_NAMES[self.relevance_label]
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SemiconductorRelevanceAnnotationV1:
        raw_at = data["annotated_at"]
        if isinstance(raw_at, datetime):
            annotated_at = raw_at
        else:
            text = str(raw_at).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            annotated_at = datetime.fromisoformat(text)
        central = data.get("central_entity_ids") or ()
        if isinstance(central, str):
            central = [x for x in central.split("|") if x.strip()]
        return cls(
            article_id=str(data["article_id"]),
            duplicate_cluster_id=str(data["duplicate_cluster_id"]),
            annotator_id=str(data["annotator_id"]),
            guideline_version=str(data["guideline_version"]),
            relevance_label=int(data["relevance_label"]),
            central_entity_ids=tuple(central),
            evidence_text=(str(data["evidence_text"]) if data.get("evidence_text") else None),
            evidence_start=(
                int(data["evidence_start"]) if data.get("evidence_start") is not None else None
            ),
            evidence_end=(
                int(data["evidence_end"]) if data.get("evidence_end") is not None else None
            ),
            uncertain=bool(data.get("uncertain", False)),
            uncertainty_reason=(
                str(data["uncertainty_reason"]) if data.get("uncertainty_reason") else None
            ),
            annotator_notes=(str(data["annotator_notes"]) if data.get("annotator_notes") else None),
            annotated_at=annotated_at,
            annotation_version=str(data.get("annotation_version", "annotation-v1")),
            schema_version=str(data.get("schema_version", ANNOTATION_SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class AdjudicatedAnnotationV1:
    """Final label with provenance; never overwrites raw annotations."""

    article_id: str
    duplicate_cluster_id: str
    relevance_label: int
    central_entity_ids: tuple[str, ...]
    adjudicator_id: str
    adjudication_reason: str
    guideline_version: str
    annotation_version: str
    raw_annotator_ids: tuple[str, ...]
    raw_labels: tuple[int, ...]
    adjudicated_at: datetime
    schema_version: str = "adjudicated-annotation-v1"

    def __post_init__(self) -> None:
        if self.relevance_label not in RELEVANCE_LABELS:
            raise ValueError("invalid adjudicated relevance_label")
        if not str(self.adjudicator_id).strip():
            raise ValueError("adjudicator_id required")
        if not str(self.adjudication_reason).strip():
            raise ValueError("adjudication_reason required")
        if len(self.raw_annotator_ids) != len(self.raw_labels):
            raise ValueError("raw_annotator_ids and raw_labels length mismatch")
        if len(self.raw_labels) >= 2:
            distance = abs(self.raw_labels[0] - self.raw_labels[1])
            if distance >= 2 and len(str(self.adjudication_reason).strip()) < 8:
                raise ValueError(
                    "large ordinal disagreements (distance >= 2) require a substantive reason"
                )
        object.__setattr__(self, "adjudicated_at", _utc(self.adjudicated_at, "adjudicated_at"))

    @property
    def binary_relevant(self) -> bool:
        return binary_relevant(self.relevance_label)

    def to_dict(self) -> dict[str, Any]:
        payload = to_json_value(self)
        assert isinstance(payload, dict)
        payload["binary_relevant"] = self.binary_relevant
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AdjudicatedAnnotationV1:
        raw_at = data["adjudicated_at"]
        if isinstance(raw_at, datetime):
            adjudicated_at = raw_at
        else:
            text = str(raw_at).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            adjudicated_at = datetime.fromisoformat(text)
        return cls(
            article_id=str(data["article_id"]),
            duplicate_cluster_id=str(data["duplicate_cluster_id"]),
            relevance_label=int(data["relevance_label"]),
            central_entity_ids=tuple(data.get("central_entity_ids") or ()),
            adjudicator_id=str(data["adjudicator_id"]),
            adjudication_reason=str(data["adjudication_reason"]),
            guideline_version=str(data["guideline_version"]),
            annotation_version=str(data.get("annotation_version", "annotation-v1")),
            raw_annotator_ids=tuple(data.get("raw_annotator_ids") or ()),
            raw_labels=tuple(int(x) for x in (data.get("raw_labels") or ())),
            adjudicated_at=adjudicated_at,
            schema_version=str(data.get("schema_version", "adjudicated-annotation-v1")),
        )

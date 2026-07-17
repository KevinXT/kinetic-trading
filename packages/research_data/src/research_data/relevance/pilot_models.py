"""Pilot-specific schemas: annotations, duplicate-pair review, states, frame units."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from market_data.domain.serialization import to_json_value

from research_data.relevance.models import (
    RELEVANCE_LABEL_NAMES,
    RELEVANCE_LABELS,
    binary_relevant,
)

PILOT_ANNOTATION_VERSION = "pilot-relevance-annotation-v1"
DUPLICATE_PAIR_REVIEW_VERSION = "duplicate-pair-review-v1"
SAMPLING_FRAME_VERSION = "pilot-sampling-frame-v1"

PILOT_STATES = frozenset(
    {
        "CORPUS_PREFLIGHT",
        "BLOCKED_MISSING_CORPUS",
        "BLOCKED_MISSING_RIGHTS_CLEARED_CORPUS",
        "BLOCKED_RIGHTS_VALIDATION",
        "SAMPLE_PLAN_READY",
        "CALIBRATION_BATCH_READY",
        "CALIBRATION_LABELS_INCOMPLETE",
        "CALIBRATION_REVIEW_REQUIRED",
        "FORMAL_PILOT_APPROVAL_REQUIRED",
        "FORMAL_BATCH_READY",
        "FORMAL_LABELS_INCOMPLETE",
        "ADJUDICATION_REQUIRED",
        "PILOT_ANALYSIS_READY",
        "BENCHMARK_BUILD_REVIEW_REQUIRED",
    }
)

CANNOT_DETERMINE_REASONS = frozenset(
    {
        "BODY_UNAVAILABLE",
        "TEXT_TRUNCATED",
        "LANGUAGE_UNSUPPORTED",
        "ENTITY_REFERENCE_UNCLEAR",
        "ARTICLE_CONTEXT_INSUFFICIENT",
        "CORRUPTED_TEXT",
        "OTHER",
    }
)

DISAGREEMENT_CAUSE_CATEGORIES = frozenset(
    {
        "GUIDELINE_AMBIGUITY",
        "ARTICLE_AMBIGUITY",
        "ENTITY_CENTRALITY_AMBIGUITY",
        "INSUFFICIENT_TEXT",
        "ANNOTATOR_ERROR",
        "EVIDENCE_SPAN_DIFFERENCE",
        "ORDINAL_BOUNDARY_0_1",
        "ORDINAL_BOUNDARY_1_2",
        "ORDINAL_BOUNDARY_2_3",
        "MULTI_ENTITY_COMPLEXITY",
        "OTHER",
    }
)

CHALLENGE_REASON_CODES = frozenset(
    {
        "AMBIGUOUS_INTEL",
        "AMBIGUOUS_AMD",
        "AMBIGUOUS_MICRON",
        "MACRO_WITH_COMPANY_MENTION",
        "INDEX_CONSTITUENT_MENTION",
        "CONSUMER_PRODUCT_SECONDARY",
        "SEMICONDUCTOR_WITHOUT_TITLE_ENTITY",
        "MULTI_ENTITY",
        "INDUSTRY_PHRASE_ONLY",
        "METADATA_ONLY",
        "POSSIBLE_SYNDICATION",
        "SIMILAR_TITLE_DISTINCT_EVENT",
        "SUPPLY_CHAIN_INDIRECT",
        "POLICY_EXPORT_CONTROL",
        "FAB_CAPACITY",
        "EARNINGS",
        "OTHER_DOCUMENTED_REASON",
    }
)

PAIR_LABELS = frozenset(
    {
        "EXACT_SAME_CONTENT",
        "SYNDICATED_SAME_STORY",
        "REWRITTEN_SAME_STORY",
        "RELATED_SAME_EVENT_DISTINCT_ARTICLE",
        "RELATED_TOPIC_DISTINCT_EVENT",
        "UNRELATED",
        "CANNOT_DETERMINE",
    }
)

SAME_UNDERLYING_STORY_LABELS = frozenset(
    {
        "EXACT_SAME_CONTENT",
        "SYNDICATED_SAME_STORY",
        "REWRITTEN_SAME_STORY",
    }
)

SAMPLE_ROLES = frozenset({"representative", "challenge", "calibration", "formal"})

READINESS_STATUSES = frozenset(
    {
        "NOT_READY",
        "REVIEW_REQUIRED",
        "READY_FOR_FINAL_BENCHMARK_CONSTRUCTION",
    }
)

CALIBRATION_DECISIONS = frozenset(
    {
        "READY_FOR_FORMAL_PILOT",
        "REVISE_GUIDELINES",
        "REVISE_SCHEMA",
        "REVISE_CORPUS_ELIGIBILITY",
        "BLOCKED",
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


def _parse_dt(raw: Any, field_name: str) -> datetime:
    if isinstance(raw, datetime):
        return _utc(raw, field_name)
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return _utc(datetime.fromisoformat(text), field_name)


def _parse_optional_dt(raw: Any, field_name: str) -> Optional[datetime]:
    if raw is None or raw == "":
        return None
    return _parse_dt(raw, field_name)


def is_same_underlying_story(pair_label: str) -> Optional[bool]:
    if pair_label == "CANNOT_DETERMINE":
        return None
    if pair_label in SAME_UNDERLYING_STORY_LABELS:
        return True
    if pair_label in PAIR_LABELS:
        return False
    raise ValueError(f"unknown pair_label: {pair_label}")


@dataclass(frozen=True)
class PilotRelevanceAnnotationV1:
    """Pilot annotation unit. cannot_determine is never encoded as label 0."""

    article_id: str
    duplicate_cluster_id: str
    sample_roles: tuple[str, ...]
    annotator_id: str
    guideline_version: str
    relevance_label: Optional[int]
    cannot_determine: bool
    cannot_determine_reason: Optional[str]
    central_entity_ids: tuple[str, ...]
    secondary_entity_ids: tuple[str, ...]
    evidence_text: Optional[str]
    evidence_start: Optional[int]
    evidence_end: Optional[int]
    content_sufficient: bool
    uncertain: bool
    uncertainty_reason: Optional[str]
    decision_reason_code: str
    annotator_notes: Optional[str]
    annotation_started_at: Optional[datetime]
    annotation_completed_at: datetime
    annotation_version: str = PILOT_ANNOTATION_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "article_id", _req_text(self.article_id, "article_id"))
        object.__setattr__(
            self,
            "duplicate_cluster_id",
            _req_text(self.duplicate_cluster_id, "duplicate_cluster_id"),
        )
        roles = tuple(str(r).strip() for r in self.sample_roles if str(r).strip())
        if not roles:
            raise ValueError("sample_roles must be non-empty")
        for role in roles:
            if role not in SAMPLE_ROLES:
                raise ValueError(f"unsupported sample role {role!r}")
        object.__setattr__(self, "sample_roles", roles)
        object.__setattr__(self, "annotator_id", _req_text(self.annotator_id, "annotator_id"))
        object.__setattr__(
            self, "guideline_version", _req_text(self.guideline_version, "guideline_version")
        )
        if self.cannot_determine:
            if self.relevance_label is not None:
                raise ValueError("relevance_label must be null when cannot_determine is true")
            if not self.cannot_determine_reason:
                raise ValueError("cannot_determine_reason required when cannot_determine")
            reason = str(self.cannot_determine_reason).strip()
            if reason not in CANNOT_DETERMINE_REASONS:
                raise ValueError(f"unsupported cannot_determine_reason {reason!r}")
            object.__setattr__(self, "cannot_determine_reason", reason)
        else:
            if self.relevance_label not in RELEVANCE_LABELS:
                raise ValueError(
                    f"relevance_label must be in {sorted(RELEVANCE_LABELS)} "
                    "when cannot_determine is false"
                )
            object.__setattr__(self, "cannot_determine_reason", None)
        object.__setattr__(
            self,
            "central_entity_ids",
            tuple(str(x).strip() for x in self.central_entity_ids if str(x).strip()),
        )
        object.__setattr__(
            self,
            "secondary_entity_ids",
            tuple(str(x).strip() for x in self.secondary_entity_ids if str(x).strip()),
        )
        if (self.evidence_start is None) ^ (self.evidence_end is None):
            raise ValueError("evidence_start and evidence_end must both be set or both null")
        if self.evidence_start is not None and self.evidence_end is not None:
            if self.evidence_start < 0 or self.evidence_end < self.evidence_start:
                raise ValueError("invalid evidence span")
            if self.evidence_text is not None:
                if self.evidence_end - self.evidence_start != len(self.evidence_text):
                    raise ValueError(
                        "evidence_text length must equal evidence_end - evidence_start"
                    )
        if self.uncertain and not (
            self.uncertainty_reason and str(self.uncertainty_reason).strip()
        ):
            raise ValueError("uncertainty_reason required when uncertain is true")
        if not self.uncertain:
            object.__setattr__(self, "uncertainty_reason", None)
        object.__setattr__(
            self,
            "decision_reason_code",
            _req_text(self.decision_reason_code, "decision_reason_code"),
        )
        object.__setattr__(
            self,
            "annotation_started_at",
            (
                _utc(self.annotation_started_at, "annotation_started_at")
                if self.annotation_started_at is not None
                else None
            ),
        )
        object.__setattr__(
            self,
            "annotation_completed_at",
            _utc(self.annotation_completed_at, "annotation_completed_at"),
        )
        object.__setattr__(
            self,
            "annotation_version",
            _req_text(self.annotation_version, "annotation_version"),
        )

    @property
    def binary_relevant(self) -> Optional[bool]:
        if self.cannot_determine or self.relevance_label is None:
            return None
        return binary_relevant(self.relevance_label)

    def to_dict(self) -> dict[str, Any]:
        payload = to_json_value(self)
        assert isinstance(payload, dict)
        payload["binary_relevant"] = self.binary_relevant
        if self.relevance_label is not None:
            payload["relevance_label_name"] = RELEVANCE_LABEL_NAMES[self.relevance_label]
        else:
            payload["relevance_label_name"] = None
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PilotRelevanceAnnotationV1:
        roles = data.get("sample_roles") or ("formal",)
        if isinstance(roles, str):
            roles = [x for x in roles.split("|") if x.strip()]
        central = data.get("central_entity_ids") or ()
        if isinstance(central, str):
            central = [x for x in central.split("|") if x.strip()]
        secondary = data.get("secondary_entity_ids") or ()
        if isinstance(secondary, str):
            secondary = [x for x in secondary.split("|") if x.strip()]
        label_raw = data.get("relevance_label")
        cannot = bool(data.get("cannot_determine", False))
        return cls(
            article_id=str(data["article_id"]),
            duplicate_cluster_id=str(data["duplicate_cluster_id"]),
            sample_roles=tuple(roles),
            annotator_id=str(data["annotator_id"]),
            guideline_version=str(data["guideline_version"]),
            relevance_label=(None if cannot or label_raw in (None, "") else int(str(label_raw))),
            cannot_determine=cannot,
            cannot_determine_reason=(
                str(data["cannot_determine_reason"])
                if data.get("cannot_determine_reason")
                else None
            ),
            central_entity_ids=tuple(central),
            secondary_entity_ids=tuple(secondary),
            evidence_text=(str(data["evidence_text"]) if data.get("evidence_text") else None),
            evidence_start=(
                int(data["evidence_start"]) if data.get("evidence_start") is not None else None
            ),
            evidence_end=(
                int(data["evidence_end"]) if data.get("evidence_end") is not None else None
            ),
            content_sufficient=bool(data.get("content_sufficient", True)),
            uncertain=bool(data.get("uncertain", False)),
            uncertainty_reason=(
                str(data["uncertainty_reason"]) if data.get("uncertainty_reason") else None
            ),
            decision_reason_code=str(data.get("decision_reason_code", "UNSPECIFIED")),
            annotator_notes=(str(data["annotator_notes"]) if data.get("annotator_notes") else None),
            annotation_started_at=_parse_optional_dt(
                data.get("annotation_started_at"), "annotation_started_at"
            ),
            annotation_completed_at=_parse_dt(
                data["annotation_completed_at"], "annotation_completed_at"
            ),
            annotation_version=str(data.get("annotation_version", PILOT_ANNOTATION_VERSION)),
        )


@dataclass(frozen=True)
class DuplicatePairReviewV1:
    pair_id: str
    article_id_a: str
    article_id_b: str
    exact_cluster_id_a: str
    exact_cluster_id_b: str
    title_similarity: Optional[float]
    body_similarity: Optional[float]
    publication_time_distance_hours: Optional[float]
    language_pair: str
    domain_pair: str
    score_bin: str
    candidate_at_current_threshold: bool
    sampling_probability: float
    sampling_weight: float
    reviewer_id: str
    pair_label: str
    uncertain: bool
    reason: str
    reviewed_at: datetime
    review_version: str = DUPLICATE_PAIR_REVIEW_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "pair_id", _req_text(self.pair_id, "pair_id"))
        if self.pair_label not in PAIR_LABELS:
            raise ValueError(f"unsupported pair_label {self.pair_label!r}")
        if not 0.0 < self.sampling_probability <= 1.0:
            raise ValueError("sampling_probability must be in (0, 1]")
        if self.sampling_weight <= 0:
            raise ValueError("sampling_weight must be > 0")
        object.__setattr__(self, "reviewed_at", _utc(self.reviewed_at, "reviewed_at"))
        object.__setattr__(self, "reviewer_id", _req_text(self.reviewer_id, "reviewer_id"))
        object.__setattr__(self, "reason", _req_text(self.reason, "reason"))

    @property
    def same_underlying_story(self) -> Optional[bool]:
        return is_same_underlying_story(self.pair_label)

    def to_dict(self) -> dict[str, Any]:
        payload = to_json_value(self)
        assert isinstance(payload, dict)
        payload["same_underlying_story"] = self.same_underlying_story
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DuplicatePairReviewV1:
        return cls(
            pair_id=str(data["pair_id"]),
            article_id_a=str(data["article_id_a"]),
            article_id_b=str(data["article_id_b"]),
            exact_cluster_id_a=str(data["exact_cluster_id_a"]),
            exact_cluster_id_b=str(data["exact_cluster_id_b"]),
            title_similarity=(
                float(data["title_similarity"])
                if data.get("title_similarity") is not None
                else None
            ),
            body_similarity=(
                float(data["body_similarity"]) if data.get("body_similarity") is not None else None
            ),
            publication_time_distance_hours=(
                float(data["publication_time_distance_hours"])
                if data.get("publication_time_distance_hours") is not None
                else None
            ),
            language_pair=str(data["language_pair"]),
            domain_pair=str(data["domain_pair"]),
            score_bin=str(data["score_bin"]),
            candidate_at_current_threshold=bool(data["candidate_at_current_threshold"]),
            sampling_probability=float(data["sampling_probability"]),
            sampling_weight=float(data["sampling_weight"]),
            reviewer_id=str(data["reviewer_id"]),
            pair_label=str(data["pair_label"]),
            uncertain=bool(data.get("uncertain", False)),
            reason=str(data["reason"]),
            reviewed_at=_parse_dt(data["reviewed_at"], "reviewed_at"),
            review_version=str(data.get("review_version", DUPLICATE_PAIR_REVIEW_VERSION)),
        )


@dataclass(frozen=True)
class PilotSamplingFrameUnitV1:
    cluster_id: str
    canonical_article_id: str
    cluster_size: int
    publication_time: Optional[str]
    domain: str
    language: Optional[str]
    content_status: str
    matched_entity_ids: tuple[str, ...]
    match_fields: tuple[str, ...]
    clear_match_count: int
    ambiguous_match_count: int
    industry_phrase_only: bool
    multi_entity: bool
    difficulty_flags: tuple[str, ...]
    sampling_stratum: str
    eligibility_status: str
    rights_status: Optional[str]
    published_at_precision: str
    publication_timezone_present: bool
    publication_time_inferred: bool
    frame_version: str = SAMPLING_FRAME_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "canonical_article_id": self.canonical_article_id,
            "cluster_size": self.cluster_size,
            "publication_time": self.publication_time,
            "domain": self.domain,
            "language": self.language,
            "content_status": self.content_status,
            "matched_entity_ids": list(self.matched_entity_ids),
            "match_fields": list(self.match_fields),
            "clear_match_count": self.clear_match_count,
            "ambiguous_match_count": self.ambiguous_match_count,
            "industry_phrase_only": self.industry_phrase_only,
            "multi_entity": self.multi_entity,
            "difficulty_flags": list(self.difficulty_flags),
            "sampling_stratum": self.sampling_stratum,
            "eligibility_status": self.eligibility_status,
            "rights_status": self.rights_status,
            "published_at_precision": self.published_at_precision,
            "publication_timezone_present": self.publication_timezone_present,
            "publication_time_inferred": self.publication_time_inferred,
            "frame_version": self.frame_version,
        }


@dataclass(frozen=True)
class SampledUnitV1:
    cluster_id: str
    article_id: str
    sample_roles: tuple[str, ...]
    sampling_stratum: str
    inclusion_probability: Optional[float]
    design_weight: Optional[float]
    challenge_reason_codes: tuple[str, ...]
    double_annotate: bool
    purposive: bool
    sampling_rank_key: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "article_id": self.article_id,
            "sample_roles": list(self.sample_roles),
            "sampling_stratum": self.sampling_stratum,
            "inclusion_probability": self.inclusion_probability,
            "design_weight": self.design_weight,
            "challenge_reason_codes": list(self.challenge_reason_codes),
            "double_annotate": self.double_annotate,
            "purposive": self.purposive,
            "sampling_rank_key": self.sampling_rank_key,
        }


def merge_sample_roles(*role_lists: Sequence[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for roles in role_lists:
        for role in roles:
            if role not in seen:
                seen.append(role)
    return tuple(seen)

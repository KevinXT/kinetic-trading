"""Versioned corpus eligibility rules for the real-corpus relevance pilot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timezone
from typing import Any, Mapping, Optional, Sequence

from news_data.article.models import ArticleTextRecordV1
from news_data.dedupe.exact import ExactDuplicateCluster

from research_data.relevance.provenance import (
    ACCEPTABLE_RIGHTS_FOR_ELIGIBILITY,
    ArticleContentProvenanceV1,
)

ELIGIBILITY_VERSION = "pilot-corpus-eligibility-v1"

EXCLUSION_REASON_CODES = frozenset(
    {
        "RIGHTS_UNCLEAR",
        "RIGHTS_RESTRICTED",
        "MISSING_PROVENANCE",
        "INVALID_PROVENANCE",
        "OUTSIDE_DATE_WINDOW",
        "UNSUPPORTED_LANGUAGE",
        "MISSING_PUBLICATION_TIME",
        "INSUFFICIENT_TEXT",
        "DUPLICATE_NONCANONICAL_MEMBER",
        "CONTENT_RESTRICTED",
        "METADATA_ONLY_DISALLOWED",
        "RETRIEVAL_FAILED_DISALLOWED",
        "MISSING_SOURCE_URL",
        "MISSING_ENTITY_OR_INDUSTRY_CANDIDATE_EVIDENCE",
        "RESEARCH_USE_NOT_ALLOWED",
        "STORAGE_NOT_ALLOWED",
    }
)


@dataclass(frozen=True)
class PilotCorpusEligibilityV1:
    permitted_rights_statuses: frozenset[str] = frozenset(ACCEPTABLE_RIGHTS_FOR_ELIGIBILITY)
    permitted_languages: frozenset[str] = frozenset({"en"})
    publication_date_start: Optional[date] = None
    publication_date_end: Optional[date] = None
    minimum_title_length: int = 8
    minimum_available_text_length: int = 40
    metadata_only_eligible: bool = True
    undated_eligible: bool = False
    retrieval_failed_eligible: bool = False
    require_provenance: bool = True
    require_source_url: bool = True
    require_exact_cluster_canonical: bool = True
    require_entity_or_industry_evidence: bool = False
    eligibility_version: str = ELIGIBILITY_VERSION

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> PilotCorpusEligibilityV1:
        if not data:
            return cls()
        start = data.get("publication_date_start")
        end = data.get("publication_date_end")
        return cls(
            permitted_rights_statuses=frozenset(
                str(x)
                for x in (
                    data.get("permitted_rights_statuses") or ACCEPTABLE_RIGHTS_FOR_ELIGIBILITY
                )
            ),
            permitted_languages=frozenset(
                str(x).strip().lower() for x in (data.get("permitted_languages") or ("en",))
            ),
            publication_date_start=(date.fromisoformat(str(start)) if start is not None else None),
            publication_date_end=(date.fromisoformat(str(end)) if end is not None else None),
            minimum_title_length=int(data.get("minimum_title_length", 8)),
            minimum_available_text_length=int(data.get("minimum_available_text_length", 40)),
            metadata_only_eligible=bool(data.get("metadata_only_eligible", True)),
            undated_eligible=bool(data.get("undated_eligible", False)),
            retrieval_failed_eligible=bool(data.get("retrieval_failed_eligible", False)),
            require_provenance=bool(data.get("require_provenance", True)),
            require_source_url=bool(data.get("require_source_url", True)),
            require_exact_cluster_canonical=bool(data.get("require_exact_cluster_canonical", True)),
            require_entity_or_industry_evidence=bool(
                data.get("require_entity_or_industry_evidence", False)
            ),
            eligibility_version=str(data.get("eligibility_version", ELIGIBILITY_VERSION)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "permitted_rights_statuses": sorted(self.permitted_rights_statuses),
            "permitted_languages": sorted(self.permitted_languages),
            "publication_date_start": (
                self.publication_date_start.isoformat() if self.publication_date_start else None
            ),
            "publication_date_end": (
                self.publication_date_end.isoformat() if self.publication_date_end else None
            ),
            "minimum_title_length": self.minimum_title_length,
            "minimum_available_text_length": self.minimum_available_text_length,
            "metadata_only_eligible": self.metadata_only_eligible,
            "undated_eligible": self.undated_eligible,
            "retrieval_failed_eligible": self.retrieval_failed_eligible,
            "require_provenance": self.require_provenance,
            "require_source_url": self.require_source_url,
            "require_exact_cluster_canonical": self.require_exact_cluster_canonical,
            "require_entity_or_industry_evidence": self.require_entity_or_industry_evidence,
            "eligibility_version": self.eligibility_version,
        }


@dataclass(frozen=True)
class EligibilityDecisionV1:
    article_id: str
    eligible: bool
    reason_codes: tuple[str, ...]
    rights_status: Optional[str]
    is_canonical_cluster_member: Optional[bool]
    eligibility_version: str = ELIGIBILITY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "article_id": self.article_id,
            "eligible": self.eligible,
            "ineligible": not self.eligible,
            "reason_codes": list(self.reason_codes),
            "rights_status": self.rights_status,
            "is_canonical_cluster_member": self.is_canonical_cluster_member,
            "eligibility_version": self.eligibility_version,
        }


@dataclass
class EligibilityFlowCounts:
    local_input: int = 0
    schema_valid: int = 0
    schema_rejected: int = 0
    rights_eligible: int = 0
    rights_ineligible: int = 0
    corpus_eligible: int = 0
    corpus_excluded_non_rights: int = 0
    eligible_articles: int = 0
    exact_cluster_memberships: int = 0
    sampling_frame_units: int = 0
    reason_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_input": self.local_input,
            "schema_valid": self.schema_valid,
            "schema_rejected": self.schema_rejected,
            "rights_eligible": self.rights_eligible,
            "rights_ineligible": self.rights_ineligible,
            "corpus_eligible": self.corpus_eligible,
            "corpus_excluded_non_rights": self.corpus_excluded_non_rights,
            "eligible_articles": self.eligible_articles,
            "exact_cluster_memberships": self.exact_cluster_memberships,
            "sampling_frame_units": self.sampling_frame_units,
            "reason_counts": dict(sorted(self.reason_counts.items())),
            "flow_diagram": (
                "local input → schema-valid → rights-eligible → "
                "date/language eligible → exact clusters → canonical sample frame"
            ),
        }


def _available_text_length(article: ArticleTextRecordV1) -> int:
    parts = [article.title]
    if article.description:
        parts.append(article.description)
    if article.body:
        parts.append(article.body)
    return sum(len(p) for p in parts)


def evaluate_article_eligibility(
    article: ArticleTextRecordV1,
    provenance: Optional[ArticleContentProvenanceV1],
    *,
    rules: PilotCorpusEligibilityV1,
    is_canonical: Optional[bool] = None,
    has_entity_or_industry_evidence: bool = True,
) -> EligibilityDecisionV1:
    reasons: list[str] = []

    if rules.require_provenance and provenance is None:
        reasons.append("MISSING_PROVENANCE")
    if provenance is not None:
        if provenance.rights_status not in rules.permitted_rights_statuses:
            if provenance.rights_status in {"rights_unclear"}:
                reasons.append("RIGHTS_UNCLEAR")
            elif provenance.rights_status in {
                "redistribution_restricted",
                "research_use_restricted",
            }:
                reasons.append("RIGHTS_RESTRICTED")
            else:
                reasons.append("INVALID_PROVENANCE")
        if not provenance.research_use_allowed:
            reasons.append("RESEARCH_USE_NOT_ALLOWED")
        if not provenance.storage_allowed:
            reasons.append("STORAGE_NOT_ALLOWED")
        if article.content_status == "rights_restricted":
            reasons.append("CONTENT_RESTRICTED")

    if rules.require_source_url and not (article.url and article.url.strip()):
        reasons.append("MISSING_SOURCE_URL")

    lang = (article.language or "").strip().lower()
    if rules.permitted_languages and lang not in rules.permitted_languages:
        reasons.append("UNSUPPORTED_LANGUAGE")

    if article.published_at is None:
        if not rules.undated_eligible:
            reasons.append("MISSING_PUBLICATION_TIME")
    else:
        pub_date = article.published_at.astimezone(timezone.utc).date()
        if rules.publication_date_start and pub_date < rules.publication_date_start:
            reasons.append("OUTSIDE_DATE_WINDOW")
        if rules.publication_date_end and pub_date > rules.publication_date_end:
            reasons.append("OUTSIDE_DATE_WINDOW")

    if len(article.title.strip()) < rules.minimum_title_length:
        reasons.append("INSUFFICIENT_TEXT")
    elif _available_text_length(article) < rules.minimum_available_text_length:
        if article.content_status == "metadata_only" and rules.metadata_only_eligible:
            # Metadata-only may pass with title alone if title meets minimum.
            if len(article.title.strip()) < rules.minimum_title_length:
                reasons.append("INSUFFICIENT_TEXT")
        else:
            reasons.append("INSUFFICIENT_TEXT")

    if article.content_status == "metadata_only" and not rules.metadata_only_eligible:
        reasons.append("METADATA_ONLY_DISALLOWED")
    if article.content_status == "retrieval_failed" and not rules.retrieval_failed_eligible:
        reasons.append("RETRIEVAL_FAILED_DISALLOWED")

    if rules.require_exact_cluster_canonical and is_canonical is not None and not is_canonical:
        reasons.append("DUPLICATE_NONCANONICAL_MEMBER")

    if rules.require_entity_or_industry_evidence and not has_entity_or_industry_evidence:
        reasons.append("MISSING_ENTITY_OR_INDUSTRY_CANDIDATE_EVIDENCE")

    # Deduplicate while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for code in reasons:
        if code not in EXCLUSION_REASON_CODES:
            raise ValueError(f"unknown exclusion reason code: {code}")
        if code not in seen:
            seen.add(code)
            ordered.append(code)

    return EligibilityDecisionV1(
        article_id=article.article_id,
        eligible=len(ordered) == 0,
        reason_codes=tuple(ordered),
        rights_status=(provenance.rights_status if provenance else None),
        is_canonical_cluster_member=is_canonical,
        eligibility_version=rules.eligibility_version,
    )


def evaluate_corpus_eligibility(
    articles: Sequence[ArticleTextRecordV1],
    provenance_by_id: Mapping[str, ArticleContentProvenanceV1],
    clusters: Sequence[ExactDuplicateCluster],
    *,
    rules: PilotCorpusEligibilityV1,
    schema_rejected: int = 0,
    entity_or_industry_ids: Optional[set[str]] = None,
) -> tuple[list[EligibilityDecisionV1], EligibilityFlowCounts, list[str]]:
    """Return decisions, flow counts, and eligible canonical article IDs for the frame."""
    canonical_ids = {c.canonical_article_id for c in clusters}
    member_to_canonical: dict[str, str] = {}
    for cluster in clusters:
        for mid in cluster.member_article_ids:
            member_to_canonical[mid] = cluster.canonical_article_id

    decisions: list[EligibilityDecisionV1] = []
    reason_counts: dict[str, int] = {}
    rights_eligible = 0
    rights_ineligible = 0
    corpus_eligible = 0
    corpus_excluded_non_rights = 0

    evidence = entity_or_industry_ids or set()

    for article in sorted(articles, key=lambda a: a.article_id):
        prov = provenance_by_id.get(article.article_id)
        is_canonical = article.article_id in canonical_ids
        decision = evaluate_article_eligibility(
            article,
            prov,
            rules=rules,
            is_canonical=is_canonical if rules.require_exact_cluster_canonical else None,
            has_entity_or_industry_evidence=(
                article.article_id in evidence
                if rules.require_entity_or_industry_evidence
                else True
            ),
        )
        decisions.append(decision)
        for code in decision.reason_codes:
            reason_counts[code] = reason_counts.get(code, 0) + 1

        rights_codes = {
            "RIGHTS_UNCLEAR",
            "RIGHTS_RESTRICTED",
            "MISSING_PROVENANCE",
            "INVALID_PROVENANCE",
            "RESEARCH_USE_NOT_ALLOWED",
            "STORAGE_NOT_ALLOWED",
            "CONTENT_RESTRICTED",
        }
        has_rights_issue = any(c in rights_codes for c in decision.reason_codes)
        if has_rights_issue:
            rights_ineligible += 1
        else:
            rights_eligible += 1
            if decision.eligible:
                corpus_eligible += 1
            else:
                corpus_excluded_non_rights += 1

    eligible_canonical = sorted(
        d.article_id
        for d in decisions
        if d.eligible and (d.is_canonical_cluster_member is not False)
    )

    # Cluster membership sum for eligible articles (all members of clusters whose
    # canonical is eligible — used for reconciliation).
    eligible_set = {d.article_id for d in decisions if d.eligible}
    membership_sum = 0
    frame_units = 0
    for cluster in clusters:
        if cluster.canonical_article_id in eligible_set:
            frame_units += 1
            membership_sum += len(cluster.member_article_ids)

    counts = EligibilityFlowCounts(
        local_input=len(articles) + schema_rejected,
        schema_valid=len(articles),
        schema_rejected=schema_rejected,
        rights_eligible=rights_eligible,
        rights_ineligible=rights_ineligible,
        corpus_eligible=corpus_eligible,
        corpus_excluded_non_rights=corpus_excluded_non_rights,
        eligible_articles=len(eligible_set),
        exact_cluster_memberships=membership_sum,
        sampling_frame_units=frame_units,
        reason_counts=reason_counts,
    )
    return decisions, counts, eligible_canonical


def render_rights_validation_report(
    decisions: Sequence[EligibilityDecisionV1],
    counts: EligibilityFlowCounts,
) -> str:
    lines = [
        "# Rights and eligibility validation report",
        "",
        "Public reachability is not treated as redistribution permission.",
        "",
        f"- Local input rows (schema accepted + rejected): {counts.local_input}",
        f"- Schema accepted: {counts.schema_valid}",
        f"- Schema rejected: {counts.schema_rejected}",
        f"- Rights-eligible (no rights-blocking codes): {counts.rights_eligible}",
        f"- Rights-ineligible: {counts.rights_ineligible}",
        f"- Corpus eligible: {counts.corpus_eligible}",
        f"- Corpus excluded for non-rights reasons: {counts.corpus_excluded_non_rights}",
        f"- Sampling frame units (eligible exact clusters): {counts.sampling_frame_units}",
        "",
        "## Exclusion reason counts",
        "",
    ]
    if counts.reason_counts:
        for code, n in sorted(counts.reason_counts.items()):
            lines.append(f"- `{code}`: {n}")
    else:
        lines.append("- (none)")
    lines.extend(
        [
            "",
            "## Flow",
            "",
            "```text",
            counts.to_dict()["flow_diagram"],
            "```",
            "",
            f"Eligible article decisions: {sum(1 for d in decisions if d.eligible)}",
            f"Ineligible article decisions: {sum(1 for d in decisions if not d.eligible)}",
            "",
        ]
    )
    return "\n".join(lines)

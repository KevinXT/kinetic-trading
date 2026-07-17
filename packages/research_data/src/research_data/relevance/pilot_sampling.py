"""Probability (Sample A), challenge (Sample B), and pair (Sample C) sampling."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timezone
from typing import Any, Mapping, Optional, Sequence

from news_data.article.models import ArticleTextRecordV1
from news_data.dedupe.exact import ExactDuplicateCluster
from news_data.dedupe.near import (
    NearDuplicateConfig,
    jaccard,
    title_tokens,
    word_shingles,
)
from news_data.entity.matching import EntityMatchV1

from research_data.relevance.eligibility import EligibilityDecisionV1
from research_data.relevance.pilot_models import (
    CHALLENGE_REASON_CODES,
    PilotSamplingFrameUnitV1,
    SampledUnitV1,
    merge_sample_roles,
)
from research_data.relevance.provenance import ArticleContentProvenanceV1
from research_data.relevance.sampling import _industry_phrase_hit

PILOT_SAMPLING_VERSION = "pilot-probability-sampling-v1"

# Mutually exclusive primary stratum precedence (documented).
STRATUM_PRECEDENCE = (
    "industry_phrase_only",
    "ambiguous_alias_only",
    "multiple_clear_entities",
    "single_clear_entity",
    "metadata_only",
    "other_candidate",
)

PAIR_SCORE_BINS = (
    (0.00, 0.30, "[0.00,0.30)"),
    (0.30, 0.50, "[0.30,0.50)"),
    (0.50, 0.60, "[0.50,0.60)"),
    (0.60, 0.65, "[0.60,0.65)"),
    (0.65, 0.70, "[0.65,0.70)"),
    (0.70, 0.75, "[0.70,0.75)"),
    (0.75, 0.80, "[0.75,0.80)"),
    (0.80, 0.90, "[0.80,0.90)"),
    (0.90, 1.01, "[0.90,1.00]"),
)


def _stable_rank_key(unit_id: str, seed: str) -> str:
    return hashlib.sha256(f"{seed}|{unit_id}".encode("utf-8")).hexdigest()


def assign_primary_stratum(
    article: ArticleTextRecordV1,
    matches: Sequence[EntityMatchV1],
    *,
    industry_phrase_only: bool,
) -> str:
    """Deterministic mutually exclusive stratum — does not use labels."""
    if industry_phrase_only and not matches:
        return "industry_phrase_only"
    if matches and all(m.ambiguity_status == "ambiguous_alias" for m in matches):
        return "ambiguous_alias_only"
    clear = [m for m in matches if m.ambiguity_status == "clear"]
    clear_entities = {m.entity_id for m in clear}
    if len(clear_entities) >= 2:
        return "multiple_clear_entities"
    if len(clear_entities) == 1:
        return f"single_clear_entity:{sorted(clear_entities)[0]}"
    if article.content_status == "metadata_only" or article.body is None:
        return "metadata_only"
    return "other_candidate"


def _publication_precision(article: ArticleTextRecordV1) -> tuple[str, bool, bool]:
    """Return precision, timezone_present, inferred."""
    if article.published_at is None:
        return "missing", False, False
    # Heuristic from source field metadata when available.
    source = (article.published_at_source or "").lower()
    inferred = "infer" in source or source.endswith("_date")
    tz_present = article.published_timezone is not None or (article.published_at.tzinfo is not None)
    # Date-only: midnight UTC with published_at_source suggesting date.
    if article.published_at.hour == 0 and article.published_at.minute == 0:
        if "date" in source or article.published_at.second == 0 and "time" not in source:
            if "minute" not in source and "second" not in source:
                return "date", tz_present, inferred
    if article.published_at.second != 0:
        return "second", tz_present, inferred
    if article.published_at.minute != 0 or "minute" in source:
        return "minute", tz_present, inferred
    return "second", tz_present, inferred


def build_sampling_frame(
    articles: Sequence[ArticleTextRecordV1],
    clusters: Sequence[ExactDuplicateCluster],
    matches: Sequence[EntityMatchV1],
    decisions: Sequence[EligibilityDecisionV1],
    provenance_by_id: Mapping[str, ArticleContentProvenanceV1],
    near_ids: Optional[set[str]] = None,
) -> list[PilotSamplingFrameUnitV1]:
    by_id = {a.article_id: a for a in articles}
    decision_by_id = {d.article_id: d for d in decisions}
    matches_by: dict[str, list[EntityMatchV1]] = defaultdict(list)
    for m in matches:
        matches_by[m.article_id].append(m)
    near = near_ids or set()

    frame: list[PilotSamplingFrameUnitV1] = []
    for cluster in sorted(clusters, key=lambda c: c.cluster_id):
        article = by_id[cluster.canonical_article_id]
        decision = decision_by_id.get(article.article_id)
        if decision is None or not decision.eligible:
            continue
        am = matches_by.get(article.article_id, [])
        industry_hit = _industry_phrase_hit(article)
        industry_only = industry_hit and not am
        stratum = assign_primary_stratum(article, am, industry_phrase_only=industry_only)
        clear_n = sum(1 for m in am if m.ambiguity_status == "clear")
        amb_n = sum(1 for m in am if m.ambiguity_status == "ambiguous_alias")
        entity_ids = tuple(sorted({m.entity_id for m in am}))
        flags: list[str] = []
        if article.article_id in near:
            flags.append("POSSIBLE_SYNDICATION")
        if industry_only:
            flags.append("INDUSTRY_PHRASE_ONLY")
        if len(entity_ids) >= 2:
            flags.append("MULTI_ENTITY")
        if article.content_status == "metadata_only":
            flags.append("METADATA_ONLY")
        title_has_entity = any(m.match_field == "title" for m in am)
        if am and not title_has_entity:
            flags.append("SEMICONDUCTOR_WITHOUT_TITLE_ENTITY")
        for m in am:
            alias = m.matched_alias.casefold()
            if alias == "intel":
                flags.append("AMBIGUOUS_INTEL")
            if alias == "amd":
                flags.append("AMBIGUOUS_AMD")
            if alias == "micron":
                flags.append("AMBIGUOUS_MICRON")
        precision, tz_present, inferred = _publication_precision(article)
        prov = provenance_by_id.get(article.article_id)
        frame.append(
            PilotSamplingFrameUnitV1(
                cluster_id=cluster.cluster_id,
                canonical_article_id=article.article_id,
                cluster_size=len(cluster.member_article_ids),
                publication_time=(
                    article.published_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                    if article.published_at
                    else None
                ),
                domain=article.domain,
                language=article.language,
                content_status=article.content_status,
                matched_entity_ids=entity_ids,
                match_fields=tuple(sorted({m.match_field for m in am})),
                clear_match_count=clear_n,
                ambiguous_match_count=amb_n,
                industry_phrase_only=industry_only,
                multi_entity=len(entity_ids) >= 2,
                difficulty_flags=tuple(sorted(set(flags))),
                sampling_stratum=stratum,
                eligibility_status="eligible",
                rights_status=(prov.rights_status if prov else decision.rights_status),
                published_at_precision=precision,
                publication_timezone_present=tz_present,
                publication_time_inferred=inferred,
            )
        )
    frame.sort(key=lambda u: u.cluster_id)
    return frame


@dataclass(frozen=True)
class RepresentativeSampleConfig:
    target_n: int
    sampling_seed: str
    allocation: str = "proportional_min1"
    min_per_stratum: int = 1
    version: str = PILOT_SAMPLING_VERSION

    def __post_init__(self) -> None:
        if self.target_n < 0:
            raise ValueError("target_n must be >= 0")
        if self.allocation not in {"proportional", "proportional_min1", "explicit"}:
            raise ValueError(f"unsupported allocation {self.allocation!r}")


def sample_representative_probability(
    frame: Sequence[PilotSamplingFrameUnitV1],
    *,
    config: RepresentativeSampleConfig,
) -> tuple[list[SampledUnitV1], dict[str, Any]]:
    """Stratified SRSWOR within stratum; known π_hi = n_h / N_h."""
    by_stratum: dict[str, list[PilotSamplingFrameUnitV1]] = defaultdict(list)
    for unit in frame:
        by_stratum[unit.sampling_stratum].append(unit)

    strata = sorted(by_stratum)
    N = {h: len(by_stratum[h]) for h in strata}
    total_N = sum(N.values())
    if total_N == 0 or config.target_n == 0:
        return [], {
            "sampling_version": config.version,
            "sampling_seed": config.sampling_seed,
            "allocation": config.allocation,
            "eligible_population_N": total_N,
            "requested_n": config.target_n,
            "actual_n": 0,
            "stratum_population_sizes": N,
            "stratum_sample_sizes": {},
            "inclusion_probability_by_stratum": {},
            "notes": ["empty frame or zero target"],
            "probability_sample": True,
        }

    # Allocation
    n_h: dict[str, int] = {}
    if config.allocation in {"proportional", "proportional_min1"}:
        remaining = config.target_n
        if config.allocation == "proportional_min1":
            for h in strata:
                if N[h] > 0 and remaining > 0:
                    take = min(config.min_per_stratum, N[h])
                    n_h[h] = take
                    remaining -= take
                else:
                    n_h[h] = 0
        else:
            for h in strata:
                n_h[h] = 0
        # Proportional remainder
        if remaining > 0 and total_N > 0:
            # Largest-remainder method on unused capacity.
            quotas = []
            for h in strata:
                capacity = N[h] - n_h.get(h, 0)
                if capacity <= 0:
                    continue
                exact = remaining * (N[h] / total_N)
                quotas.append((exact - int(exact), h, int(exact), capacity))
            base_sum = 0
            for _, h, base, capacity in quotas:
                take = min(base, capacity)
                n_h[h] = n_h.get(h, 0) + take
                base_sum += take
            leftover = remaining - base_sum
            for _, h, _, capacity in sorted(quotas, reverse=True):
                if leftover <= 0:
                    break
                if n_h[h] < N[h]:
                    n_h[h] += 1
                    leftover -= 1
            # Any remaining shortfall is filled after the within-stratum draw below.
    else:
        raise ValueError("explicit allocation requires precomputed n_h (not used in default)")

    selected: list[SampledUnitV1] = []
    for h in strata:
        units = sorted(
            by_stratum[h],
            key=lambda u: (_stable_rank_key(u.cluster_id, config.sampling_seed), u.cluster_id),
        )
        take = min(n_h.get(h, 0), len(units))
        pi = (take / N[h]) if N[h] else 0.0
        weight = (N[h] / take) if take else None
        for unit in units[:take]:
            selected.append(
                SampledUnitV1(
                    cluster_id=unit.cluster_id,
                    article_id=unit.canonical_article_id,
                    sample_roles=("representative",),
                    sampling_stratum=h,
                    inclusion_probability=pi,
                    design_weight=weight,
                    challenge_reason_codes=(),
                    double_annotate=False,
                    purposive=False,
                    sampling_rank_key=_stable_rank_key(unit.cluster_id, config.sampling_seed),
                )
            )

    # If allocation undershot target due to tiny strata, fill remainder by global rank
    # among unselected, updating stratum n_h and recomputing π within affected strata.
    selected_clusters = {s.cluster_id for s in selected}
    if len(selected) < config.target_n:
        remaining_units = [
            u
            for u in sorted(
                frame,
                key=lambda u: (
                    _stable_rank_key(u.cluster_id, config.sampling_seed),
                    u.cluster_id,
                ),
            )
            if u.cluster_id not in selected_clusters
        ]
        for unit in remaining_units:
            if len(selected) >= config.target_n:
                break
            h = unit.sampling_stratum
            n_h[h] = n_h.get(h, 0) + 1
            selected.append(
                SampledUnitV1(
                    cluster_id=unit.cluster_id,
                    article_id=unit.canonical_article_id,
                    sample_roles=("representative",),
                    sampling_stratum=h,
                    inclusion_probability=None,  # recomputed below
                    design_weight=None,
                    challenge_reason_codes=(),
                    double_annotate=False,
                    purposive=False,
                    sampling_rank_key=_stable_rank_key(unit.cluster_id, config.sampling_seed),
                )
            )
            selected_clusters.add(unit.cluster_id)

    # Recompute inclusion probabilities from final n_h / N_h
    finalized: list[SampledUnitV1] = []
    for s in selected:
        h = s.sampling_stratum
        take = n_h[h]
        pi = take / N[h]
        weight = N[h] / take
        finalized.append(
            SampledUnitV1(
                cluster_id=s.cluster_id,
                article_id=s.article_id,
                sample_roles=s.sample_roles,
                sampling_stratum=s.sampling_stratum,
                inclusion_probability=pi,
                design_weight=weight,
                challenge_reason_codes=(),
                double_annotate=False,
                purposive=False,
                sampling_rank_key=s.sampling_rank_key,
            )
        )
    finalized.sort(key=lambda s: (s.sampling_rank_key, s.cluster_id))

    pi_by = {h: (n_h[h] / N[h] if N[h] else 0.0) for h in strata}
    manifest = {
        "sampling_version": config.version,
        "sampling_seed": config.sampling_seed,
        "allocation": config.allocation,
        "eligible_population_N": total_N,
        "requested_n": config.target_n,
        "actual_n": len(finalized),
        "stratum_population_sizes": dict(sorted(N.items())),
        "stratum_sample_sizes": dict(sorted(n_h.items())),
        "inclusion_probability_by_stratum": dict(sorted(pi_by.items())),
        "primary_stratum_precedence": list(STRATUM_PRECEDENCE),
        "probability_sample": True,
        "notes": [
            "Inclusion probabilities are known: π_hi = n_h / N_h under SRSWOR within stratum.",
            "Design weights w_hi = N_h / n_h.",
            "Strata assigned without using annotation labels, returns, or baseline correctness.",
            "Mutually exclusive primary strata prefer transparent weighting over entity caps.",
        ],
    }
    return finalized, manifest


def _challenge_reasons_for_unit(unit: PilotSamplingFrameUnitV1) -> list[str]:
    reasons = [f for f in unit.difficulty_flags if f in CHALLENGE_REASON_CODES]
    title_l = " ".join(unit.matched_entity_ids).casefold()
    # Map flags already on unit; add documented fallbacks.
    if unit.industry_phrase_only and "INDUSTRY_PHRASE_ONLY" not in reasons:
        reasons.append("INDUSTRY_PHRASE_ONLY")
    if unit.multi_entity and "MULTI_ENTITY" not in reasons:
        reasons.append("MULTI_ENTITY")
    if unit.content_status == "metadata_only" and "METADATA_ONLY" not in reasons:
        reasons.append("METADATA_ONLY")
    if not reasons:
        reasons.append("OTHER_DOCUMENTED_REASON")
    # silence unused
    _ = title_l
    return sorted(set(reasons))


def sample_challenge_purposive(
    frame: Sequence[PilotSamplingFrameUnitV1],
    *,
    target_n: int,
    sampling_seed: str,
    exclude_cluster_ids: Optional[set[str]] = None,
) -> tuple[list[SampledUnitV1], dict[str, Any]]:
    """Purposive challenge sample; no population weights."""
    exclude = exclude_cluster_ids or set()
    # Prefer units with difficulty flags; deterministic by rank within reason.
    by_reason: dict[str, list[PilotSamplingFrameUnitV1]] = defaultdict(list)
    for unit in frame:
        if unit.cluster_id in exclude:
            # Still eligible for multi-role; do not exclude from challenge solely
            # because representative — overlap is allowed and stored once later.
            pass
        for reason in _challenge_reasons_for_unit(unit):
            by_reason[reason].append(unit)

    selected_ids: set[str] = set()
    selected: list[SampledUnitV1] = []
    reason_coverage: Counter[str] = Counter()

    # Round-robin across reason codes for coverage.
    reason_order = sorted(by_reason)
    progressed = True
    while len(selected) < target_n and progressed:
        progressed = False
        for reason in reason_order:
            if len(selected) >= target_n:
                break
            candidates = sorted(
                by_reason[reason],
                key=lambda u: (
                    _stable_rank_key(f"{reason}|{u.cluster_id}", sampling_seed),
                    u.cluster_id,
                ),
            )
            for unit in candidates:
                if unit.cluster_id in selected_ids:
                    continue
                reasons = tuple(_challenge_reasons_for_unit(unit))
                selected.append(
                    SampledUnitV1(
                        cluster_id=unit.cluster_id,
                        article_id=unit.canonical_article_id,
                        sample_roles=("challenge",),
                        sampling_stratum=unit.sampling_stratum,
                        inclusion_probability=None,
                        design_weight=None,
                        challenge_reason_codes=reasons,
                        double_annotate=False,
                        purposive=True,
                        sampling_rank_key=_stable_rank_key(unit.cluster_id, sampling_seed),
                    )
                )
                selected_ids.add(unit.cluster_id)
                for r in reasons:
                    reason_coverage[r] += 1
                progressed = True
                break

    selected.sort(key=lambda s: (s.sampling_rank_key, s.cluster_id))
    manifest = {
        "sampling_version": PILOT_SAMPLING_VERSION,
        "sampling_seed": sampling_seed,
        "inferential_use": False,
        "requested_n": target_n,
        "actual_n": len(selected),
        "reason_code_coverage": dict(sorted(reason_coverage.items())),
        "purposive": True,
        "notes": [
            "Challenge sample is purposive and must not estimate corpus prevalence.",
            "No population inclusion weights are assigned.",
        ],
    }
    return selected, manifest


def merge_annotation_units(
    representative: Sequence[SampledUnitV1],
    challenge: Sequence[SampledUnitV1],
) -> tuple[list[SampledUnitV1], dict[str, Any]]:
    """One annotation unit per cluster; preserve multi-role membership."""
    by_cluster: dict[str, SampledUnitV1] = {}
    for unit in list(representative) + list(challenge):
        existing = by_cluster.get(unit.cluster_id)
        if existing is None:
            by_cluster[unit.cluster_id] = unit
            continue
        by_cluster[unit.cluster_id] = SampledUnitV1(
            cluster_id=existing.cluster_id,
            article_id=existing.article_id,
            sample_roles=merge_sample_roles(existing.sample_roles, unit.sample_roles),
            sampling_stratum=existing.sampling_stratum,
            inclusion_probability=existing.inclusion_probability,
            design_weight=existing.design_weight,
            challenge_reason_codes=tuple(
                sorted(set(existing.challenge_reason_codes) | set(unit.challenge_reason_codes))
            ),
            double_annotate=existing.double_annotate or unit.double_annotate,
            purposive=existing.purposive or unit.purposive,
            sampling_rank_key=min(existing.sampling_rank_key, unit.sampling_rank_key),
        )
    merged = sorted(by_cluster.values(), key=lambda u: (u.sampling_rank_key, u.cluster_id))
    overlap = [
        u.cluster_id
        for u in merged
        if "representative" in u.sample_roles and "challenge" in u.sample_roles
    ]
    report = {
        "unique_annotation_units": len(merged),
        "representative_count": sum(1 for u in merged if "representative" in u.sample_roles),
        "challenge_count": sum(1 for u in merged if "challenge" in u.sample_roles),
        "overlap_cluster_ids": overlap,
        "overlap_count": len(overlap),
        "note": "Overlapping units are stored once with both sample roles.",
    }
    return merged, report


def assign_double_annotation(
    units: Sequence[SampledUnitV1],
    *,
    target_count: int,
    sampling_seed: str,
    prefer_representative_fraction: float = 0.6,
) -> list[SampledUnitV1]:
    """Select double-annotation before labels using stable hashes."""
    if target_count <= 0 or not units:
        return list(units)
    reps = [u for u in units if "representative" in u.sample_roles]
    challenges = [u for u in units if "challenge" in u.sample_roles]
    n_rep = min(len(reps), int(round(target_count * prefer_representative_fraction)))
    n_chal = min(len(challenges), target_count - n_rep)
    # Adjust if short.
    if n_rep + n_chal < target_count:
        n_rep = min(len(reps), target_count - n_chal)

    def pick(pool: Sequence[SampledUnitV1], n: int) -> set[str]:
        ordered = sorted(
            pool,
            key=lambda u: (_stable_rank_key(f"double|{u.cluster_id}", sampling_seed), u.cluster_id),
        )
        return {u.cluster_id for u in ordered[:n]}

    double_ids = pick(reps, n_rep) | pick(challenges, n_chal)
    # Fill remainder from any
    if len(double_ids) < target_count:
        ordered = sorted(
            units,
            key=lambda u: (_stable_rank_key(f"double|{u.cluster_id}", sampling_seed), u.cluster_id),
        )
        for u in ordered:
            if len(double_ids) >= target_count:
                break
            double_ids.add(u.cluster_id)

    out: list[SampledUnitV1] = []
    for u in units:
        out.append(
            SampledUnitV1(
                cluster_id=u.cluster_id,
                article_id=u.article_id,
                sample_roles=u.sample_roles,
                sampling_stratum=u.sampling_stratum,
                inclusion_probability=u.inclusion_probability,
                design_weight=u.design_weight,
                challenge_reason_codes=u.challenge_reason_codes,
                double_annotate=u.cluster_id in double_ids,
                purposive=u.purposive,
                sampling_rank_key=u.sampling_rank_key,
            )
        )
    return out


def sample_calibration_units(
    frame: Sequence[PilotSamplingFrameUnitV1],
    *,
    target_n: int,
    sampling_seed: str,
) -> list[SampledUnitV1]:
    """Calibration units balanced across difficulty where feasible; all double-annotated."""
    if target_n <= 0:
        return []
    by_flag: dict[str, list[PilotSamplingFrameUnitV1]] = defaultdict(list)
    for unit in frame:
        flags = unit.difficulty_flags or ("OTHER_DOCUMENTED_REASON",)
        for f in flags:
            by_flag[f].append(unit)
    selected_ids: set[str] = set()
    selected: list[SampledUnitV1] = []
    flag_keys = sorted(by_flag)
    progressed = True
    while len(selected) < target_n and progressed:
        progressed = False
        for flag in flag_keys:
            if len(selected) >= target_n:
                break
            for unit in sorted(
                by_flag[flag],
                key=lambda u: (
                    _stable_rank_key(f"calib|{flag}|{u.cluster_id}", sampling_seed),
                    u.cluster_id,
                ),
            ):
                if unit.cluster_id in selected_ids:
                    continue
                selected.append(
                    SampledUnitV1(
                        cluster_id=unit.cluster_id,
                        article_id=unit.canonical_article_id,
                        sample_roles=("calibration",),
                        sampling_stratum=unit.sampling_stratum,
                        inclusion_probability=None,
                        design_weight=None,
                        challenge_reason_codes=tuple(
                            f for f in unit.difficulty_flags if f in CHALLENGE_REASON_CODES
                        ),
                        double_annotate=True,
                        purposive=True,
                        sampling_rank_key=_stable_rank_key(unit.cluster_id, sampling_seed),
                    )
                )
                selected_ids.add(unit.cluster_id)
                progressed = True
                break
    # Fill remainder
    if len(selected) < target_n:
        for unit in sorted(
            frame,
            key=lambda u: (_stable_rank_key(f"calib|{u.cluster_id}", sampling_seed), u.cluster_id),
        ):
            if len(selected) >= target_n:
                break
            if unit.cluster_id in selected_ids:
                continue
            selected.append(
                SampledUnitV1(
                    cluster_id=unit.cluster_id,
                    article_id=unit.canonical_article_id,
                    sample_roles=("calibration",),
                    sampling_stratum=unit.sampling_stratum,
                    inclusion_probability=None,
                    design_weight=None,
                    challenge_reason_codes=(),
                    double_annotate=True,
                    purposive=True,
                    sampling_rank_key=_stable_rank_key(unit.cluster_id, sampling_seed),
                )
            )
            selected_ids.add(unit.cluster_id)
    selected.sort(key=lambda s: (s.sampling_rank_key, s.cluster_id))
    return selected


def pair_score_bin(score: Optional[float]) -> str:
    if score is None:
        return "missing_score"
    for lo, hi, label in PAIR_SCORE_BINS:
        if lo <= score < hi:
            return label
    return "[0.90,1.00]"


def stable_pair_id(article_id_a: str, article_id_b: str) -> str:
    a, b = sorted((article_id_a, article_id_b))
    return hashlib.sha256(f"{a}|{b}".encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class PairFrameRow:
    pair_id: str
    article_id_a: str
    article_id_b: str
    exact_cluster_id_a: str
    exact_cluster_id_b: str
    title_similarity: Optional[float]
    body_similarity: Optional[float]
    max_similarity: Optional[float]
    publication_time_distance_hours: Optional[float]
    language_pair: str
    domain_pair: str
    score_bin: str
    candidate_at_current_threshold: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "article_id_a": self.article_id_a,
            "article_id_b": self.article_id_b,
            "exact_cluster_id_a": self.exact_cluster_id_a,
            "exact_cluster_id_b": self.exact_cluster_id_b,
            "title_similarity": self.title_similarity,
            "body_similarity": self.body_similarity,
            "max_similarity": self.max_similarity,
            "publication_time_distance_hours": self.publication_time_distance_hours,
            "language_pair": self.language_pair,
            "domain_pair": self.domain_pair,
            "score_bin": self.score_bin,
            "candidate_at_current_threshold": self.candidate_at_current_threshold,
        }


def build_pair_comparison_universe(
    articles: Sequence[ArticleTextRecordV1],
    clusters: Sequence[ExactDuplicateCluster],
    *,
    config: NearDuplicateConfig,
    current_title_threshold: float,
    current_body_threshold: float,
    max_pairs: Optional[int] = None,
) -> tuple[list[PairFrameRow], dict[str, Any]]:
    """Blocked pair universe for duplicate calibration (not all n choose 2)."""
    by_id = {a.article_id: a for a in articles}
    cluster_of = {}
    for c in clusters:
        for mid in c.member_article_ids:
            cluster_of[mid] = c.cluster_id

    # Compare only canonical articles to avoid exact-cluster internal pairs.
    canonical_ids = sorted({c.canonical_article_id for c in clusters})
    rows: list[PairFrameRow] = []
    for i, id_a in enumerate(canonical_ids):
        a = by_id[id_a]
        for id_b in canonical_ids[i + 1 :]:
            b = by_id[id_b]
            if cluster_of[id_a] == cluster_of[id_b]:
                continue
            if config.require_compatible_language:
                la = (a.language or "").casefold()
                lb = (b.language or "").casefold()
                if la and lb and la != lb:
                    continue
            # Time separation
            dist_hours: Optional[float] = None
            if a.published_at and b.published_at:
                dist_hours = abs((a.published_at - b.published_at).total_seconds()) / 3600.0
                if (
                    config.max_publication_distance_hours is not None
                    and dist_hours > config.max_publication_distance_hours
                ):
                    continue
            # Require some text
            if a.body is None and b.body is None and not a.title and not b.title:
                continue
            t_sim = jaccard(title_tokens(a.title), title_tokens(b.title))
            b_sim = None
            if a.body and b.body:
                b_sim = jaccard(
                    word_shingles(a.body, n=config.shingle_size),
                    word_shingles(b.body, n=config.shingle_size),
                )
            scores = [s for s in (t_sim, b_sim) if s is not None]
            max_sim = max(scores) if scores else None
            candidate = False
            if t_sim is not None and t_sim >= current_title_threshold:
                candidate = True
            if b_sim is not None and b_sim >= current_body_threshold:
                candidate = True
            aid_a, aid_b = sorted((id_a, id_b))
            ca, cb = by_id[aid_a], by_id[aid_b]
            rows.append(
                PairFrameRow(
                    pair_id=stable_pair_id(aid_a, aid_b),
                    article_id_a=aid_a,
                    article_id_b=aid_b,
                    exact_cluster_id_a=cluster_of[aid_a],
                    exact_cluster_id_b=cluster_of[aid_b],
                    title_similarity=t_sim,
                    body_similarity=b_sim,
                    max_similarity=max_sim,
                    publication_time_distance_hours=dist_hours,
                    language_pair=f"{ca.language or 'unk'}|{cb.language or 'unk'}",
                    domain_pair=f"{ca.domain}|{cb.domain}",
                    score_bin=pair_score_bin(max_sim),
                    candidate_at_current_threshold=candidate,
                )
            )
    rows.sort(key=lambda r: r.pair_id)
    if max_pairs is not None and len(rows) > max_pairs:
        # Deterministic cap by pair_id order (engineering safety).
        rows = rows[:max_pairs]
    blocking = {
        "languages": "same or compatible when both present",
        "max_publication_distance_hours": config.max_publication_distance_hours,
        "body_text": "pairs with neither title nor body excluded",
        "exact_cluster_exclusions": "no within-exact-cluster pairs; canonicals only",
        "domain_blocking": None,
        "entity_blocking": None,
        "max_pair_cap": max_pairs,
        "current_title_threshold": current_title_threshold,
        "current_body_threshold": current_body_threshold,
        "pair_count": len(rows),
        "recall_universe_statement": (
            "Duplicate recall is estimated only within this blocked comparison universe."
        ),
    }
    return rows, blocking


def sample_duplicate_pair_reviews(
    pair_frame: Sequence[PairFrameRow],
    *,
    candidate_target: int,
    below_threshold_target: int,
    sampling_seed: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Stratified sample of candidate and below-threshold pairs with known π."""
    by_bin: dict[str, list[PairFrameRow]] = defaultdict(list)
    for row in pair_frame:
        by_bin[row.score_bin].append(row)

    selected: list[dict[str, Any]] = []

    def take_from(
        pool: Sequence[PairFrameRow], n: int, *, label: str
    ) -> list[tuple[PairFrameRow, float, float]]:
        if n <= 0 or not pool:
            return []
        ordered = sorted(
            pool,
            key=lambda r: (_stable_rank_key(f"{label}|{r.pair_id}", sampling_seed), r.pair_id),
        )
        take = min(n, len(ordered))
        pi = take / len(pool)
        w = len(pool) / take
        return [(ordered[i], pi, w) for i in range(take)]

    candidates = [r for r in pair_frame if r.candidate_at_current_threshold]
    non_candidates = [r for r in pair_frame if not r.candidate_at_current_threshold]

    # Spread candidate sample across high bins; below-threshold across low bins.
    cand_bins = sorted({r.score_bin for r in candidates})
    non_bins = sorted({r.score_bin for r in non_candidates})
    per_cand = max(1, candidate_target // max(1, len(cand_bins))) if cand_bins else 0
    per_non = max(1, below_threshold_target // max(1, len(non_bins))) if non_bins else 0

    seen: set[str] = set()
    for b in cand_bins:
        for row, pi, w in take_from(by_bin[b], per_cand, label="cand"):
            if row.pair_id in seen or not row.candidate_at_current_threshold:
                continue
            if sum(1 for s in selected if s["candidate_at_current_threshold"]) >= candidate_target:
                break
            seen.add(row.pair_id)
            selected.append({**row.to_dict(), "sampling_probability": pi, "sampling_weight": w})
    for b in non_bins:
        for row, pi, w in take_from(by_bin[b], per_non, label="non"):
            if row.pair_id in seen or row.candidate_at_current_threshold:
                continue
            if (
                sum(1 for s in selected if not s["candidate_at_current_threshold"])
                >= below_threshold_target
            ):
                break
            seen.add(row.pair_id)
            selected.append({**row.to_dict(), "sampling_probability": pi, "sampling_weight": w})

    selected.sort(key=lambda r: r["pair_id"])
    manifest = {
        "candidate_target": candidate_target,
        "below_threshold_target": below_threshold_target,
        "candidate_selected": sum(1 for s in selected if s["candidate_at_current_threshold"]),
        "below_threshold_selected": sum(
            1 for s in selected if not s["candidate_at_current_threshold"]
        ),
        "total_selected": len(selected),
        "sampling_seed": sampling_seed,
        "notes": [
            "Pair inclusion probabilities recorded per bin draw.",
            "Pairs sharing an article are dependent; uncertainty should resample components.",
        ],
    }
    return selected, manifest

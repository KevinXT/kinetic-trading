"""Transparent non-ML relevance baselines for the semiconductor benchmark."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from news_data.article.models import ArticleTextRecordV1
from news_data.bigquery.seed_matching import DEFAULT_SEMICONDUCTOR_SEEDS, INDUSTRY_PHRASE
from news_data.entity.matching import EntityMatchV1, match_entities
from news_data.entity.models import EntityReferenceV1

BASELINE_A_VERSION = "baseline-naive-substring-v1"
BASELINE_B_VERSION = "baseline-token-safe-entity-v1"
BASELINE_C_VERSION = "baseline-strict-title-entity-v1"
BASELINE_D_VERSION = "baseline-strict-all-text-entity-v1"


@dataclass(frozen=True)
class BaselinePrediction:
    article_id: str
    baseline_id: str
    baseline_version: str
    predicted_binary_relevant: Optional[bool]
    abstained: bool
    matched_entity_ids: tuple[str, ...]
    matched_aliases: tuple[str, ...]
    match_fields: tuple[str, ...]
    rule_explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "article_id": self.article_id,
            "baseline_id": self.baseline_id,
            "baseline_version": self.baseline_version,
            "predicted_binary_relevant": self.predicted_binary_relevant,
            "abstained": self.abstained,
            "matched_entity_ids": list(self.matched_entity_ids),
            "matched_aliases": list(self.matched_aliases),
            "match_fields": list(self.match_fields),
            "rule_explanation": self.rule_explanation,
        }


def _blob(article: ArticleTextRecordV1) -> str:
    parts = [article.title]
    if article.description:
        parts.append(article.description)
    if article.body:
        parts.append(article.body)
    return "\n".join(parts)


def baseline_a_naive_substring(
    article: ArticleTextRecordV1,
    entities: Sequence[EntityReferenceV1],
) -> BaselinePrediction:
    """Unrestricted substring matching — audit failure mode only."""
    blob = _blob(article).casefold()
    hits: list[tuple[str, str]] = []
    for ent in entities:
        for alias in ent.aliases:
            if alias in blob:
                hits.append((ent.entity_id, alias))
    # Also naive industry phrases.
    for seed in DEFAULT_SEMICONDUCTOR_SEEDS:
        if seed.kind == INDUSTRY_PHRASE and seed.canonical in blob:
            hits.append(("industry_phrase", seed.canonical))
    if hits:
        return BaselinePrediction(
            article_id=article.article_id,
            baseline_id="A_naive_substring",
            baseline_version=BASELINE_A_VERSION,
            predicted_binary_relevant=True,
            abstained=False,
            matched_entity_ids=tuple(sorted({h[0] for h in hits if h[0] != "industry_phrase"})),
            matched_aliases=tuple(sorted({h[1] for h in hits})),
            match_fields=("all_text",),
            rule_explanation=(
                "Naive casefold substring match on title/description/body; "
                "demonstrates false-positive failure modes. Not a production rule."
            ),
        )
    return BaselinePrediction(
        article_id=article.article_id,
        baseline_id="A_naive_substring",
        baseline_version=BASELINE_A_VERSION,
        predicted_binary_relevant=False,
        abstained=False,
        matched_entity_ids=(),
        matched_aliases=(),
        match_fields=(),
        rule_explanation="No naive substring hit.",
    )


def baseline_b_token_safe(
    article: ArticleTextRecordV1,
    entities: Sequence[EntityReferenceV1],
    matches: Optional[Sequence[EntityMatchV1]] = None,
) -> BaselinePrediction:
    """Current token-safe entity matcher; ambiguous-only matches abstain."""
    found = list(matches) if matches is not None else match_entities(article, entities)
    if not found:
        return BaselinePrediction(
            article_id=article.article_id,
            baseline_id="B_token_safe_entity",
            baseline_version=BASELINE_B_VERSION,
            predicted_binary_relevant=False,
            abstained=False,
            matched_entity_ids=(),
            matched_aliases=(),
            match_fields=(),
            rule_explanation="No token-safe entity match.",
        )
    clear = [m for m in found if m.ambiguity_status == "clear"]
    if not clear:
        return BaselinePrediction(
            article_id=article.article_id,
            baseline_id="B_token_safe_entity",
            baseline_version=BASELINE_B_VERSION,
            predicted_binary_relevant=None,
            abstained=True,
            matched_entity_ids=tuple(sorted({m.entity_id for m in found})),
            matched_aliases=tuple(sorted({m.matched_alias for m in found})),
            match_fields=tuple(sorted({m.match_field for m in found})),
            rule_explanation="Only ambiguous aliases matched; abstaining.",
        )
    return BaselinePrediction(
        article_id=article.article_id,
        baseline_id="B_token_safe_entity",
        baseline_version=BASELINE_B_VERSION,
        predicted_binary_relevant=True,
        abstained=False,
        matched_entity_ids=tuple(sorted({m.entity_id for m in clear})),
        matched_aliases=tuple(sorted({m.matched_alias for m in clear})),
        match_fields=tuple(sorted({m.match_field for m in clear})),
        rule_explanation="At least one clear token-safe entity match anywhere in text.",
    )


def baseline_c_strict_title(
    article: ArticleTextRecordV1,
    entities: Sequence[EntityReferenceV1],
    matches: Optional[Sequence[EntityMatchV1]] = None,
) -> BaselinePrediction:
    found = list(matches) if matches is not None else match_entities(article, entities)
    title_matches = [m for m in found if m.match_field == "title" and m.ambiguity_status == "clear"]
    ambiguous_title = [
        m for m in found if m.match_field == "title" and m.ambiguity_status == "ambiguous_alias"
    ]
    if title_matches:
        return BaselinePrediction(
            article_id=article.article_id,
            baseline_id="C_strict_title_entity",
            baseline_version=BASELINE_C_VERSION,
            predicted_binary_relevant=True,
            abstained=False,
            matched_entity_ids=tuple(sorted({m.entity_id for m in title_matches})),
            matched_aliases=tuple(sorted({m.matched_alias for m in title_matches})),
            match_fields=("title",),
            rule_explanation="Clear token-safe entity match required in title.",
        )
    if ambiguous_title and not title_matches:
        return BaselinePrediction(
            article_id=article.article_id,
            baseline_id="C_strict_title_entity",
            baseline_version=BASELINE_C_VERSION,
            predicted_binary_relevant=None,
            abstained=True,
            matched_entity_ids=tuple(sorted({m.entity_id for m in ambiguous_title})),
            matched_aliases=tuple(sorted({m.matched_alias for m in ambiguous_title})),
            match_fields=("title",),
            rule_explanation="Ambiguous title alias only; abstaining.",
        )
    return BaselinePrediction(
        article_id=article.article_id,
        baseline_id="C_strict_title_entity",
        baseline_version=BASELINE_C_VERSION,
        predicted_binary_relevant=False,
        abstained=False,
        matched_entity_ids=(),
        matched_aliases=(),
        match_fields=(),
        rule_explanation="No clear title entity match.",
    )


def baseline_d_strict_all_text(
    article: ArticleTextRecordV1,
    entities: Sequence[EntityReferenceV1],
    matches: Optional[Sequence[EntityMatchV1]] = None,
) -> BaselinePrediction:
    """Strict token-safe match on any available text field (optional baseline)."""
    pred = baseline_b_token_safe(article, entities, matches=matches)
    return BaselinePrediction(
        article_id=pred.article_id,
        baseline_id="D_strict_all_text_entity",
        baseline_version=BASELINE_D_VERSION,
        predicted_binary_relevant=pred.predicted_binary_relevant,
        abstained=pred.abstained,
        matched_entity_ids=pred.matched_entity_ids,
        matched_aliases=pred.matched_aliases,
        match_fields=pred.match_fields,
        rule_explanation=(
            "Strict token-safe entity match on available title/description/body "
            "(same decision rule as B; separate baseline id for reporting)."
        ),
    )


def run_all_baselines(
    articles: Sequence[ArticleTextRecordV1],
    entities: Sequence[EntityReferenceV1],
    matches: Sequence[EntityMatchV1],
) -> list[BaselinePrediction]:
    by_article: dict[str, list[EntityMatchV1]] = {}
    for m in matches:
        by_article.setdefault(m.article_id, []).append(m)
    out: list[BaselinePrediction] = []
    for article in sorted(articles, key=lambda a: a.article_id):
        am = by_article.get(article.article_id, [])
        out.append(baseline_a_naive_substring(article, entities))
        out.append(baseline_b_token_safe(article, entities, matches=am))
        out.append(baseline_c_strict_title(article, entities, matches=am))
        out.append(baseline_d_strict_all_text(article, entities, matches=am))
    return out

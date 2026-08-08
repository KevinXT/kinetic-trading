"""Deterministic, token-safe entity alias matching over article fields.

Reuses the boundary semantics from ``kinetic.data.catalog.seeds`` so
``intel`` does not match ``intelligence``, ``amd`` does not match inside larger
words, and ``micron`` does not match ``omicron``. A match does not imply
company centrality.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

from kinetic.data.catalog.seeds import KNOWN_AMBIGUOUS_ALIASES
from kinetic.data.schemas.entities import EntityReferenceV1
from kinetic.data.schemas.news import ArticleTextRecordV1
from kinetic.data.serialization import to_json_value

MATCHING_RULE_VERSION = "entity-alias-match-v1"
MATCH_FIELDS = ("title", "description", "body")


@dataclass(frozen=True)
class EntityMatchV1:
    article_id: str
    entity_id: str
    matched_alias: str
    match_field: str
    start: int
    end: int
    matching_rule_version: str
    ambiguity_status: str
    schema_version: str = "entity-match-v1"

    def __post_init__(self) -> None:
        if self.match_field not in MATCH_FIELDS:
            raise ValueError(f"unsupported match_field {self.match_field!r}")
        if self.start < 0 or self.end < self.start:
            raise ValueError("invalid match span")
        if self.ambiguity_status not in {"clear", "ambiguous_alias"}:
            raise ValueError(f"unsupported ambiguity_status {self.ambiguity_status!r}")

    def to_dict(self) -> dict[str, Any]:
        payload = to_json_value(self)
        if not isinstance(payload, dict):
            raise TypeError("EntityMatchV1 must serialize to a dict")
        return payload


def _alias_pattern(alias: str) -> re.Pattern[str]:
    # Alias is already normalized to [a-z0-9 ] via EntityReferenceV1.
    escaped = re.escape(alias)
    return re.compile(rf"(^|[^a-z0-9])({escaped})([^a-z0-9]|$)", re.IGNORECASE)


def _field_text(article: ArticleTextRecordV1, field_name: str) -> Optional[str]:
    if field_name == "title":
        return article.title
    if field_name == "description":
        return article.description
    if field_name == "body":
        return article.body
    return None


def _ambiguity_status(alias: str, entity: EntityReferenceV1) -> str:
    if alias in entity.ambiguous_aliases or alias in KNOWN_AMBIGUOUS_ALIASES:
        return "ambiguous_alias"
    return "clear"


def match_entities(
    article: ArticleTextRecordV1,
    entities: Sequence[EntityReferenceV1],
    *,
    fields: Sequence[str] = MATCH_FIELDS,
) -> list[EntityMatchV1]:
    """Return deterministic entity matches for one article.

    Matches are ordered by (match_field order, start, end, entity_id, alias).
    Overlapping aliases on the same field: longer alias wins; ties keep both
    only when they refer to different entities.
    """
    ordered_fields = [f for f in fields if f in MATCH_FIELDS]
    matches: list[EntityMatchV1] = []

    for field_name in ordered_fields:
        text = _field_text(article, field_name)
        if not text:
            continue
        haystack = text
        # Collect candidate spans then resolve overlaps preferring longer aliases.
        candidates: list[EntityMatchV1] = []
        for entity in entities:
            for alias in entity.aliases:
                pattern = _alias_pattern(alias)
                for m in pattern.finditer(haystack):
                    # Group 2 is the alias; start/end relative to original text.
                    start = m.start(2)
                    end = m.end(2)
                    candidates.append(
                        EntityMatchV1(
                            article_id=article.article_id,
                            entity_id=entity.entity_id,
                            matched_alias=alias,
                            match_field=field_name,
                            start=start,
                            end=end,
                            matching_rule_version=MATCHING_RULE_VERSION,
                            ambiguity_status=_ambiguity_status(alias, entity),
                        )
                    )
        matches.extend(_resolve_overlaps(candidates))

    matches.sort(
        key=lambda m: (
            MATCH_FIELDS.index(m.match_field),
            m.start,
            m.end,
            m.entity_id,
            m.matched_alias,
        )
    )
    return matches


def _resolve_overlaps(candidates: Sequence[EntityMatchV1]) -> list[EntityMatchV1]:
    if not candidates:
        return []
    ordered = sorted(
        candidates,
        key=lambda m: (-(m.end - m.start), m.start, m.entity_id, m.matched_alias),
    )
    accepted: list[EntityMatchV1] = []
    for cand in ordered:
        conflict = False
        for prev in accepted:
            if cand.entity_id == prev.entity_id and not (
                cand.end <= prev.start or cand.start >= prev.end
            ):
                # Same entity overlapping span: keep longer (already ordered).
                conflict = True
                break
            if cand.entity_id != prev.entity_id and not (
                cand.end <= prev.start or cand.start >= prev.end
            ):
                # Different entities may share a span only if aliases differ;
                # still keep both for multi-entity audit.
                continue
        if not conflict:
            # Drop same-entity shorter overlaps already handled; also drop exact
            # duplicate tuples.
            dup = any(
                p.entity_id == cand.entity_id
                and p.start == cand.start
                and p.end == cand.end
                and p.matched_alias == cand.matched_alias
                for p in accepted
            )
            if not dup:
                accepted.append(cand)
    accepted.sort(key=lambda m: (m.start, m.end, m.entity_id, m.matched_alias))
    return accepted


def match_entities_many(
    articles: Iterable[ArticleTextRecordV1],
    entities: Sequence[EntityReferenceV1],
) -> list[EntityMatchV1]:
    out: list[EntityMatchV1] = []
    for article in sorted(articles, key=lambda a: a.article_id):
        out.extend(match_entities(article, entities))
    return out

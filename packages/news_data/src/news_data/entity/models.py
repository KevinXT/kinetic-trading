"""Versioned entity-reference records for research matching."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Optional, Sequence

from news_data.article.serialize import to_json_value
from news_data.bigquery.seed_matching import normalize_seed

SCHEMA_VERSION = "entity-reference-v1"
ENTITY_TYPES = frozenset({"public_company", "private_company", "organization", "other"})


def _req_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_date(value: Optional[date | str], field_name: str) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a date, not datetime")
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


@dataclass(frozen=True)
class EntityReferenceV1:
    entity_id: str
    legal_name: str
    display_name: str
    entity_type: str
    aliases: tuple[str, ...]
    ambiguous_aliases: tuple[str, ...]
    ticker: Optional[str]
    exchange: Optional[str]
    cik: Optional[str]
    country: Optional[str]
    valid_from: Optional[date]
    valid_to: Optional[date]
    reference_source: str
    reference_version: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_id", _req_text(self.entity_id, "entity_id"))
        object.__setattr__(self, "legal_name", _req_text(self.legal_name, "legal_name"))
        object.__setattr__(self, "display_name", _req_text(self.display_name, "display_name"))
        if self.entity_type not in ENTITY_TYPES:
            raise ValueError(
                f"unsupported entity_type {self.entity_type!r}; "
                f"expected one of {sorted(ENTITY_TYPES)}"
            )
        aliases = tuple(normalize_seed(a) for a in self.aliases if str(a).strip())
        if not aliases:
            raise ValueError("aliases must contain at least one normalized alias")
        # Longer aliases first aids span preference at match time.
        aliases = tuple(sorted(set(aliases), key=lambda a: (-len(a), a)))
        object.__setattr__(self, "aliases", aliases)
        ambiguous = tuple(normalize_seed(a) for a in self.ambiguous_aliases if str(a).strip())
        object.__setattr__(
            self, "ambiguous_aliases", tuple(sorted(set(ambiguous), key=lambda a: (-len(a), a)))
        )
        for name in ("ticker", "exchange", "cik", "country"):
            raw = getattr(self, name)
            if raw is None:
                continue
            cleaned = str(raw).strip()
            object.__setattr__(self, name, cleaned if cleaned else None)
        object.__setattr__(self, "valid_from", _optional_date(self.valid_from, "valid_from"))
        object.__setattr__(self, "valid_to", _optional_date(self.valid_to, "valid_to"))
        object.__setattr__(
            self, "reference_source", _req_text(self.reference_source, "reference_source")
        )
        object.__setattr__(
            self, "reference_version", _req_text(self.reference_version, "reference_version")
        )
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {SCHEMA_VERSION!r}, got {self.schema_version!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        payload = to_json_value(self)
        if not isinstance(payload, dict):
            raise TypeError("EntityReferenceV1 must serialize to a dict")
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EntityReferenceV1:
        return cls(
            entity_id=str(data["entity_id"]),
            legal_name=str(data["legal_name"]),
            display_name=str(data["display_name"]),
            entity_type=str(data.get("entity_type", "public_company")),
            aliases=tuple(data.get("aliases") or ()),
            ambiguous_aliases=tuple(data.get("ambiguous_aliases") or ()),
            ticker=(str(data["ticker"]) if data.get("ticker") else None),
            exchange=(str(data["exchange"]) if data.get("exchange") else None),
            cik=(str(data["cik"]) if data.get("cik") else None),
            country=(str(data["country"]) if data.get("country") else None),
            valid_from=_optional_date(data.get("valid_from"), "valid_from"),
            valid_to=_optional_date(data.get("valid_to"), "valid_to"),
            reference_source=str(data["reference_source"]),
            reference_version=str(data["reference_version"]),
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
        )


def dedupe_entity_ids(entities: Sequence[EntityReferenceV1]) -> list[EntityReferenceV1]:
    seen: dict[str, EntityReferenceV1] = {}
    for ent in entities:
        if ent.entity_id in seen:
            raise ValueError(f"duplicate entity_id: {ent.entity_id}")
        seen[ent.entity_id] = ent
    return list(seen.values())

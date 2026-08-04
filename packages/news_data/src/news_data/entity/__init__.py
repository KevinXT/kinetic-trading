"""Entity reference records and deterministic alias matching."""

from news_data.entity.matching import (
    MATCHING_RULE_VERSION,
    EntityMatchV1,
    match_entities,
)
from news_data.entity.models import ENTITY_TYPES, SCHEMA_VERSION, EntityReferenceV1
from news_data.entity.reference import (
    SEMICONDUCTOR_FIXTURE_VERSION,
    default_semiconductor_entities,
    load_entity_references,
)

__all__ = [
    "ENTITY_TYPES",
    "MATCHING_RULE_VERSION",
    "SCHEMA_VERSION",
    "SEMICONDUCTOR_FIXTURE_VERSION",
    "EntityMatchV1",
    "EntityReferenceV1",
    "default_semiconductor_entities",
    "load_entity_references",
    "match_entities",
]

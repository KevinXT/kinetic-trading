"""Load and validate versioned topic→instrument mappings from YAML.

Mappings are researcher-defined assumptions, not objective truth. They are
versioned, validated for internal consistency, and ordered deterministically so a
rebuild is byte-stable. A current ticker list is not a survivorship-bias-free
historical universe; that limitation is documented, not silently ignored.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from kinetic.data.schemas.research import TopicInstrumentMapping

DEFAULT_MAPPINGS_PATH = "configs/research/topic_instrument_mappings.yaml"


def _parse_date(value: object, field_name: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        return date.fromisoformat(value.strip())
    raise ValueError(f"{field_name} must be an ISO date string or null")


def load_topic_instrument_mappings(
    path: str | Path = DEFAULT_MAPPINGS_PATH,
) -> tuple[str, tuple[TopicInstrumentMapping, ...]]:
    """Return ``(mapping_version, mappings)`` loaded and validated from YAML.

    Raises ``ValueError`` on duplicate (topic, instrument) pairs, inappropriate
    benchmark self-reference, or conflicting active windows for the same
    (topic, instrument).
    """
    mappings_path = Path(path)
    if not mappings_path.is_file():
        raise FileNotFoundError(f"topic-instrument mappings not found: {mappings_path}")
    raw = yaml.safe_load(mappings_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("topic-instrument mappings must be a mapping")

    mapping_version = str(raw.get("mapping_version", "")).strip()
    if not mapping_version:
        raise ValueError("topic-instrument mappings require a non-empty mapping_version")

    topics = raw.get("topics") or {}
    if not isinstance(topics, dict) or not topics:
        raise ValueError("topic-instrument mappings require a non-empty 'topics' mapping")

    result: list[TopicInstrumentMapping] = []
    seen_keys: set[tuple[str, str]] = set()

    for topic, block in topics.items():
        if not isinstance(block, dict):
            raise ValueError(f"topic {topic!r} must map to a mapping with 'instruments'")
        instruments = block.get("instruments") or []
        if not isinstance(instruments, list) or not instruments:
            raise ValueError(f"topic {topic!r} requires a non-empty 'instruments' list")
        for entry in instruments:
            if not isinstance(entry, dict):
                raise ValueError(f"topic {topic!r} has a non-mapping instrument entry")
            mapping = TopicInstrumentMapping(
                mapping_version=mapping_version,
                topic=str(topic),
                instrument_id=str(entry["instrument_id"]),
                symbol=str(entry["symbol"]),
                relationship=str(entry["relationship"]),
                exposure_type=str(entry["exposure_type"]),
                weight=float(entry.get("weight", 1.0)),
                benchmark_symbol=str(entry["benchmark_symbol"]),
                sector_benchmark_symbol=(
                    str(entry["sector_benchmark_symbol"])
                    if entry.get("sector_benchmark_symbol")
                    else None
                ),
                valid_from=_parse_date(entry.get("valid_from"), "valid_from"),
                valid_to=_parse_date(entry.get("valid_to"), "valid_to"),
                research_rationale=str(entry.get("research_rationale", "")),
            )
            key = (mapping.topic, mapping.instrument_id)
            if key in seen_keys:
                raise ValueError(
                    f"duplicate topic-instrument mapping for {key!r} in {mapping_version}"
                )
            seen_keys.add(key)
            # A broad-market/benchmark instrument may legitimately be its own
            # benchmark; a company/sector exposure using itself as the broad
            # benchmark is almost always a mistake.
            if mapping.benchmark_symbol == mapping.symbol and mapping.relationship not in {
                "broad_market",
                "benchmark",
            }:
                raise ValueError(
                    f"benchmark self-reference for {key!r}: {mapping.symbol} cannot be its own "
                    f"broad benchmark with relationship {mapping.relationship!r}"
                )
            result.append(mapping)

    # Deterministic ordering: topic, then instrument_id.
    result.sort(key=lambda m: (m.topic, m.instrument_id))
    return mapping_version, tuple(result)


def active_mappings_for_topic(
    mappings: tuple[TopicInstrumentMapping, ...],
    topic: str,
    session_date: date,
) -> list[TopicInstrumentMapping]:
    """Mappings for ``topic`` active on ``session_date`` (deterministic order)."""
    return [m for m in mappings if m.topic == topic and m.is_active_on(session_date)]

"""Entity-reference loading and the semiconductor research fixture.

Reconciles aliases with ``DEFAULT_SEMICONDUCTOR_SEEDS`` in :mod:`kinetic.data.catalog.seeds`.
This fixture is NOT a complete industry universe. Preferred future US
ticker/exchange/CIK source: SEC company_tickers_exchange.json / EDGAR metadata
(not downloaded live in offline tests).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from kinetic.data.catalog.seeds import (
    COMPANY_ALIAS,
    DEFAULT_SEMICONDUCTOR_SEEDS,
    KNOWN_AMBIGUOUS_ALIASES,
)
from kinetic.data.schemas.entities import EntityReferenceV1, dedupe_entity_ids

SEMICONDUCTOR_FIXTURE_VERSION = "semiconductor-entity-fixture-v1"
SEMICONDUCTOR_REFERENCE_SOURCE = (
    "kinetic_fixture+seed_matching_defaults;SEC_ticker_fields_stub_not_live"
)


def _aliases_for_canonical(canonical: str) -> tuple[str, ...]:
    for seed in DEFAULT_SEMICONDUCTOR_SEEDS:
        if seed.canonical == canonical and seed.kind == COMPANY_ALIAS:
            return tuple(seed.aliases)
    return (canonical,)


def _ambiguous_for(aliases: Sequence[str]) -> tuple[str, ...]:
    return tuple(a for a in aliases if a in KNOWN_AMBIGUOUS_ALIASES)


def default_semiconductor_entities() -> list[EntityReferenceV1]:
    """Small semiconductor reference fixture for offline benchmarks.

    Companies already present in the theme experiment. CIK/ticker values are
    research stubs with explicit provenance — not a live SEC download.
    """
    specs: list[dict[str, Any]] = [
        {
            "entity_id": "ent_nvidia",
            "legal_name": "NVIDIA Corporation",
            "display_name": "NVIDIA",
            "canonical": "nvidia",
            "ticker": "NVDA",
            "exchange": "NASDAQ",
            "cik": "0001045810",
            "country": "US",
        },
        {
            "entity_id": "ent_intel",
            "legal_name": "Intel Corporation",
            "display_name": "Intel",
            "canonical": "intel",
            "ticker": "INTC",
            "exchange": "NASDAQ",
            "cik": "0000050863",
            "country": "US",
        },
        {
            "entity_id": "ent_amd",
            "legal_name": "Advanced Micro Devices, Inc.",
            "display_name": "AMD",
            "canonical": "amd",
            "ticker": "AMD",
            "exchange": "NASDAQ",
            "cik": "0000002488",
            "country": "US",
        },
        {
            "entity_id": "ent_tsmc",
            "legal_name": "Taiwan Semiconductor Manufacturing Company Limited",
            "display_name": "TSMC",
            "canonical": "tsmc",
            "ticker": "TSM",
            "exchange": "NYSE",
            "cik": "0001046179",
            "country": "TW",
        },
        {
            "entity_id": "ent_micron",
            "legal_name": "Micron Technology, Inc.",
            "display_name": "Micron",
            "canonical": "micron",
            "ticker": "MU",
            "exchange": "NASDAQ",
            "cik": "0000723125",
            "country": "US",
        },
        {
            "entity_id": "ent_qualcomm",
            "legal_name": "QUALCOMM Incorporated",
            "display_name": "Qualcomm",
            "canonical": "qualcomm",
            "ticker": "QCOM",
            "exchange": "NASDAQ",
            "cik": "0000804329",
            "country": "US",
        },
        {
            "entity_id": "ent_broadcom",
            "legal_name": "Broadcom Inc.",
            "display_name": "Broadcom",
            "canonical": "broadcom",
            "ticker": "AVGO",
            "exchange": "NASDAQ",
            "cik": "0001730168",
            "country": "US",
        },
        {
            "entity_id": "ent_asml",
            "legal_name": "ASML Holding N.V.",
            "display_name": "ASML",
            "canonical": "asml",
            "ticker": "ASML",
            "exchange": "NASDAQ",
            "cik": "0000937966",
            "country": "NL",
        },
        {
            "entity_id": "ent_samsung_electronics",
            "legal_name": "Samsung Electronics Co., Ltd.",
            "display_name": "Samsung Electronics",
            "canonical": "samsung electronics",
            "ticker": None,
            "exchange": None,
            "cik": None,
            "country": "KR",
        },
    ]
    entities: list[EntityReferenceV1] = []
    for spec in specs:
        aliases = _aliases_for_canonical(str(spec["canonical"]))
        entities.append(
            EntityReferenceV1(
                entity_id=str(spec["entity_id"]),
                legal_name=str(spec["legal_name"]),
                display_name=str(spec["display_name"]),
                entity_type="public_company",
                aliases=aliases,
                ambiguous_aliases=_ambiguous_for(aliases),
                ticker=spec.get("ticker"),
                exchange=spec.get("exchange"),
                cik=spec.get("cik"),
                country=spec.get("country"),
                valid_from=None,
                valid_to=None,
                reference_source=SEMICONDUCTOR_REFERENCE_SOURCE,
                reference_version=SEMICONDUCTOR_FIXTURE_VERSION,
            )
        )
    return dedupe_entity_ids(entities)


def load_entity_references(path: str | Path | None = None) -> list[EntityReferenceV1]:
    """Load entity references from JSON/JSONL, or the default fixture when path is None."""
    if path is None:
        return default_semiconductor_entities()
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    stripped = text.lstrip()
    rows: list[Mapping[str, Any]]
    if stripped.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError(f"{p} must contain a JSON array")
        rows = [r for r in data if isinstance(r, dict)]
    else:
        rows = []
        for line in text.splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
    entities = [EntityReferenceV1.from_dict(r) for r in rows]
    return dedupe_entity_ids(entities)

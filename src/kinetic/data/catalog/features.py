"""Machine-readable feature catalog.

Every field emitted by the builder belongs to exactly one category. The catalog
is the single source of truth for a field's formula, source, window, availability
rule, null behavior, leakage risk, and implementation status. The builder can emit
the catalog as an artifact so a reviewer can audit provenance without reading code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# The eight allowed categories (see design memo §7 / task §7).
CATEGORIES = frozenset(
    {
        "identity",
        "lineage",
        "availability",
        "input",
        "contemporaneous",
        "target",
        "quality",
        "diagnostic",
    }
)

IMPLEMENTATION_STATUSES = frozenset({"core_v1", "v1_optional", "future", "unsupported"})
HYPOTHESIS_CLASSES = frozenset({"confirmatory", "exploratory"})

DEFAULT_CATALOG_PATH = "configs/research/news_market_feature_catalog.yaml"


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    label: str
    category: str
    formula: str
    required_source_fields: tuple[str, ...]
    window: str
    includes_current: bool
    availability_rule: str
    null_behavior: str
    minimum_history: int
    leakage_risk: str
    interpretation: str
    units: str
    version: str
    motivation: str
    implementation_status: str
    hypothesis_class: str | None
    hypothesis_family: str | None
    hypothesis_id: str | None

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"feature {self.name!r} has unknown category {self.category!r}")
        if self.implementation_status not in IMPLEMENTATION_STATUSES:
            raise ValueError(
                f"feature {self.name!r} has unknown implementation_status "
                f"{self.implementation_status!r}"
            )
        if self.minimum_history < 0:
            raise ValueError(f"feature {self.name!r} minimum_history must be >= 0")
        if self.hypothesis_class is not None and self.hypothesis_class not in HYPOTHESIS_CLASSES:
            raise ValueError(
                f"feature {self.name!r} has unknown hypothesis_class " f"{self.hypothesis_class!r}"
            )
        hypothesis_values = (
            self.hypothesis_class,
            self.hypothesis_family,
            self.hypothesis_id,
        )
        if any(value is not None for value in hypothesis_values) and not all(
            value is not None and value.strip() for value in hypothesis_values
        ):
            raise ValueError(
                f"feature {self.name!r} hypothesis metadata must set class, family, and id"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "category": self.category,
            "formula": self.formula,
            "required_source_fields": list(self.required_source_fields),
            "window": self.window,
            "includes_current": self.includes_current,
            "availability_rule": self.availability_rule,
            "null_behavior": self.null_behavior,
            "minimum_history": self.minimum_history,
            "leakage_risk": self.leakage_risk,
            "interpretation": self.interpretation,
            "units": self.units,
            "version": self.version,
            "motivation": self.motivation,
            "implementation_status": self.implementation_status,
            "hypothesis_class": self.hypothesis_class,
            "hypothesis_family": self.hypothesis_family,
            "hypothesis_id": self.hypothesis_id,
        }


@dataclass(frozen=True)
class FeatureCatalog:
    catalog_version: str
    features: tuple[FeatureDefinition, ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for feature in self.features:
            if feature.name in seen:
                raise ValueError(f"duplicate feature name in catalog: {feature.name!r}")
            seen.add(feature.name)

    def by_name(self, name: str) -> FeatureDefinition | None:
        for feature in self.features:
            if feature.name == name:
                return feature
        return None

    def by_category(self, category: str) -> tuple[FeatureDefinition, ...]:
        return tuple(f for f in self.features if f.category == category)

    def names_for_category(self, category: str) -> frozenset[str]:
        return frozenset(f.name for f in self.by_category(category))

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog_version": self.catalog_version,
            "feature_count": len(self.features),
            "categories": sorted(CATEGORIES),
            "features": [f.to_dict() for f in sorted(self.features, key=lambda x: x.name)],
        }


def load_feature_catalog(path: str | Path = DEFAULT_CATALOG_PATH) -> FeatureCatalog:
    """Load and validate the feature catalog from YAML."""
    catalog_path = Path(path)
    if not catalog_path.is_file():
        raise FileNotFoundError(f"feature catalog not found: {catalog_path}")
    raw = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("feature catalog must be a mapping")
    version = str(raw.get("catalog_version", "")).strip()
    if not version:
        raise ValueError("feature catalog requires a non-empty catalog_version")
    raw_features = raw.get("features") or []
    if not isinstance(raw_features, list) or not raw_features:
        raise ValueError("feature catalog requires a non-empty 'features' list")
    features: list[FeatureDefinition] = []
    for entry in raw_features:
        if not isinstance(entry, dict):
            raise ValueError("each feature catalog entry must be a mapping")
        features.append(
            FeatureDefinition(
                name=str(entry["name"]),
                label=str(entry.get("label", entry["name"])),
                category=str(entry["category"]),
                formula=str(entry.get("formula", "")),
                required_source_fields=tuple(
                    str(x) for x in (entry.get("required_source_fields") or [])
                ),
                window=str(entry.get("window", "point")),
                includes_current=bool(entry.get("includes_current", False)),
                availability_rule=str(entry.get("availability_rule", "")),
                null_behavior=str(entry.get("null_behavior", "")),
                minimum_history=int(entry.get("minimum_history", 0)),
                leakage_risk=str(entry.get("leakage_risk", "")),
                interpretation=str(entry.get("interpretation", "")),
                units=str(entry.get("units", "")),
                version=str(entry.get("version", version)),
                motivation=str(entry.get("motivation", "")),
                implementation_status=str(entry.get("implementation_status", "core_v1")),
                hypothesis_class=(
                    str(entry["hypothesis_class"]) if entry.get("hypothesis_class") else None
                ),
                hypothesis_family=(
                    str(entry["hypothesis_family"]) if entry.get("hypothesis_family") else None
                ),
                hypothesis_id=(str(entry["hypothesis_id"]) if entry.get("hypothesis_id") else None),
            )
        )
    return FeatureCatalog(catalog_version=version, features=tuple(features))

"""
Theme bundles: map a research topic (e.g. ``inflation_rates``) to a transparent,
testable set of GDELT theme codes.

A single theme code is often too narrow — ``ECON_INFLATION`` alone misses closely
related coverage (interest rates, cost of living, central banks). A *bundle*
groups several related codes under one name so a topic can be expressed as a set
of codes rather than a single guess.

Bundles are a research convenience, not a final trading signal. The codes in a
bundle should be *validated and refined* with the theme-discovery debug path
(``build_gdelt_theme_discovery_query``), which shows what GDELT actually contains
in a given window.

Bundle file shape (``configs/gdelt_theme_bundles.yaml``)::

    theme_bundles:
      inflation_rates:
        description: "..."
        themes:
          - ECON_INFLATION
          - ECON_INTEREST_RATES
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence

import yaml
from common.errors import ConfigError

DEFAULT_THEME_BUNDLES_PATH = "configs/gdelt_theme_bundles.yaml"


def load_theme_bundles(path: str | Path = DEFAULT_THEME_BUNDLES_PATH) -> Dict[str, List[str]]:
    """
    Load ``configs/gdelt_theme_bundles.yaml`` into ``{bundle_name: [themes]}``.

    Each bundle spec may be either a mapping with a ``themes`` list (preferred,
    allows a ``description``) or a bare list of theme codes. Theme codes are
    stripped; blanks are dropped.

    Raises:
        ConfigError: missing file, wrong top-level shape, or a malformed bundle.
    """
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"theme bundle file not found or not a regular file: {p}")

    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"theme bundle file {p} must be a mapping, got {type(data).__name__}.")

    raw = data.get("theme_bundles", {}) or {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"theme bundle file {p}: 'theme_bundles' must be a mapping, got {type(raw).__name__}."
        )

    bundles: Dict[str, List[str]] = {}
    for name, spec in raw.items():
        if isinstance(spec, dict):
            themes = spec.get("themes", [])
        elif isinstance(spec, list):
            themes = spec
        else:
            raise ConfigError(
                f"theme bundle {name!r} must be a mapping with 'themes' or a list, "
                f"got {type(spec).__name__}."
            )
        clean = [str(t).strip() for t in (themes or []) if str(t).strip()]
        bundles[str(name)] = clean
    return bundles


def get_bundle_themes(
    bundle_name: str, path: str | Path = DEFAULT_THEME_BUNDLES_PATH
) -> List[str]:
    """
    Return the theme codes for ``bundle_name``.

    Raises:
        ConfigError: unknown bundle name or a bundle with no themes.
    """
    bundles = load_theme_bundles(path)
    if bundle_name not in bundles:
        raise ConfigError(
            f"unknown theme_bundle {bundle_name!r}; available bundles: {sorted(bundles)}."
        )
    themes = bundles[bundle_name]
    if not themes:
        raise ConfigError(f"theme_bundle {bundle_name!r} resolves to no theme codes.")
    return themes


def resolve_query_terms(
    *,
    query_terms: Optional[Sequence[str]],
    theme_bundle: Optional[str],
    bundles_path: str | Path = DEFAULT_THEME_BUNDLES_PATH,
) -> List[str]:
    """
    Resolve the effective query terms from explicit ``query_terms`` and/or a
    named ``theme_bundle``.

    Behavior (chosen for flexibility; documented in the README):

    - Only ``query_terms`` -> returned as-is (existing behavior preserved).
    - Only ``theme_bundle`` -> the bundle's themes are used as query terms.
    - Both -> **merged, de-duplicated, order-preserving**: explicit terms come
      first, then bundle themes not already present (case-insensitive de-dupe).

    This never silently drops a term and lets an operator pin extra codes on top
    of a bundle.
    """
    explicit = [t.strip() for t in (query_terms or []) if isinstance(t, str) and t.strip()]
    if not theme_bundle:
        return explicit

    bundle_themes = get_bundle_themes(str(theme_bundle), bundles_path)

    merged: List[str] = list(explicit)
    seen = {t.lower() for t in merged}
    for theme in bundle_themes:
        if theme.lower() not in seen:
            merged.append(theme)
            seen.add(theme.lower())
    return merged

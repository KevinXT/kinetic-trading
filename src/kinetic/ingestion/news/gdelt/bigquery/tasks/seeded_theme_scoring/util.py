"""Small parsing/validation helpers for seeded theme scoring."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from kinetic.core.errors import PipelineError

JsonDict = Dict[str, Any]


def _neg(v):
    return -v if isinstance(v, (int, float)) else 0.0


# ── parsing helpers ──────────────────────────────────────────────────────────


def _struct_array(value: Any) -> List[Dict[str, Any]]:
    """Coerce a BigQuery ARRAY<STRUCT> cell into a list of dicts."""
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(v) for v in value if isinstance(v, dict)]


def _struct_n(struct: Dict[str, Any]) -> Optional[int]:
    """Count field of an APPROX_TOP_COUNT struct (``count``; ``n`` in fixtures)."""
    val = struct.get("count")
    if val is None:
        val = struct.get("n")
    return _int_or_none(val)


def _seed_hits(value: Any) -> List[Tuple[str, int]]:
    """Parse the ``seed_hits`` array of ``{seed, n}`` structs into tuples."""
    out: List[Tuple[str, int]] = []
    if not isinstance(value, (list, tuple)):
        return out
    for item in value:
        if not isinstance(item, dict):
            continue
        seed = str(item.get("seed") or "").strip()
        n = _int_or_none(item.get("n")) or 0
        if seed:
            out.append((seed, n))
    return out


def _screen_value(exact: Optional[float], approx: Optional[float]) -> Tuple[Optional[float], str]:
    """Pick the screening share and record its provenance.

    Exact is always preferred; the approximate sketch is only a fallback. The
    returned provenance is one of ``exact`` / ``approximate`` / ``unavailable``
    and is never claimed to be exact when only the sketch was available.
    """
    if exact is not None:
        return exact, "exact"
    if approx is not None:
        return approx, "approximate"
    return None, "unavailable"


def _test_distribution(rows: List[JsonDict]) -> Dict[str, int]:
    """Count candidates by selected hypothesis test (for the summary artifact)."""
    dist: Dict[str, int] = {}
    for r in rows:
        name = str(r.get("hypothesis_test") or "unknown")
        dist[name] = dist.get(name, 0) + 1
    return dist


def _concentration_coverage(rows: List[JsonDict]) -> JsonDict:
    """Exact/approx concentration coverage counts across the scored family."""

    def has(key: str) -> int:
        return sum(1 for r in rows if r.get(key) is not None)

    n = len(rows)
    return {
        "total_candidate_count": n,
        "candidates_with_approx_source_concentration": has("approx_top_source_share"),
        "candidates_with_exact_source_concentration": has("exact_top_source_share"),
        "candidates_with_approx_day_concentration": has("approx_top_day_share"),
        "candidates_with_exact_day_concentration": has("exact_top_day_share"),
        # seed concentration is exact-only (per-seed COUNTIF), no sketch exists
        "candidates_with_approx_seed_concentration": 0,
        "candidates_with_exact_seed_concentration": has("exact_top_seed_share"),
        "source_screen_uses_exact_when_available": True,
        "day_screen_uses_exact_when_available": True,
        "seed_screen_uses_exact_when_available": True,
        "exact_concentration_selection_rule": (
            "exact metrics are computed for every candidate (source/day via extra "
            "aggregation grains, seed via per-seed COUNTIF, all in the one base "
            "scan); screening prefers exact and only falls back to the "
            "APPROX_TOP_COUNT sketch when an exact value is unavailable."
        ),
    }


def _sample_records(value: Any) -> List[JsonDict]:
    """Parse the hash-sampled ``sample_records`` STRUCT array into plain dicts.

    Carries the GKG ``doc_id`` (DocumentIdentifier / source URL) so record-level
    evidence is independently auditable.
    """
    out: List[JsonDict] = []
    for item in _struct_array(value):
        rec_id = str(item.get("rec_id") or "").strip()
        if not rec_id:
            continue
        seeds = item.get("matched_seeds")
        doc_id = item.get("doc_id")
        out.append(
            {
                "rec_id": rec_id,
                "source": (str(item.get("source")).strip() if item.get("source") else None),
                "day": _int_or_none(item.get("day")),
                "doc_id": (str(doc_id).strip() if doc_id else None),
                "matched_seeds": (
                    [str(s) for s in seeds] if isinstance(seeds, (list, tuple)) else []
                ),
            }
        )
    return out


def _diff_or_none(total: Optional[int], part: Optional[int]) -> Optional[int]:
    """``total - part`` as a non-negative int, else ``None`` (honest null)."""
    if total is None or part is None:
        return None
    d = int(total) - int(part)
    return d if d >= 0 else None


def _contingency_valid(
    a: Optional[int], n1: Optional[int], c: Optional[int], n0: Optional[int]
) -> bool:
    """All four 2x2 cells present and non-negative with valid marginals.

    ``a`` seeded-with-theme, ``n1`` seeded-total, ``c`` nonseed-with-theme,
    ``n0`` nonseed-total. Requires ``0 <= a <= n1`` and ``0 <= c <= n0`` and both
    totals positive, so ``b = n1-a`` and ``d = n0-c`` are also valid.
    """
    if None in (a, n1, c, n0):
        return False
    assert a is not None and n1 is not None and c is not None and n0 is not None
    if n1 <= 0 or n0 <= 0:
        return False
    return 0 <= a <= n1 and 0 <= c <= n0


def _positive_int(value: Any, name: str) -> int:
    try:
        iv = int(value)
    except (TypeError, ValueError) as exc:
        raise PipelineError(
            f"bigquery_gdelt_seeded_theme_scoring '{name}' must be an integer: {exc}."
        )
    if iv <= 0:
        raise PipelineError(f"bigquery_gdelt_seeded_theme_scoring '{name}' must be positive.")
    return iv


def _non_negative_int(value: Any, name: str) -> int:
    try:
        iv = int(value)
    except (TypeError, ValueError) as exc:
        raise PipelineError(
            f"bigquery_gdelt_seeded_theme_scoring '{name}' must be an integer: {exc}."
        )
    if iv < 0:
        raise PipelineError(f"bigquery_gdelt_seeded_theme_scoring '{name}' must be non-negative.")
    return iv


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _round(value: float | None, ndigits: int = 6) -> float | None:
    return None if value is None else round(value, ndigits)


def _str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []

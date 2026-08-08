from __future__ import annotations

from typing import Iterable, List, Optional


def or_group(items: Iterable[str]) -> str:
    cleaned = [s.strip() for s in items if isinstance(s, str) and s.strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return "(" + " OR ".join(cleaned) + ")"


def build_gdelt_query(
    *,
    aliases: List[str],
    keywords: List[str],
    sourcelang: Optional[str] = "english",
) -> str:
    a = or_group(aliases)
    k = or_group(keywords)

    if a and k:
        base = f"({a} AND {k})"
    else:
        base = a or k or ""

    if sourcelang and base:
        return f"{base} sourcelang:{sourcelang}"
    if sourcelang and not base:
        return f"sourcelang:{sourcelang}"
    return base

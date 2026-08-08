"""SQL predicates that apply seed-matching semantics inside a BigQuery query.

Kept next to the query builder that is its only consumer, and separate from the
seed vocabulary in :mod:`kinetic.data.catalog.seeds`, so the semantics can be
unit-tested without BigQuery and the vocabulary carries no SQL.

Only characters from the validated ``[a-z0-9 ]`` seed alphabet are ever
interpolated, so no regex or SQL metacharacter can reach a literal.
"""

from __future__ import annotations

from typing import Sequence

from kinetic.data.catalog.seeds import COMPANY_ALIAS, SeedTerm


def _alias_regex(alias: str) -> str:
    """Token-boundary regex literal for a normalized alias (no metacharacters)."""
    # ``alias`` is already validated to ``[a-z0-9 ]``; embed it verbatim. The
    # boundary class ``[^a-z0-9]`` treats spaces, commas, and semicolons (the GKG
    # V2Organizations ``name,offset;`` delimiters) all as boundaries.
    return f"(^|[^a-z0-9]){alias}([^a-z0-9]|$)"


def seed_match_predicate(blob_expr: str, seed: SeedTerm) -> str:
    """SQL boolean expression: does ``blob_expr`` mention ``seed``?

    - company_alias: any alias matched with token boundaries via REGEXP_CONTAINS.
    - industry_phrase: any alias matched as a substring via LIKE.
    """
    if seed.kind == COMPANY_ALIAS:
        parts = [f"REGEXP_CONTAINS({blob_expr}, r'{_alias_regex(a)}')" for a in seed.aliases]
    else:
        parts = [f"{blob_expr} LIKE '%{a}%'" for a in seed.aliases]
    if len(parts) == 1:
        return parts[0]
    return "(" + " OR ".join(parts) + ")"


def matched_seeds_array_expr(blob_expr: str, seeds: Sequence[SeedTerm]) -> str:
    """Build a SQL ARRAY of the canonical seed labels matched by ``blob_expr``.

    Emits ``ARRAY_CONCAT(IF(<pred>, ['canonical'], []), ...)`` so each seed uses
    its own (boundary or substring) predicate rather than one shared LIKE.
    """
    pieces = [
        f"IF({seed_match_predicate(blob_expr, s)}, ['{s.canonical}'], CAST([] AS ARRAY<STRING>))"
        for s in seeds
    ]
    if len(pieces) == 1:
        return pieces[0]
    inner = ",\n    ".join(pieces)
    return f"ARRAY_CONCAT(\n    {inner}\n  )"


# ── default semiconductor seed spec ──────────────────────────────────────────

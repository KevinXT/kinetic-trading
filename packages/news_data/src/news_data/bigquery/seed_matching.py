"""
Normalized, disambiguated seed matching for record-based theme discovery.

The first seeded-discovery SQL matched every seed as a raw, unrestricted
lowercase substring against a free-text blob::

    LOWER(CONCAT(IFNULL(V2Organizations, ''))) LIKE CONCAT('%', s, '%')

That is unsafe for short / ambiguous company aliases. The worst offenders,
observed against the real 30-day corpus, are:

- ``intel``  -> matches ``intelligence`` / ``central intelligence agency`` /
  ``artificial intelligence`` (a huge, unrelated inflation of the corpus).
- ``amd``    -> matches any organisation whose name merely contains ``amd``.
- ``micron`` -> matches the length unit ``micron`` / ``microns`` and any name
  containing that substring, not only ``Micron Technology``.

This module separates two seed *kinds* and matches each appropriately:

- ``company_alias`` — matched only as a whole token with non-alphanumeric
  boundaries, so ``intel`` matches ``Intel`` / ``Intel Corp`` but **not**
  ``intelligence``. This is the safe default for company names.
- ``industry_phrase`` — matched as a case-insensitive substring, because a
  multi-word industry phrase (``semiconductor``, ``wafer fabrication``) rarely
  collides with unrelated tokens and benefits from sub-word coverage
  (``semiconductor`` inside ``semiconductors``).

Matching is defined once here and reused by the SQL builder so the behaviour is
unit-testable without touching BigQuery. The generated predicates only ever
embed characters from a validated ``[a-z0-9 ]`` alphabet, so no regex
metacharacter can reach the SQL literal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

# Seed kinds.
COMPANY_ALIAS = "company_alias"
INDUSTRY_PHRASE = "industry_phrase"
SEED_KINDS = frozenset({COMPANY_ALIAS, INDUSTRY_PHRASE})

# A normalized seed literal may only contain lowercase letters, digits, and
# single internal spaces. This keeps both the LIKE literal and the REGEXP
# pattern free of metacharacters (nothing untrusted is ever interpolated).
_SAFE_SEED_RE = re.compile(r"^[a-z0-9]+( [a-z0-9]+)*$")

# Company aliases short/ambiguous enough that raw substring matching is unsafe.
# These are always matched with token boundaries. (Others default to boundary
# matching too; this set is documentation of the *known* offenders.)
KNOWN_AMBIGUOUS_ALIASES = frozenset({"amd", "intel", "micron", "arm"})


def normalize_seed(term: str) -> str:
    """Lowercase, collapse internal whitespace, and strip a seed term.

    Raises ``ValueError`` if the normalized term is empty or contains a
    character outside the safe ``[a-z0-9 ]`` alphabet (so it can never inject a
    regex metacharacter or break a SQL literal).
    """
    norm = " ".join(str(term).strip().lower().split())
    if not norm:
        raise ValueError(f"seed term {term!r} is empty after normalization.")
    if not _SAFE_SEED_RE.match(norm):
        raise ValueError(
            f"seed term {term!r} normalizes to {norm!r}, which contains characters "
            "outside [a-z0-9 ]; only letters, digits, and single spaces are allowed."
        )
    return norm


@dataclass(frozen=True)
class SeedTerm:
    """A single normalized seed and how it should be matched.

    ``canonical`` is the lowercased label emitted in output artifacts.
    ``aliases`` are the concrete strings matched in SQL (defaults to
    ``[canonical]``); a company can carry several (``intel``, ``intel corp``).
    """

    canonical: str
    kind: str
    aliases: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.kind not in SEED_KINDS:
            raise ValueError(
                f"unknown seed kind {self.kind!r}; expected one of {sorted(SEED_KINDS)}."
            )
        object.__setattr__(self, "canonical", normalize_seed(self.canonical))
        cleaned = [normalize_seed(a) for a in (self.aliases or [self.canonical])]
        # Deterministic, de-duplicated alias order.
        seen: Dict[str, None] = {}
        for a in cleaned:
            seen.setdefault(a, None)
        object.__setattr__(self, "aliases", list(seen))


def classify_seed(term: str) -> str:
    """Heuristically classify a bare seed string into a seed kind.

    A multi-word term, or a single word that reads like an industry phrase
    (``semiconductor``, ``chipmaker``, ``microprocessor``, ``foundry`` …) is an
    ``industry_phrase``; everything else (company names / tickers) defaults to
    the safe ``company_alias`` (token-boundary) matching.
    """
    norm = normalize_seed(term)
    industry_words = (
        "semiconductor",
        "semiconductors",
        "chipmaker",
        "chipmakers",
        "microprocessor",
        "microprocessors",
        "microelectronics",
        "foundry",
        "foundries",
        "wafer",
        "fabrication",
        "lithography",
        "photolithography",
        "fabless",
    )
    if " " in norm:
        return INDUSTRY_PHRASE
    if norm in industry_words:
        return INDUSTRY_PHRASE
    return COMPANY_ALIAS


def seed_terms_from_flat(terms: Sequence[str]) -> List[SeedTerm]:
    """Build ``SeedTerm`` objects from a legacy flat ``seed_terms`` list.

    Each term is classified by :func:`classify_seed`, preserving order and
    de-duplicating on the normalized canonical form. This keeps existing configs
    (a plain ``seed_terms`` list) working while applying safe matching.
    """
    out: List[SeedTerm] = []
    seen: set[str] = set()
    for raw in terms:
        if not str(raw).strip():
            continue
        norm = normalize_seed(raw)
        if norm in seen:
            continue
        seen.add(norm)
        out.append(SeedTerm(canonical=norm, kind=classify_seed(norm)))
    if not out:
        raise ValueError("seed_terms_from_flat: no non-empty seed terms provided.")
    return out


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
#
# Company aliases carry explicit, disambiguated forms. Bare short tickers that
# are unsafe even with boundaries (``amd`` matches the standalone token ``amd``
# anywhere) still benefit from boundary matching, but reviewers should treat
# alias-only matches with care — see ``requires_record_review`` downstream.
DEFAULT_SEMICONDUCTOR_SEEDS: List[SeedTerm] = [
    SeedTerm("semiconductor", INDUSTRY_PHRASE, ["semiconductor"]),
    SeedTerm("semiconductors", INDUSTRY_PHRASE, ["semiconductors"]),
    SeedTerm("microprocessor", INDUSTRY_PHRASE, ["microprocessor"]),
    SeedTerm("chipmaker", INDUSTRY_PHRASE, ["chipmaker"]),
    SeedTerm("wafer fabrication", INDUSTRY_PHRASE, ["wafer fabrication"]),
    SeedTerm("tsmc", COMPANY_ALIAS, ["tsmc", "taiwan semiconductor"]),
    SeedTerm("nvidia", COMPANY_ALIAS, ["nvidia"]),
    SeedTerm("amd", COMPANY_ALIAS, ["amd", "advanced micro devices"]),
    SeedTerm("intel", COMPANY_ALIAS, ["intel corp", "intel corporation", "intel"]),
    SeedTerm("asml", COMPANY_ALIAS, ["asml"]),
    SeedTerm("broadcom", COMPANY_ALIAS, ["broadcom"]),
    SeedTerm("micron", COMPANY_ALIAS, ["micron technology", "micron"]),
    SeedTerm("qualcomm", COMPANY_ALIAS, ["qualcomm"]),
    SeedTerm("samsung electronics", COMPANY_ALIAS, ["samsung electronics"]),
]


def resolve_seed_terms(
    seed_specs: Optional[Sequence[dict]] = None,
    seed_terms: Optional[Sequence[str]] = None,
) -> List[SeedTerm]:
    """Resolve the effective ``SeedTerm`` list from config.

    Priority: an explicit structured ``seed_specs`` list (each ``{canonical,
    kind, aliases?}``) wins; otherwise a legacy flat ``seed_terms`` list is
    classified automatically. Raises ``ValueError`` if neither is usable.
    """
    if seed_specs:
        out: List[SeedTerm] = []
        for spec in seed_specs:
            out.append(
                SeedTerm(
                    canonical=str(spec["canonical"]),
                    kind=str(spec.get("kind") or classify_seed(str(spec["canonical"]))),
                    aliases=[str(a) for a in (spec.get("aliases") or [])],
                )
            )
        if out:
            return out
    if seed_terms:
        return seed_terms_from_flat(seed_terms)
    raise ValueError("resolve_seed_terms: provide seed_specs or seed_terms.")

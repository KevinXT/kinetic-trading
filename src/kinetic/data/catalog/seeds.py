"""Seed-term vocabulary: what a seed is, and the sets we currently ship.

A *seed* is a term used to select a topical subcorpus from a large news archive.
Two kinds behave differently and must not be conflated:

- ``company_alias`` — matched only as a whole token with non-alphanumeric
  boundaries, so ``intel`` matches ``Intel Corp`` but **not** ``intelligence``.
  The unsafe-substring problem this solves was real: matching ``intel`` as a raw
  substring against GDELT's organization blob pulled in
  ``central intelligence agency`` and ``artificial intelligence`` and inflated
  the corpus with unrelated records.
- ``industry_phrase`` — matched as a case-insensitive substring, because a
  multi-word industry phrase rarely collides with unrelated tokens and benefits
  from sub-word coverage (``semiconductor`` inside ``semiconductors``).

A normalized seed may only contain lowercase letters, digits and single internal
spaces. That restriction is what makes it safe to embed a seed in a SQL literal
or a regex: no metacharacter can survive normalization.

This module holds the vocabulary and its validation only. The SQL predicates
that apply these semantics live with the provider that emits them, in
:mod:`kinetic.ingestion.news.gdelt.bigquery.seed_predicates`.

``DEFAULT_SEMICONDUCTOR_SEEDS`` is case-specific reference data that still lives
in the platform because the relevance baselines and offline entity fixtures
consume it as a default. See the limitations section of
``docs/architecture/dependency-rules.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

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

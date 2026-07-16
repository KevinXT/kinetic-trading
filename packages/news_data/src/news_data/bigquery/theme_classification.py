"""
Research-only classification of GDELT theme codes for seeded-discovery review.

This is a transparent, rule-based first pass that groups candidate theme codes
into review buckets. It is **not** ground truth: GDELT theme codes are noisy and
a human reviewer decides final membership. Every classification carries a
``confidence`` in ``[0, 1]`` and a short ``reasoning`` string, and generic /
ambiguous codes are flagged for manual review rather than silently dropped.

Categories (research buckets, not trading labels):

- ``industry_core``            — names semiconductor / chip identity directly.
- ``supply_chain``             — trade, exports, shortages, logistics.
- ``manufacturing``            — industrial production / manufacturing.
- ``technology_infrastructure``— ICT / computing / AI infrastructure.
- ``market_context``           — prices, stock markets, earnings, macro.
- ``policy_geopolitics``       — policy, sanctions, government, conflict.
- ``generic_noise``            — ubiquitous themes present in ~all news.
- ``ambiguous``                — could be relevant or noise; needs a record read.
- ``requires_record_review``   — no rule fired; inspect sample records.

The classifier never asserts predictive value and never edits any bundle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

INDUSTRY_CORE = "industry_core"
SUPPLY_CHAIN = "supply_chain"
MANUFACTURING = "manufacturing"
TECHNOLOGY_INFRASTRUCTURE = "technology_infrastructure"
MARKET_CONTEXT = "market_context"
POLICY_GEOPOLITICS = "policy_geopolitics"
GENERIC_NOISE = "generic_noise"
AMBIGUOUS = "ambiguous"
REQUIRES_RECORD_REVIEW = "requires_record_review"

CATEGORIES = frozenset(
    {
        INDUSTRY_CORE,
        SUPPLY_CHAIN,
        MANUFACTURING,
        TECHNOLOGY_INFRASTRUCTURE,
        MARKET_CONTEXT,
        POLICY_GEOPOLITICS,
        GENERIC_NOISE,
        AMBIGUOUS,
        REQUIRES_RECORD_REVIEW,
    }
)

# Categories a reviewer must inspect before trusting; never auto-usable.
_REVIEW_CATEGORIES = frozenset(
    {GENERIC_NOISE, AMBIGUOUS, REQUIRES_RECORD_REVIEW, MARKET_CONTEXT, POLICY_GEOPOLITICS}
)

# Substrings that mark a code as *directly* about chips/semiconductors. GDELT's
# taxonomy has no clean SEMICONDUCTOR code, so this list may stay empty in
# practice — which is itself the key finding, not a bug.
_INDUSTRY_CORE_TOKENS = ("SEMICONDUCTOR", "MICROCHIP", "_CHIP", "CHIP_", "MICROPROCESSOR")

# Ubiquitous / functional-actor / broad-macro families that dominate any large
# news corpus regardless of topic. Presence here => generic_noise.
_GENERIC_PREFIXES = (
    "TAX_FNCACT",
    "TAX_ETHNICITY",
    "TAX_WORLDLANGUAGES",
    "TAX_RELIGION",
    "LEADER",
    "GENERAL_",
    "CRISISLEX_",
    "UNGP_",
    "SOC_POINTSOFINTEREST",
    "WB_696_PUBLIC_SECTOR",
)
_GENERIC_EXACT = frozenset(
    {
        "TAX_ECON_PRICE",
        "EPU_ECONOMY_HISTORIC",
        "ECON_STOCKMARKET",
        "USPEC_POLITICS_GENERAL1",
        "USPEC_POLICY1",
        "MANMADE_DISASTER_IMPLIED",
        "MEDIA_MSM",
        "MEDIA_SOCIAL",
        "SCIENCE",
    }
)

# Weaker topical signals grouped by review bucket. Order matters only for the
# reasoning string; the first matching rule wins.
_TOKEN_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (SUPPLY_CHAIN, ("TRADE", "EXPORT", "IMPORT", "SUPPLY", "SHORTAGE", "LOGISTIC", "TARIFF")),
    (
        MANUFACTURING,
        ("MANUFACTUR", "INDUSTR", "REAL_SECTOR", "PRODUCTION", "FACTORY"),
    ),
    (
        TECHNOLOGY_INFRASTRUCTURE,
        (
            "INFORMATION_AND_COMMUNICATION",
            "ICT",
            "TELECOMMUNICATION",
            "TECHNOLOG",
            "DIGITAL",
            "COMPUTER",
            "ARTIFICIAL_INTELLIGENCE",
            "INTERNET",
        ),
    ),
    (
        POLICY_GEOPOLITICS,
        (
            "SANCTION",
            "GEOPOLIT",
            "GOVERNMENT",
            "POLICY",
            "POLITICS",
            "ARMEDCONFLICT",
            "CONFLICT",
            "MILITARY",
            "NATIONAL_SECURITY",
            "ESPIONAGE",
        ),
    ),
    (
        MARKET_CONTEXT,
        (
            "ECON",
            "EPU",
            "STOCKMARKET",
            "PRICE",
            "EARNINGS",
            "INVEST",
            "MARKET",
            "IPO",
            "MERGER",
        ),
    ),
)


def _token_hits(code: str, token: str) -> bool:
    """Does ``token`` match ``code``?

    Short tokens (``ICT``, ``IPO``, ``ECON`` …) must equal a whole ``_``-delimited
    segment, so ``ICT`` does not match ``ARMEDCONFLICT`` (``...confl-ICT``) and
    ``ECON`` does not match a random substring. Longer tokens (>= 6 chars, e.g.
    ``CONFLICT``, ``MANUFACTUR``) may match as a substring to allow morphological
    variants (``MANUFACTURING`` / ``INDUSTRIAL``).
    """
    if len(token) >= 6:
        return token in code
    return token in code.split("_")


@dataclass(frozen=True)
class Classification:
    """Result of classifying one theme code."""

    category: str
    confidence: float
    reasoning: str
    generic_noise_flag: bool
    manual_review_required: bool
    matched_rule: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "classification": self.category,
            "classification_confidence": round(self.confidence, 4),
            "classification_reasoning": self.reasoning,
            "generic_noise_flag": self.generic_noise_flag,
            "manual_review_required": self.manual_review_required,
            "matched_rule": self.matched_rule,
        }


def classify_theme(theme_code: str) -> Classification:
    """Classify a single normalized GDELT theme code into a review bucket.

    ``matched_rule`` names the concrete rule that fired (the matched token /
    prefix / exact code, or ``none``) so a reviewer can see *why* a code was
    bucketed without re-deriving the lexical logic.
    """
    code = str(theme_code or "").strip().upper()
    if not code:
        return Classification(REQUIRES_RECORD_REVIEW, 0.0, "empty theme code", False, True, "empty")

    hit_core = next((tok for tok in _INDUSTRY_CORE_TOKENS if tok in code), None)
    if hit_core is not None:
        return Classification(
            INDUSTRY_CORE,
            0.9,
            "code text names semiconductor/chip identity directly",
            False,
            False,
            f"industry_core_token:{hit_core}",
        )

    if code in _GENERIC_EXACT:
        return Classification(
            GENERIC_NOISE,
            0.85,
            "ubiquitous functional-actor / broad-macro theme present across all news",
            True,
            True,
            f"generic_exact:{code}",
        )
    generic_prefix = next((p for p in _GENERIC_PREFIXES if code.startswith(p)), None)
    if generic_prefix is not None:
        return Classification(
            GENERIC_NOISE,
            0.85,
            "ubiquitous functional-actor / broad-macro theme present across all news",
            True,
            True,
            f"generic_prefix:{generic_prefix}",
        )

    for category, tokens in _TOKEN_RULES:
        hit = next((t for t in tokens if _token_hits(code, t)), None)
        if hit is not None:
            # Topical-but-contextual buckets are lower confidence and always
            # flagged for a record read: they may reflect market consequences of
            # semiconductor news rather than semiconductor identity.
            confidence = 0.6 if category in (SUPPLY_CHAIN, MANUFACTURING) else 0.5
            return Classification(
                category,
                confidence,
                f"matched {hit!r} token; contextual, not identity — review sample records",
                False,
                category in _REVIEW_CATEGORIES,
                f"{category}_token:{hit}",
            )

    return Classification(
        REQUIRES_RECORD_REVIEW,
        0.2,
        "no classification rule fired; inspect sample records before use",
        False,
        True,
        "none",
    )


def classify_many(theme_codes: List[str]) -> List[Classification]:
    """Classify a list of theme codes, preserving order."""
    return [classify_theme(t) for t in theme_codes]

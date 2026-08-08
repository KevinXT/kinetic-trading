"""
Defensible association scoring for seeded theme-discovery candidates.

Raw co-occurrence frequency is **not** a relevance measure: the most frequent
themes in a semiconductor-seeded corpus (``TAX_ECON_PRICE``,
``EPU_ECONOMY_HISTORIC``, ``LEADER`` …) are simply the most frequent themes in
*all* news. To tell a semiconductor-associated theme from a ubiquitous one, a
theme's prevalence in the seeded corpus must be compared against its prevalence
in a matched **background** corpus over the same window.

This module holds pure, dependency-free functions that turn a 2x2 contingency
count (seed vs background, theme present vs absent) into transparent, separately
reported association metrics with additive (Laplace) smoothing so that
low-support ratios do not explode. Nothing here decides relevance — it produces
numbers a reviewer (or a later, leakage-safe model) can weigh.

Contingency layout for a theme ``t``::

    seed_record_count         a   = seeded records carrying t
    total_seed_records        N1  = seeded records total
    background_record_count   b   = background records carrying t
    total_background_records  N0  = background records total

All functions return ``None`` (never a fabricated value) when an input is
missing or a quantity is undefined (e.g. background prevalence of zero).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Sequence

# Additive smoothing constant applied to contingency counts. 0.5 (a Jeffreys /
# Haldane-Anscombe style correction) keeps odds ratios finite at zero cells.
DEFAULT_SMOOTHING = 0.5


def _pos_int(value: Optional[float]) -> Optional[int]:
    if value is None:
        return None
    try:
        iv = int(value)
    except (TypeError, ValueError):
        return None
    return iv if iv >= 0 else None


def prevalence(count: Optional[int], total: Optional[int]) -> Optional[float]:
    """Fraction ``count / total`` in ``[0, 1]``; ``None`` if total is missing/zero."""
    c, n = _pos_int(count), _pos_int(total)
    if c is None or n is None or n == 0:
        return None
    return c / n


def lift(
    seed_count: Optional[int],
    total_seed: Optional[int],
    background_count: Optional[int],
    total_background: Optional[int],
) -> Optional[float]:
    """Ratio of seeded prevalence to background prevalence (``None`` if undefined)."""
    ps = prevalence(seed_count, total_seed)
    pb = prevalence(background_count, total_background)
    if ps is None or pb is None or pb == 0.0:
        return None
    return ps / pb


def smoothed_log_lift(
    seed_count: Optional[int],
    total_seed: Optional[int],
    background_count: Optional[int],
    total_background: Optional[int],
    *,
    alpha: float = DEFAULT_SMOOTHING,
) -> Optional[float]:
    """Natural log of the additively-smoothed prevalence ratio.

    ``ln( ((a+alpha)/(N1+alpha)) / ((b+alpha)/(N0+alpha)) )``. Smoothing keeps the
    value finite when ``b == 0`` and shrinks extreme ratios from tiny support.
    """
    a, n1 = _pos_int(seed_count), _pos_int(total_seed)
    b, n0 = _pos_int(background_count), _pos_int(total_background)
    if None in (a, n1, b, n0) or n1 == 0 or n0 == 0:
        return None
    assert a is not None and n1 is not None and b is not None and n0 is not None
    seed_p = (a + alpha) / (n1 + alpha)
    bg_p = (b + alpha) / (n0 + alpha)
    if seed_p <= 0 or bg_p <= 0:
        return None
    return math.log(seed_p / bg_p)


def risk_difference(
    seed_count: Optional[int],
    total_seed: Optional[int],
    background_count: Optional[int],
    total_background: Optional[int],
) -> Optional[float]:
    """Seeded prevalence minus background prevalence (absolute, in ``[-1, 1]``)."""
    ps = prevalence(seed_count, total_seed)
    pb = prevalence(background_count, total_background)
    if ps is None or pb is None:
        return None
    return ps - pb


def odds_ratio(
    seed_count: Optional[int],
    total_seed: Optional[int],
    background_count: Optional[int],
    total_background: Optional[int],
    *,
    alpha: float = DEFAULT_SMOOTHING,
) -> Optional[float]:
    """Smoothed odds ratio of theme presence between seed and background corpora.

    ``((a+alpha)(d+alpha)) / ((c+alpha)(b+alpha))`` where ``c = N1-a`` and
    ``d = N0-b``. Smoothing avoids division by zero at empty cells.
    """
    a, n1 = _pos_int(seed_count), _pos_int(total_seed)
    b, n0 = _pos_int(background_count), _pos_int(total_background)
    if None in (a, n1, b, n0) or n1 == 0 or n0 == 0:
        return None
    assert a is not None and n1 is not None and b is not None and n0 is not None
    if a > n1 or b > n0:
        return None
    c = n1 - a
    d = n0 - b
    numerator = (a + alpha) * (d + alpha)
    denominator = (c + alpha) * (b + alpha)
    if denominator <= 0:
        return None
    return numerator / denominator


def two_proportion_z_test(
    seed_count: Optional[int],
    total_seed: Optional[int],
    background_count: Optional[int],
    total_background: Optional[int],
) -> Optional[float]:
    """Two-sided p-value of a pooled two-proportion z-test (``None`` if undefined).

    Tests H0: the theme's prevalence is equal in the seeded and background
    corpora. This is a *significance* statement only — it says nothing about
    effect size, so it must never be the primary ranking key (a ubiquitous theme
    with a microscopic prevalence gap can be "significant" purely from sample
    size). Reported alongside, and subordinate to, the smoothed lift.
    """
    a, n1 = _pos_int(seed_count), _pos_int(total_seed)
    b, n0 = _pos_int(background_count), _pos_int(total_background)
    if None in (a, n1, b, n0) or n1 == 0 or n0 == 0:
        return None
    assert a is not None and n1 is not None and b is not None and n0 is not None
    if a > n1 or b > n0:
        return None
    p_pool = (a + b) / (n1 + n0)
    se = math.sqrt(p_pool * (1.0 - p_pool) * (1.0 / n1 + 1.0 / n0))
    if se <= 0.0:
        return None
    z = (a / n1 - b / n0) / se
    # Two-sided normal tail: P(|Z| > |z|) = erfc(|z| / sqrt(2)).
    return math.erfc(abs(z) / math.sqrt(2.0))


# --------------------------------------------------------------------------- #
# Sparse-aware 2x2 hypothesis-test selection.
#
# The pooled two-proportion z-test is a large-sample normal approximation. It is
# unreliable when any *expected* cell count under H0 is small, which is common
# here: seed support can be >= 25 yet the with-theme column total (a + c) is tiny
# relative to the corpus, so the expected seeded-with-theme cell is well below 5.
# For those sparse tables we use a two-sided Fisher exact test instead.
#
# Fisher's exact test is computed with an exact, dependency-free hypergeometric
# sum (via ``math.lgamma`` for numerical stability). SciPy is intentionally NOT a
# dependency of this lean, pure-Python module, and adding a compiled numeric
# stack solely for one test is inappropriate here; the exact computation below is
# the standard two-sided definition (sum of the probabilities of every table with
# the same margins whose probability does not exceed the observed table's), so it
# is not an unreviewed approximation. The selection is only ever applied to sparse
# tables (min expected cell < threshold), which bounds the summation range.
EXPECTED_CELL_THRESHOLD = 5.0
# A reviewed exact implementation is always available (no optional dependency).
FISHER_EXACT_AVAILABLE = True


def expected_cell_counts(
    a: int, b: int, c: int, d: int
) -> Optional[tuple[float, float, float, float]]:
    """Expected 2x2 cell counts under independence; ``None`` for an empty table.

    Row totals ``(a+b, c+d)`` times column totals ``(a+c, b+d)`` divided by the
    grand total. Used only to *select* a test, never to compute a p-value.
    """
    n = a + b + c + d
    if n <= 0:
        return None
    r1, r2 = a + b, c + d
    col1, col2 = a + c, b + d
    return (r1 * col1 / n, r1 * col2 / n, r2 * col1 / n, r2 * col2 / n)


def _log_choose(n: int, k: int) -> float:
    if k < 0 or k > n or n < 0:
        return float("-inf")
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def _hypergeom_logpmf(k: int, n1: int, n0: int, col1: int) -> float:
    """Log P(X=k) for X ~ Hypergeometric(population n1+n0, successes n1, draws col1)."""
    return _log_choose(n1, k) + _log_choose(n0, col1 - k) - _log_choose(n1 + n0, col1)


def fisher_exact_two_sided(
    a: int, b: int, c: int, d: int, *, rel_tol: float = 1e-7
) -> Optional[float]:
    """Two-sided Fisher exact p-value for a 2x2 table; ``None`` if undefined.

    Conditioning on both margins, ``X = a`` is hypergeometric. The two-sided
    p-value is the total probability of every table (same margins) whose point
    probability does not exceed the observed table's, matching the conventional
    definition used by ``scipy.stats.fisher_exact``. ``rel_tol`` guards the
    floating-point comparison at the inclusion boundary. This is exact, not an
    approximation; callers restrict it to sparse tables so the sum is short.
    """
    if a < 0 or b < 0 or c < 0 or d < 0:
        return None
    n1, n0, col1 = a + b, c + d, a + c
    n = n1 + n0
    if n == 0 or col1 == 0 or col1 == n or n1 == 0 or n0 == 0:
        # A degenerate margin makes the table deterministic: p-value is 1.
        return 1.0
    lo, hi = max(0, col1 - n0), min(n1, col1)
    if hi < lo:
        return None
    log_p_obs = _hypergeom_logpmf(a, n1, n0, col1)
    threshold = log_p_obs + math.log1p(rel_tol)
    total = 0.0
    for k in range(lo, hi + 1):
        lp = _hypergeom_logpmf(k, n1, n0, col1)
        if lp <= threshold:
            total += math.exp(lp)
    return min(1.0, total)


@dataclass(frozen=True)
class HypothesisTestResult:
    """Outcome of sparse-aware 2x2 test selection for one candidate.

    ``hypothesis_test`` is one of ``two_proportion_z_test``,
    ``fisher_exact_two_sided``, ``unavailable_invalid_table`` or
    ``unavailable_sparse_without_exact_test``. ``p_value`` is ``None`` whenever
    ``p_value_valid`` is ``False`` — an unavailable test is never coerced to 1.
    """

    hypothesis_test: str
    hypothesis_test_reason: str
    p_value: Optional[float]
    p_value_valid: bool
    p_value_unavailable_reason: Optional[str]
    minimum_expected_cell_count: Optional[float]
    expected_cell_threshold: float
    contingency_a: Optional[int]
    contingency_b: Optional[int]
    contingency_c: Optional[int]
    contingency_d: Optional[int]

    def to_dict(self) -> Dict[str, object]:
        return dict(asdict(self))


def select_hypothesis_test(
    seed_with_theme: Optional[int],
    total_seed: Optional[int],
    nonseed_with_theme: Optional[int],
    total_nonseed: Optional[int],
    *,
    threshold: float = EXPECTED_CELL_THRESHOLD,
    fisher_available: bool = FISHER_EXACT_AVAILABLE,
) -> HypothesisTestResult:
    """Build the disjoint 2x2 table, validate it, and pick a valid p-value test.

    The contingency table uses *disjoint* groups::

        a = seeded records with the theme      b = total_seed - a
        c = non-seeded records with the theme  d = total_nonseed - c

    A pooled two-proportion z-test is used only when every expected cell count is
    at least ``threshold`` (default 5); otherwise a two-sided Fisher exact test is
    used. Invalid tables (negative or inconsistent cells) yield no p-value. The
    choice is fixed and deterministic — never selected by which p-value is smaller.
    """
    a = _pos_int(seed_with_theme)
    n1 = _pos_int(total_seed)
    c = _pos_int(nonseed_with_theme)
    n0 = _pos_int(total_nonseed)

    def _invalid(reason: str) -> HypothesisTestResult:
        return HypothesisTestResult(
            hypothesis_test="unavailable_invalid_table",
            hypothesis_test_reason=reason,
            p_value=None,
            p_value_valid=False,
            p_value_unavailable_reason=reason,
            minimum_expected_cell_count=None,
            expected_cell_threshold=threshold,
            contingency_a=a,
            contingency_b=None,
            contingency_c=c,
            contingency_d=None,
        )

    if None in (a, n1, c, n0):
        return _invalid("missing contingency inputs")
    assert a is not None and n1 is not None and c is not None and n0 is not None
    if a > n1 or c > n0:
        return _invalid("with-theme count exceeds its group total")
    b, d = n1 - a, n0 - c
    if min(a, b, c, d) < 0:
        return _invalid("negative contingency cell")
    exp = expected_cell_counts(a, b, c, d)
    if exp is None:
        return _invalid("empty contingency table")
    min_exp = min(exp)

    if min_exp >= threshold:
        p = two_proportion_z_test(a, n1, c, n0)
        test = "two_proportion_z_test"
        reason = f"min expected cell {min_exp:.4f} >= threshold {threshold}"
    elif fisher_available:
        p = fisher_exact_two_sided(a, b, c, d)
        test = "fisher_exact_two_sided"
        reason = f"min expected cell {min_exp:.4f} < threshold {threshold}"
    else:
        return HypothesisTestResult(
            hypothesis_test="unavailable_sparse_without_exact_test",
            hypothesis_test_reason=(
                f"min expected cell {min_exp:.4f} < threshold {threshold} and no "
                "exact-test implementation is available"
            ),
            p_value=None,
            p_value_valid=False,
            p_value_unavailable_reason="sparse table without an exact test",
            minimum_expected_cell_count=min_exp,
            expected_cell_threshold=threshold,
            contingency_a=a,
            contingency_b=b,
            contingency_c=c,
            contingency_d=d,
        )

    valid = p is not None
    return HypothesisTestResult(
        hypothesis_test=test,
        hypothesis_test_reason=reason,
        p_value=p,
        p_value_valid=valid,
        p_value_unavailable_reason=None if valid else "test returned an undefined p-value",
        minimum_expected_cell_count=min_exp,
        expected_cell_threshold=threshold,
        contingency_a=a,
        contingency_b=b,
        contingency_c=c,
        contingency_d=d,
    )


def lift_confidence_interval(
    seed_count: Optional[int],
    total_seed: Optional[int],
    background_count: Optional[int],
    total_background: Optional[int],
    *,
    alpha: float = DEFAULT_SMOOTHING,
    z: float = 1.959963984540054,
) -> Optional[tuple[float, float]]:
    """Approximate 95% CI for the (smoothed) lift, via the log-ratio delta method.

    ``Var(ln lift) ≈ 1/a' - 1/N1' + 1/b' - 1/N0'`` on additively-smoothed counts,
    then the interval is exponentiated back onto the lift scale. Returns
    ``(low, high)`` or ``None`` when inputs are missing. The default ``z``
    corresponds to a two-sided 95% interval.
    """
    log_lift_value = smoothed_log_lift(
        seed_count, total_seed, background_count, total_background, alpha=alpha
    )
    if log_lift_value is None:
        return None
    a, n1 = _pos_int(seed_count), _pos_int(total_seed)
    b, n0 = _pos_int(background_count), _pos_int(total_background)
    if None in (a, n1, b, n0):
        return None
    assert a is not None and n1 is not None and b is not None and n0 is not None
    a_s, n1_s, b_s, n0_s = a + alpha, n1 + alpha, b + alpha, n0 + alpha
    var = 1.0 / a_s - 1.0 / n1_s + 1.0 / b_s - 1.0 / n0_s
    if var < 0.0:
        return None
    se = math.sqrt(var)
    return (math.exp(log_lift_value - z * se), math.exp(log_lift_value + z * se))


def benjamini_hochberg(pvalues: Sequence[Optional[float]]) -> List[Optional[float]]:
    """Benjamini-Hochberg FDR-adjusted p-values, preserving input order.

    ``None`` inputs pass through as ``None`` and are excluded from the ``m`` used
    for adjustment (only actually-tested candidates count). Adjusted values are
    enforced monotone non-decreasing in p and clipped to ``[0, 1]``. Controlling
    the false-discovery rate matters here because many themes are screened at
    once; a raw per-theme p-value would over-state how many are "real".
    """
    indexed = [(i, p) for i, p in enumerate(pvalues) if p is not None]
    out: List[Optional[float]] = [None] * len(pvalues)
    m = len(indexed)
    if m == 0:
        return out
    indexed.sort(key=lambda t: t[1])  # type: ignore[arg-type,return-value]
    prev = 1.0
    # Walk from largest p (rank m) down to smallest (rank 1), enforcing monotonicity.
    for rank in range(m, 0, -1):
        idx, p = indexed[rank - 1]
        adj = min(prev, (p * m) / rank)  # type: ignore[operator]
        adj = max(0.0, min(1.0, adj))
        out[idx] = adj
        prev = adj
    return out


def weekly_stability(weekly_counts: Sequence[Optional[int]]) -> tuple[int, int]:
    """Summarize fixed weekly-bucket seed counts.

    Returns ``(periods_present, minimum_period_support)``: how many weekly buckets
    have at least one seeded record, and the smallest count among the *present*
    buckets (0 when none are present). Weekly buckets resist the generic-theme
    bias of a "present on 20/30 days" rule, which ubiquitous themes trivially pass.
    """
    positive = [int(c) for c in weekly_counts if c is not None and int(c) > 0]
    if not positive:
        return (0, 0)
    return (len(positive), min(positive))


def share(top: Optional[int], total: Optional[int]) -> Optional[float]:
    """Fraction ``top / total`` in ``[0, 1]``; ``None`` if total missing/zero."""
    t, n = _pos_int(top), _pos_int(total)
    if t is None or n is None or n == 0:
        return None
    return min(1.0, t / n)


def top_share(counts: Sequence[float]) -> Optional[float]:
    """Share of the single largest category; ``None`` if the total is zero."""
    vals = [float(c) for c in counts if c is not None and float(c) > 0]
    total = math.fsum(vals)
    if total <= 0:
        return None
    return max(vals) / total


def herfindahl(counts: Sequence[float]) -> Optional[float]:
    """Herfindahl concentration ``sum(share_i^2)``; ``None`` if the total is zero.

    ``1/k`` (even across k categories) to ``1.0`` (single category).
    """
    vals = [float(c) for c in counts if c is not None and float(c) > 0]
    total = math.fsum(vals)
    if total <= 0:
        return None
    return math.fsum((v / total) ** 2 for v in vals)


@dataclass(frozen=True)
class ScoreComponents:
    """Separately-reported, unit-scaled contributions to the composite score.

    Each field is in ``[0, 1]`` (or ``None`` when its inputs are unavailable). The
    composite deliberately keeps them separate so no weak assumption hides inside
    one number.
    """

    support: Optional[float]
    association: Optional[float]
    temporal_stability: Optional[float]
    source_diversity: Optional[float]
    seed_diversity: Optional[float]
    generic_penalty: float
    duplication_penalty: Optional[float]

    def to_dict(self) -> Dict[str, Optional[float]]:
        return dict(asdict(self))


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def support_component(
    seed_count: Optional[int], min_support: int, saturating_support: int
) -> Optional[float]:
    """Log-scaled support in ``[0, 1]``; ``None`` if count missing.

    Below ``min_support`` the component is 0 (a hard floor a reviewer can rely
    on); it saturates to 1 at ``saturating_support``.
    """
    a = _pos_int(seed_count)
    if a is None:
        return None
    if a < min_support:
        return 0.0
    if a >= saturating_support:
        return 1.0
    lo, hi = math.log(min_support + 1), math.log(saturating_support + 1)
    if hi <= lo:
        return 1.0
    return _clip01((math.log(a + 1) - lo) / (hi - lo))


def association_component(log_lift_value: Optional[float]) -> Optional[float]:
    """Map smoothed log-lift to ``[0, 1]`` via a logistic squash; ``None`` if absent.

    log-lift 0 (no enrichment) -> 0.5, strongly enriched -> ->1, depleted -> ->0.
    """
    if log_lift_value is None:
        return None
    return _clip01(1.0 / (1.0 + math.exp(-log_lift_value)))


def temporal_stability_component(
    periods_present: Optional[int], periods_total: Optional[int]
) -> Optional[float]:
    """Fraction of periods in which the theme appears; ``None`` if unknown."""
    p, t = _pos_int(periods_present), _pos_int(periods_total)
    if p is None or t is None or t == 0:
        return None
    return _clip01(p / t)


def diversity_component(top_share_value: Optional[float]) -> Optional[float]:
    """``1 - top_share`` — high when no single category dominates; ``None`` if absent."""
    if top_share_value is None:
        return None
    return _clip01(1.0 - top_share_value)


def composite_score(components: ScoreComponents) -> Optional[float]:
    """Weighted mean of the available components minus penalties.

    Returns ``None`` when the association component is unavailable (no background
    corpus): without an enrichment signal a composite would be raw-frequency
    ranking in disguise, which this whole module exists to avoid. Weights are
    fixed and transparent — they are **not** tuned against any downstream return.
    """
    if components.association is None:
        return None
    weighted: List[tuple[float, float]] = []
    for weight, value in (
        (0.30, components.association),
        (0.25, components.support),
        (0.20, components.temporal_stability),
        (0.15, components.source_diversity),
        (0.10, components.seed_diversity),
    ):
        if value is not None:
            weighted.append((weight, value))
    if not weighted:
        return None
    total_w = math.fsum(w for w, _ in weighted)
    base = math.fsum(w * v for w, v in weighted) / total_w
    penalty = components.generic_penalty
    if components.duplication_penalty is not None:
        penalty = max(penalty, components.duplication_penalty)
    return _clip01(base * (1.0 - _clip01(penalty)))

"""Small, dependency-free statistical helpers for the research layer.

The environment cannot rely on numpy/scipy/pandas, so everything here is pure
stdlib. Functions are written to be explicit about *lag-only* semantics: callers
pass the prior window and the current value separately, so a rolling baseline can
never accidentally include the observation it is meant to classify.

All estimators are documented with their exact formula and their behavior on
degenerate inputs (empty windows, zero variance). Nothing here interprets a value
as an "attention shock"; it only computes numbers.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Mapping, Sequence


def mean(values: Sequence[float]) -> float | None:
    """Arithmetic mean, or None for an empty sequence."""
    if not values:
        return None
    return math.fsum(values) / len(values)


def sample_std(values: Sequence[float]) -> float | None:
    """Sample standard deviation (ddof=1).

    Returns None when fewer than two observations exist (undefined dispersion).
    """
    n = len(values)
    if n < 2:
        return None
    mu = math.fsum(values) / n
    variance = math.fsum((v - mu) ** 2 for v in values) / (n - 1)
    return math.sqrt(variance)


def population_std(values: Sequence[float]) -> float | None:
    """Population standard deviation (ddof=0); None for an empty sequence."""
    n = len(values)
    if n == 0:
        return None
    mu = math.fsum(values) / n
    return math.sqrt(math.fsum((v - mu) ** 2 for v in values) / n)


def lag_zscore(current: float, prior_window: Sequence[float]) -> float | None:
    """z-score of ``current`` against a strictly prior window.

    ``(current - mean(prior)) / std(prior)`` using sample std. Returns None if the
    prior window is too short or has zero dispersion (so a constant history never
    manufactures an infinite shock).
    """
    if len(prior_window) < 2:
        return None
    mu = mean(prior_window)
    sd = sample_std(prior_window)
    if mu is None or sd is None or sd == 0.0:
        return None
    return (current - mu) / sd


def lag_ratio(current: float, prior_window: Sequence[float]) -> float | None:
    """Ratio of ``current`` to the mean of a strictly prior window.

    Returns None when the prior window is empty or its mean is zero.
    """
    mu = mean(prior_window)
    if mu is None or mu == 0.0:
        return None
    return current / mu


def trailing_percentile(current: float, prior_window: Sequence[float]) -> float | None:
    """Fraction of prior observations <= ``current`` (empirical CDF, 0..1).

    Uses only prior observations. Returns None for an empty prior window.
    """
    if not prior_window:
        return None
    at_or_below = sum(1 for v in prior_window if v <= current)
    return at_or_below / len(prior_window)


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolation percentile (q in [0, 1]); None for empty input."""
    if not values:
        return None
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be in [0, 1]")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return ordered[low]
    frac = pos - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


def entropy(counts: Sequence[float]) -> float | None:
    """Shannon entropy (natural log) of a count vector; None if total is zero.

    ``-sum(p_i * ln p_i)``. Zero counts contribute nothing. A single non-zero
    category yields exactly 0.0.
    """
    total = math.fsum(c for c in counts if c > 0)
    if total <= 0:
        return None
    acc = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            acc -= p * math.log(p)
    return acc


def herfindahl(counts: Sequence[float]) -> float | None:
    """Herfindahl–Hirschman Index ``sum(p_i^2)`` over shares; None if total is zero.

    Ranges from 1/k (perfectly even across k categories) to 1.0 (single source).
    """
    total = math.fsum(c for c in counts if c > 0)
    if total <= 0:
        return None
    return math.fsum((c / total) ** 2 for c in counts if c > 0)


def effective_count(counts: Sequence[float]) -> float | None:
    """Effective number of categories, ``exp(entropy)``; None if total is zero."""
    ent = entropy(counts)
    if ent is None:
        return None
    return math.exp(ent)


@dataclass(frozen=True)
class SummaryStat:
    """Distribution summary for a set of event responses."""

    count: int
    mean: float | None
    median: float | None
    std: float | None
    minimum: float | None
    maximum: float | None
    ci_low: float | None
    ci_high: float | None
    positive_fraction: float | None


def bootstrap_ci(
    values: Sequence[float],
    *,
    confidence: float = 0.95,
    iterations: int = 2000,
    seed: int = 12345,
) -> tuple[float | None, float | None]:
    """Percentile block-free bootstrap CI for the mean, using a fixed seed.

    Deterministic given identical input and seed. Returns ``(None, None)`` for
    fewer than two observations, where a resampled mean is not meaningful.
    """
    n = len(values)
    if n < 2:
        return (None, None)
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(iterations):
        resample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(math.fsum(resample) / n)
    means.sort()
    tail = (1.0 - confidence) / 2.0
    low = percentile(means, tail)
    high = percentile(means, 1.0 - tail)
    return (low, high)


def summarize(
    values: Sequence[float],
    *,
    confidence: float = 0.95,
    seed: int = 12345,
) -> SummaryStat:
    """Summarize a response distribution with a bootstrap CI for the mean."""
    n = len(values)
    if n == 0:
        return SummaryStat(0, None, None, None, None, None, None, None, None)
    ci_low, ci_high = bootstrap_ci(values, confidence=confidence, seed=seed)
    return SummaryStat(
        count=n,
        mean=mean(values),
        median=percentile(values, 0.5),
        std=sample_std(values),
        minimum=min(values),
        maximum=max(values),
        ci_low=ci_low,
        ci_high=ci_high,
        positive_fraction=sum(1 for v in values if v > 0) / n,
    )


def sample_moving_block_keys(
    ordered_keys: Sequence[str],
    *,
    block_length: int,
    seed: int,
) -> list[str]:
    """Sample circular contiguous blocks of ordered cluster keys.

    The returned list has the same length as ``ordered_keys``. A caller resamples
    the complete cluster associated with each key, so all rows sharing a market
    session move together.
    """
    if block_length < 1:
        raise ValueError("block_length must be >= 1")
    if not ordered_keys:
        return []
    keys = list(ordered_keys)
    n = len(keys)
    width = min(block_length, n)
    rng = random.Random(seed)
    sampled: list[str] = []
    while len(sampled) < n:
        start = rng.randrange(n)
        sampled.extend(keys[(start + offset) % n] for offset in range(width))
    return sampled[:n]


def session_date_block_bootstrap_ci(
    values_by_session_date: Mapping[str, Sequence[float]],
    *,
    confidence: float = 0.95,
    iterations: int = 2000,
    seed: int = 12345,
    block_length: int = 5,
) -> tuple[float | None, float | None]:
    """Moving-block bootstrap CI over session-date cluster means.

    Rows from the same session are aggregated before resampling, preventing
    cross-sectional rows from being treated as independent. Contiguous blocks of
    session dates address some serial dependence from overlapping horizons, but
    this is not a complete multiway dependence correction.
    """
    cluster_means: dict[str, float] = {}
    for key, values in values_by_session_date.items():
        cluster_mean = mean(values)
        if cluster_mean is not None:
            cluster_means[str(key)] = cluster_mean
    keys = sorted(cluster_means)
    if len(keys) < 2:
        return (None, None)
    means: list[float] = []
    for iteration in range(iterations):
        sampled_keys = sample_moving_block_keys(
            keys,
            block_length=block_length,
            seed=seed + iteration,
        )
        sample_mean = mean([cluster_means[key] for key in sampled_keys])
        if sample_mean is not None:
            means.append(sample_mean)
    tail = (1.0 - confidence) / 2.0
    return (percentile(means, tail), percentile(means, 1.0 - tail))


def welch_two_sample_p(
    mean_a: float | None,
    std_a: float | None,
    n_a: int,
    mean_b: float | None,
    std_b: float | None,
    n_b: int,
) -> float | None:
    """Two-sided p-value for H0: ``mean_a == mean_b`` via Welch's t (normal approx).

    Operates on two independent samples (here: session-date cluster means for the
    event and control groups). Uses the unequal-variance Welch statistic and a
    normal approximation for the tail, matching the coarse-screen style used
    elsewhere in this module. Returns None when either dispersion is undefined or
    the pooled standard error is zero. This is a screening statistic; it does not
    model serial dependence from overlapping horizons (see limitations).
    """
    if mean_a is None or mean_b is None or std_a is None or std_b is None:
        return None
    if n_a < 2 or n_b < 2:
        return None
    se_squared = (std_a**2) / n_a + (std_b**2) / n_b
    if se_squared <= 0.0:
        return None
    t = (mean_a - mean_b) / math.sqrt(se_squared)
    return 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t) / math.sqrt(2.0))))


def two_sample_session_block_bootstrap_difference_ci(
    event_by_session_date: Mapping[str, Sequence[float]],
    control_by_session_date: Mapping[str, Sequence[float]],
    *,
    confidence: float = 0.95,
    iterations: int = 2000,
    seed: int = 12345,
    block_length: int = 5,
) -> tuple[float | None, float | None]:
    """Moving-block bootstrap CI for ``event_mean - control_mean``.

    Both groups are first aggregated to one value per session date so that a
    single session carrying several instruments is not treated as several
    independent observations. Event and control session dates are then resampled
    *independently* with circular moving blocks (the two groups do not share the
    same calendar rows). The difference of the two resampled cluster-mean averages
    is recorded each iteration. Returns ``(None, None)`` when either group has
    fewer than two session-date clusters, where a difference CI is not meaningful.
    """
    event_means: dict[str, float] = {}
    for key, values in event_by_session_date.items():
        cluster_mean = mean(values)
        if cluster_mean is not None:
            event_means[str(key)] = cluster_mean
    control_means: dict[str, float] = {}
    for key, values in control_by_session_date.items():
        cluster_mean = mean(values)
        if cluster_mean is not None:
            control_means[str(key)] = cluster_mean
    event_keys = sorted(event_means)
    control_keys = sorted(control_means)
    if len(event_keys) < 2 or len(control_keys) < 2:
        return (None, None)
    differences: list[float] = []
    for iteration in range(iterations):
        event_sample_keys = sample_moving_block_keys(
            event_keys, block_length=block_length, seed=seed + iteration
        )
        control_sample_keys = sample_moving_block_keys(
            control_keys, block_length=block_length, seed=seed + iteration + 10_000_019
        )
        event_sample = mean([event_means[key] for key in event_sample_keys])
        control_sample = mean([control_means[key] for key in control_sample_keys])
        if event_sample is not None and control_sample is not None:
            differences.append(event_sample - control_sample)
    if not differences:
        return (None, None)
    tail = (1.0 - confidence) / 2.0
    return (percentile(differences, tail), percentile(differences, 1.0 - tail))


def benjamini_hochberg(p_values: Sequence[float], *, alpha: float = 0.05) -> list[bool]:
    """Benjamini–Hochberg FDR control.

    Returns a list of booleans (aligned to input order) marking which hypotheses
    are rejected at the given false-discovery-rate ``alpha``. Empty input yields
    an empty list. This is a warning tool against factor fishing, not a discovery
    engine.
    """
    m = len(p_values)
    if m == 0:
        return []
    indexed = sorted(enumerate(p_values), key=lambda kv: kv[1])
    threshold_rank = -1
    for rank, (_, p) in enumerate(indexed, start=1):
        if p <= alpha * rank / m:
            threshold_rank = rank
    rejected = [False] * m
    if threshold_rank >= 0:
        for rank, (orig_index, _) in enumerate(indexed, start=1):
            if rank <= threshold_rank:
                rejected[orig_index] = True
    return rejected


def benjamini_hochberg_adjusted(p_values: Sequence[float]) -> list[float]:
    """Benjamini–Hochberg adjusted p-values, aligned to input order."""
    m = len(p_values)
    if m == 0:
        return []
    indexed = sorted(enumerate(p_values), key=lambda item: item[1], reverse=True)
    adjusted = [1.0] * m
    running = 1.0
    for reverse_rank, (original_index, p_value) in enumerate(indexed, start=1):
        rank = m - reverse_rank + 1
        running = min(running, float(p_value) * m / rank)
        adjusted[original_index] = min(1.0, running)
    return adjusted

"""Deterministic, offline event study around news-attention shocks.

An "attention event" is defined by a *lag-only* rule (default: the 30-session
attention z-score meets a configurable threshold). The threshold is configuration
driven and is not chosen after inspecting which value maximizes a response — that
would be data mining.

The report distinguishes confirmatory from exploratory summaries. Formal
inference requires a minimum number of market-session-date clusters, uses a
moving-block bootstrap over session-date means, and applies Benjamini–Hochberg
only within predeclared confirmatory families.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from research_data import stats
from research_data.models import NewsMarketObservation

# The response metrics summarized for every group.
_RESPONSE_FIELDS = (
    "target_session_return",
    "target_session_absolute_return",
    "target_session_benchmark_adjusted_return",
    "target_session_parkinson_variance",
    "target_through_plus_4_cumulative_return",
    "target_through_plus_4_cumulative_benchmark_adjusted_return",
    "target_through_plus_4_volume_ratio_to_prior_20",
)

HYPOTHESIS_REGISTRY_VERSION = "news-market-hypotheses-v2"
H1_FAMILY = "confirmatory_h1_attention_volatility_v1"
H2_FAMILY = "confirmatory_h2_coverage_structure_v1"
H1_RESPONSES = (
    "target_session_absolute_return",
    "target_session_parkinson_variance",
)
HYPOTHESIS_REGISTRY = (
    {
        "hypothesis_id": "H1",
        "hypothesis_class": "confirmatory",
        "hypothesis_family": H1_FAMILY,
        "description": "news-volume attention shock and subsequent magnitude/volatility",
    },
    {
        "hypothesis_id": "H2",
        "hypothesis_class": "confirmatory",
        "hypothesis_family": H2_FAMILY,
        "description": (
            "broad domain coverage versus heavily syndicated coverage; "
            "domain identity is only an independence proxy"
        ),
    },
    {
        "hypothesis_id": "H3",
        "hypothesis_class": "exploratory",
        "hypothesis_family": "exploratory_repetition_reversal_v1",
        "description": "repetition/staleness and continuation or reversal",
    },
    {
        "hypothesis_id": "H4",
        "hypothesis_class": "exploratory",
        "hypothesis_family": "exploratory_attention_prior_volatility_v1",
        "description": "attention effects conditioned on prior volatility",
    },
    {
        "hypothesis_id": "H5",
        "hypothesis_class": "exploratory",
        "hypothesis_family": "exploratory_spillover_v1",
        "description": "multi-topic coverage and cross-instrument reaction",
    },
)


@dataclass(frozen=True)
class EventStudyConfig:
    attention_field: str = "news_attention_zscore_30"
    threshold: float = 2.0
    minimum_sample: int = 20
    forward_horizon: int = 5
    confidence: float = 0.95
    seed: int = 12345
    fdr_alpha: float = 0.05
    bootstrap_unit: str = "session_date"
    bootstrap_block_length: int = 5
    bootstrap_iterations: int = 2000

    def __post_init__(self) -> None:
        if self.forward_horizon != 5:
            raise ValueError("Core V2 event study supports forward_horizon=5 only")
        if self.bootstrap_unit != "session_date":
            raise ValueError("Core V2 requires bootstrap_unit='session_date'")
        if self.bootstrap_block_length < 1:
            raise ValueError("bootstrap_block_length must be >= 1")
        if self.bootstrap_iterations < 1:
            raise ValueError("bootstrap_iterations must be >= 1")


@dataclass(frozen=True)
class GroupSummary:
    group_kind: str
    group_value: str
    event_count: int
    metrics: dict[str, dict[str, Any]]
    hypothesis_id: str | None
    hypothesis_class: str
    hypothesis_family: str
    warnings: list[str] = field(default_factory=list)


def _normal_two_sided_p(mean: float | None, std: float | None, n: int) -> float | None:
    """Two-sided p-value for H0: mean == 0, via a normal approximation.

    This is a coarse screen, not a rigorous inference procedure; overlapping
    horizons and cross-sectional dependence are not corrected here (documented in
    limitations). Returns None when dispersion is undefined.
    """
    if mean is None or std is None or std == 0.0 or n < 2:
        return None
    t = mean / (std / math.sqrt(n))
    return 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t) / math.sqrt(2.0))))


def detect_events(
    observations: list[NewsMarketObservation],
    config: EventStudyConfig,
) -> list[dict[str, Any]]:
    """Return event rows where the lag-only attention rule fires.

    Events require a defined attention z-score at or above the threshold. The
    z-score is computed from strictly prior sessions upstream, so the event
    definition cannot include the shock it is classifying.
    """
    events: list[dict[str, Any]] = []
    for obs in observations:
        z = obs.inputs.get(config.attention_field)
        if not isinstance(z, (int, float)) or isinstance(z, bool):
            continue
        if z < config.threshold:
            continue
        dup_ratio = obs.inputs.get("news_duplicate_ratio")
        eff_domains = obs.inputs.get("news_observed_copy_effective_domain_count")
        prior_vol = obs.inputs.get("mkt_prior_trailing_close_to_close_vol_20")
        row: dict[str, Any] = {
            "observation_id": obs.observation_id,
            "session_date": obs.session_date.isoformat(),
            "topic": obs.topic,
            "symbol": obs.symbol,
            "relationship": obs.lineage.get("relationship"),
            "news_measurement_method": obs.news_measurement_method,
            "attention_zscore": float(z),
            "duplicate_ratio": dup_ratio,
            "observed_copy_effective_domain_count": eff_domains,
            "prior_volatility": prior_vol,
            "pre_event_return_5": obs.inputs.get("mkt_prior_trailing_return_5"),
            "market_coverage_status": obs.quality.get("market_coverage_status"),
        }
        for response in _RESPONSE_FIELDS:
            row[response] = obs.targets.get(response)
        events.append(row)
    events.sort(key=lambda e: (e["session_date"], e["topic"], e["symbol"]))
    return events


def _summarize_group(
    kind: str,
    value: str,
    rows: list[dict[str, Any]],
    config: EventStudyConfig,
    *,
    hypothesis_id: str | None,
    hypothesis_class: str,
    hypothesis_family: str,
    formal_responses: tuple[str, ...] = (),
) -> GroupSummary:
    metrics: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for response in _RESPONSE_FIELDS:
        values = [
            float(r[response])
            for r in rows
            if isinstance(r.get(response), (int, float)) and not isinstance(r.get(response), bool)
        ]
        summary = stats.summarize(values, confidence=config.confidence, seed=config.seed)
        by_session: dict[str, list[float]] = {}
        for row in rows:
            value_at_row = row.get(response)
            if isinstance(value_at_row, (int, float)) and not isinstance(value_at_row, bool):
                by_session.setdefault(str(row["session_date"]), []).append(float(value_at_row))
        session_means = [
            session_mean
            for key in sorted(by_session)
            if (session_mean := stats.mean(by_session[key])) is not None
        ]
        inference_n_sessions = len(session_means)
        eligible = (
            hypothesis_class == "confirmatory"
            and response in formal_responses
            and inference_n_sessions >= config.minimum_sample
        )
        if eligible:
            ci_low, ci_high = stats.session_date_block_bootstrap_ci(
                by_session,
                confidence=config.confidence,
                iterations=config.bootstrap_iterations,
                seed=config.seed,
                block_length=config.bootstrap_block_length,
            )
            p_value = _normal_two_sided_p(
                stats.mean(session_means),
                stats.sample_std(session_means),
                inference_n_sessions,
            )
            inferential_status = "eligible"
        elif hypothesis_class != "confirmatory" or response not in formal_responses:
            ci_low, ci_high = (None, None)
            p_value = None
            inferential_status = "descriptive_only"
        else:
            ci_low, ci_high = (None, None)
            p_value = None
            inferential_status = "insufficient_sample"
        metrics[response] = {
            "n": summary.count,
            "inference_n_sessions": inference_n_sessions,
            "mean": summary.mean,
            "median": summary.median,
            "std": summary.std,
            "minimum": summary.minimum,
            "maximum": summary.maximum,
            "ci_low": ci_low,
            "ci_high": ci_high,
            "ci_scope": "session_date_moving_block" if eligible else None,
            "positive_fraction": summary.positive_fraction,
            "p_value_uncorrected": p_value,
            "adjusted_p_value": None,
            "inferential_status": inferential_status,
            "missing": len(rows) - summary.count,
        }
    if any(metric["inferential_status"] == "insufficient_sample" for metric in metrics.values()):
        warnings.append(
            f"fewer than {config.minimum_sample} valid session-date clusters; "
            "formal inference omitted"
        )
    return GroupSummary(
        kind,
        value,
        len(rows),
        metrics,
        hypothesis_id,
        hypothesis_class,
        hypothesis_family,
        warnings,
    )


def _bucket_median_split(
    rows: list[dict[str, Any]], field_name: str, low_label: str, high_label: str
) -> list[tuple[str, list[dict[str, Any]]]]:
    values = [
        float(r[field_name])
        for r in rows
        if isinstance(r.get(field_name), (int, float)) and not isinstance(r.get(field_name), bool)
    ]
    median = stats.percentile(values, 0.5)
    if median is None:
        return []
    low = [
        r
        for r in rows
        if isinstance(r.get(field_name), (int, float))
        and not isinstance(r.get(field_name), bool)
        and float(r[field_name]) <= median
    ]
    high = [
        r
        for r in rows
        if isinstance(r.get(field_name), (int, float))
        and not isinstance(r.get(field_name), bool)
        and float(r[field_name]) > median
    ]
    return [(low_label, low), (high_label, high)]


def run_event_study(
    observations: list[NewsMarketObservation],
    config: EventStudyConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Compute the full event-study summary and event rows (deterministic)."""
    events = detect_events(observations, config)

    groups: list[GroupSummary] = [
        _summarize_group(
            "all",
            "all_events",
            events,
            config,
            hypothesis_id="H1",
            hypothesis_class="confirmatory",
            hypothesis_family=H1_FAMILY,
            formal_responses=H1_RESPONSES,
        )
    ]

    def add_grouping(
        kind: str,
        key: str,
        *,
        hypothesis_id: str | None,
        hypothesis_family: str,
    ) -> None:
        buckets: dict[str, list[dict[str, Any]]] = {}
        for row in events:
            value = row.get(key)
            label = str(value) if value is not None else "unknown"
            buckets.setdefault(label, []).append(row)
        for label in sorted(buckets):
            groups.append(
                _summarize_group(
                    kind,
                    label,
                    buckets[label],
                    config,
                    hypothesis_id=hypothesis_id,
                    hypothesis_class="exploratory",
                    hypothesis_family=hypothesis_family,
                )
            )

    add_grouping(
        "topic",
        "topic",
        hypothesis_id="H5",
        hypothesis_family="exploratory_spillover_v1",
    )
    add_grouping(
        "symbol",
        "symbol",
        hypothesis_id="H5",
        hypothesis_family="exploratory_spillover_v1",
    )
    add_grouping(
        "relationship",
        "relationship",
        hypothesis_id="H5",
        hypothesis_family="exploratory_spillover_v1",
    )

    for label, rows in _bucket_median_split(
        events, "duplicate_ratio", "low_syndication", "high_syndication"
    ):
        groups.append(
            _summarize_group(
                "syndication",
                label,
                rows,
                config,
                hypothesis_id="H2",
                hypothesis_class="confirmatory",
                hypothesis_family=H2_FAMILY,
                # H2 needs a predeclared between-group contrast, not separate
                # one-sample p-values. V2 emits these groups descriptively.
            )
        )
    for label, rows in _bucket_median_split(
        events, "prior_volatility", "low_prior_vol", "high_prior_vol"
    ):
        groups.append(
            _summarize_group(
                "prior_volatility",
                label,
                rows,
                config,
                hypothesis_id="H4",
                hypothesis_class="exploratory",
                hypothesis_family="exploratory_attention_prior_volatility_v1",
            )
        )
    for label, rows in _bucket_median_split(
        events,
        "observed_copy_effective_domain_count",
        "concentrated_sources",
        "broad_sources",
    ):
        groups.append(
            _summarize_group(
                "source_breadth",
                label,
                rows,
                config,
                hypothesis_id="H2",
                hypothesis_class="confirmatory",
                hypothesis_family=H2_FAMILY,
            )
        )

    h1_group = groups[0]
    h1_tests: list[tuple[str, float]] = []
    for response in H1_RESPONSES:
        p_value = h1_group.metrics[response]["p_value_uncorrected"]
        if isinstance(p_value, (int, float)):
            h1_tests.append((response, float(p_value)))
    h1_p_values = [p_value for _, p_value in h1_tests]
    h1_adjusted = stats.benjamini_hochberg_adjusted(h1_p_values)
    h1_rejected = stats.benjamini_hochberg(h1_p_values, alpha=config.fdr_alpha)
    h1_results: list[dict[str, Any]] = []
    for (response, p_value), adjusted, rejected in zip(h1_tests, h1_adjusted, h1_rejected):
        h1_group.metrics[response]["adjusted_p_value"] = adjusted
        h1_results.append(
            {
                "response": response,
                "p_value_uncorrected": p_value,
                "adjusted_p_value": adjusted,
                "rejected_after_bh": bool(rejected),
            }
        )
    fdr_families = [
        {
            "hypothesis_id": "H1",
            "hypothesis_class": "confirmatory",
            "hypothesis_family": H1_FAMILY,
            "fdr_alpha": config.fdr_alpha,
            "eligible_tests": len(h1_results),
            "results": h1_results,
        },
        {
            "hypothesis_id": "H2",
            "hypothesis_class": "confirmatory",
            "hypothesis_family": H2_FAMILY,
            "fdr_alpha": config.fdr_alpha,
            "eligible_tests": 0,
            "results": [],
            "note": "No formal H2 contrast is implemented; subgroup summaries are descriptive.",
        },
    ]

    return {
        "config": {
            "attention_field": config.attention_field,
            "threshold": config.threshold,
            "minimum_sample": config.minimum_sample,
            "forward_horizon": config.forward_horizon,
            "confidence": config.confidence,
            "fdr_alpha": config.fdr_alpha,
            "bootstrap_unit": config.bootstrap_unit,
            "bootstrap_block_length": config.bootstrap_block_length,
            "bootstrap_seed": config.seed,
            "bootstrap_iterations": config.bootstrap_iterations,
        },
        "hypothesis_registry_version": HYPOTHESIS_REGISTRY_VERSION,
        "hypotheses": list(HYPOTHESIS_REGISTRY),
        "event_count": len(events),
        "response_fields": list(_RESPONSE_FIELDS),
        "groups": [
            {
                "group_kind": g.group_kind,
                "group_value": g.group_value,
                "event_count": g.event_count,
                "hypothesis_id": g.hypothesis_id,
                "hypothesis_class": g.hypothesis_class,
                "hypothesis_family": g.hypothesis_family,
                "metrics": g.metrics,
                "warnings": g.warnings,
            }
            for g in groups
        ],
        "fdr_families": fdr_families,
        "disclaimer": (
            "Associations only. This is not evidence that news caused any price "
            "movement, and profitability is not tested."
        ),
    }, events

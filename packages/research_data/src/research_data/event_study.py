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

from dataclasses import dataclass, field
from datetime import date
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
# Secondary, benchmark-relative endpoints. Reported for context and computed with
# the same event-vs-control contrast, but deliberately NOT part of the primary H1
# endpoint family and never BH-adjusted inside it.
H1_SECONDARY_RESPONSES = (
    "target_session_benchmark_adjusted_return",
    "target_through_plus_4_cumulative_benchmark_adjusted_return",
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
    # Optional declared study sample. Observations whose session date falls
    # outside [study_window_start, study_window_end] are excluded from both the
    # event and control groups so extra market-history / target-completion bars do
    # not enter the comparison. Stored as ISO ``YYYY-MM-DD`` strings or None.
    study_window_start: str | None = None
    study_window_end: str | None = None

    def __post_init__(self) -> None:
        if self.forward_horizon != 5:
            raise ValueError("Core V2 event study supports forward_horizon=5 only")
        if self.bootstrap_unit != "session_date":
            raise ValueError("Core V2 requires bootstrap_unit='session_date'")
        if self.bootstrap_block_length < 1:
            raise ValueError("bootstrap_block_length must be >= 1")
        if self.bootstrap_iterations < 1:
            raise ValueError("bootstrap_iterations must be >= 1")
        for label, value in (
            ("study_window_start", self.study_window_start),
            ("study_window_end", self.study_window_end),
        ):
            if value is not None:
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError(f"{label} must be an ISO YYYY-MM-DD date") from exc
        if (
            self.study_window_start is not None
            and self.study_window_end is not None
            and self.study_window_start > self.study_window_end
        ):
            raise ValueError("study_window_start must not be after study_window_end")

    def in_study_window(self, session_date: date) -> bool:
        """Whether ``session_date`` falls inside the declared study sample."""
        iso = session_date.isoformat()
        if self.study_window_start is not None and iso < self.study_window_start:
            return False
        if self.study_window_end is not None and iso > self.study_window_end:
            return False
        return True


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


# --- Event-versus-control contrast (primary H1 test) -------------------------
#
# The event-only summaries above are descriptive: they say what event sessions
# looked like, but they do NOT test the hypothesis that attention-event sessions
# move more than ordinary sessions. The hypothesis is a *comparison*. The
# functions below build an explicit event-vs-control contrast:
#
#   * an EVENT observation has a defined 30-session attention z-score >= threshold;
#   * a CONTROL observation has a defined 30-session attention z-score < threshold
#     (an "ordinary" session with valid attention history);
#   * observations without a defined 30-session z-score are neither (they cannot be
#     classified as event or non-event), and are excluded from the contrast.
#
# Both groups share the same topic and news-measurement method, must have the
# response available (a complete target), and must fall inside the declared study
# sample. Symbol-specific contrasts never mix symbols; pooled contrasts aggregate
# within session date first so one date with AMD+NVDA+SMH is one cluster, not three.

_GROUP_EVENT = "event"
_GROUP_CONTROL = "control"
_GROUP_UNCLASSIFIED = "unclassified"


def classify_attention_group(obs: NewsMarketObservation, config: EventStudyConfig) -> str:
    """Classify one observation as event / control / unclassified (lag-only).

    Uses only the strictly-prior 30-session attention z-score, so classification
    cannot depend on the target session it is being compared on.
    """
    z = obs.inputs.get(config.attention_field)
    if not isinstance(z, (int, float)) or isinstance(z, bool):
        return _GROUP_UNCLASSIFIED
    return _GROUP_EVENT if float(z) >= config.threshold else _GROUP_CONTROL


def _response_value(obs: NewsMarketObservation, response: str) -> float | None:
    value = obs.targets.get(response)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _cluster_by_session_date(
    rows: list[NewsMarketObservation], response: str
) -> dict[str, list[float]]:
    by_session: dict[str, list[float]] = {}
    for obs in rows:
        value = _response_value(obs, response)
        if value is not None:
            by_session.setdefault(obs.session_date.isoformat(), []).append(value)
    return by_session


def _cluster_mean_of_means(by_session: dict[str, list[float]]) -> float | None:
    cluster_means = [
        cluster_mean
        for key in sorted(by_session)
        if (cluster_mean := stats.mean(by_session[key])) is not None
    ]
    return stats.mean(cluster_means)


def _contrast_for_response(
    *,
    topic: str,
    news_measurement_method: str,
    grouping: str,
    group_value: str,
    response: str,
    event_rows: list[NewsMarketObservation],
    control_rows: list[NewsMarketObservation],
    config: EventStudyConfig,
) -> dict[str, Any]:
    event_by_session = _cluster_by_session_date(event_rows, response)
    control_by_session = _cluster_by_session_date(control_rows, response)
    event_date_count = len(event_by_session)
    control_date_count = len(control_by_session)
    event_row_count = sum(len(v) for v in event_by_session.values())
    control_row_count = sum(len(v) for v in control_by_session.values())
    event_mean = _cluster_mean_of_means(event_by_session)
    control_mean = _cluster_mean_of_means(control_by_session)

    difference: float | None = None
    if event_mean is not None and control_mean is not None:
        difference = event_mean - control_mean
    ratio: float | None = None
    if event_mean is not None and control_mean is not None and control_mean != 0.0:
        ratio = event_mean / control_mean

    in_h1_family = response in H1_RESPONSES
    sufficient = (
        event_date_count >= config.minimum_sample and control_date_count >= config.minimum_sample
    )

    ci_low: float | None = None
    ci_high: float | None = None
    ci_method: str | None = None
    p_value: float | None = None
    if sufficient:
        ci_low, ci_high = stats.two_sample_session_block_bootstrap_difference_ci(
            event_by_session,
            control_by_session,
            confidence=config.confidence,
            iterations=config.bootstrap_iterations,
            seed=config.seed,
            block_length=config.bootstrap_block_length,
        )
        ci_method = "session_date_moving_block_two_sample"
        event_cluster_means = [
            m
            for key in sorted(event_by_session)
            if (m := stats.mean(event_by_session[key])) is not None
        ]
        control_cluster_means = [
            m
            for key in sorted(control_by_session)
            if (m := stats.mean(control_by_session[key])) is not None
        ]
        p_value = stats.welch_two_sample_p(
            stats.mean(event_cluster_means),
            stats.sample_std(event_cluster_means),
            len(event_cluster_means),
            stats.mean(control_cluster_means),
            stats.sample_std(control_cluster_means),
            len(control_cluster_means),
        )
        inferential_status = "eligible"
    else:
        inferential_status = "insufficient_sample"

    return {
        "topic": topic,
        "news_measurement_method": news_measurement_method,
        "grouping": grouping,
        "group_value": group_value,
        "response": response,
        "hypothesis_id": "H1",
        "hypothesis_family": H1_FAMILY if in_h1_family else None,
        "in_h1_family": in_h1_family,
        "minimum_sample": config.minimum_sample,
        "event_date_count": event_date_count,
        "control_date_count": control_date_count,
        "event_row_count": event_row_count,
        "control_row_count": control_row_count,
        "event_mean": event_mean,
        "control_mean": control_mean,
        "difference": difference,
        "ratio": ratio,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "ci_method": ci_method,
        "p_value_uncorrected": p_value,
        "adjusted_p_value": None,
        "inferential_status": inferential_status,
    }


def build_event_control_contrasts(
    observations: list[NewsMarketObservation],
    config: EventStudyConfig,
    *,
    responses: tuple[str, ...] = H1_RESPONSES + H1_SECONDARY_RESPONSES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build event-vs-control contrasts and the H1 BH-FDR family.

    Returns ``(contrasts, fdr_family)``. Contrasts are produced per
    ``(topic, news_measurement_method)`` partition, for a pooled grouping (all
    symbols, aggregated within session date) and for each individual symbol.
    Only eligible H1-family contrasts (both groups at or above ``minimum_sample``
    independent session dates, and a primary H1 response) receive an adjusted
    p-value; every other contrast is descriptive with a null adjusted p-value.
    """
    # Partition observations by (topic, method), classifying each as event/control.
    partitions: dict[tuple[str, str], dict[str, list[NewsMarketObservation]]] = {}
    for obs in observations:
        if not config.in_study_window(obs.session_date):
            continue
        group = classify_attention_group(obs, config)
        if group == _GROUP_UNCLASSIFIED:
            continue
        key = (obs.topic, obs.news_measurement_method)
        partitions.setdefault(key, {_GROUP_EVENT: [], _GROUP_CONTROL: []})
        partitions[key][group].append(obs)

    contrasts: list[dict[str, Any]] = []
    for topic, method in sorted(partitions):
        event_rows = partitions[(topic, method)][_GROUP_EVENT]
        control_rows = partitions[(topic, method)][_GROUP_CONTROL]
        symbols = sorted({o.symbol for o in event_rows + control_rows})

        groupings: list[
            tuple[str, str, list[NewsMarketObservation], list[NewsMarketObservation]]
        ] = [
            ("pooled", "all_symbols", event_rows, control_rows),
        ]
        for symbol in symbols:
            groupings.append(
                (
                    "symbol",
                    symbol,
                    [o for o in event_rows if o.symbol == symbol],
                    [o for o in control_rows if o.symbol == symbol],
                )
            )

        for grouping, group_value, ev_rows, ctl_rows in groupings:
            for response in responses:
                contrasts.append(
                    _contrast_for_response(
                        topic=topic,
                        news_measurement_method=method,
                        grouping=grouping,
                        group_value=group_value,
                        response=response,
                        event_rows=ev_rows,
                        control_rows=ctl_rows,
                        config=config,
                    )
                )

    # BH-FDR only across eligible primary H1-family contrasts.
    eligible = [
        contrast
        for contrast in contrasts
        if contrast["in_h1_family"]
        and contrast["inferential_status"] == "eligible"
        and isinstance(contrast["p_value_uncorrected"], (int, float))
    ]
    p_values = [float(contrast["p_value_uncorrected"]) for contrast in eligible]
    adjusted = stats.benjamini_hochberg_adjusted(p_values)
    rejected = stats.benjamini_hochberg(p_values, alpha=config.fdr_alpha)
    fdr_results: list[dict[str, Any]] = []
    for contrast, adj, rej in zip(eligible, adjusted, rejected):
        contrast["adjusted_p_value"] = adj
        fdr_results.append(
            {
                "topic": contrast["topic"],
                "news_measurement_method": contrast["news_measurement_method"],
                "grouping": contrast["grouping"],
                "group_value": contrast["group_value"],
                "response": contrast["response"],
                "p_value_uncorrected": contrast["p_value_uncorrected"],
                "adjusted_p_value": adj,
                "rejected_after_bh": bool(rej),
            }
        )

    fdr_family = {
        "hypothesis_id": "H1",
        "hypothesis_class": "confirmatory",
        "hypothesis_family": H1_FAMILY,
        "contrast_kind": "event_vs_control_difference",
        "fdr_alpha": config.fdr_alpha,
        "eligible_tests": len(fdr_results),
        "results": fdr_results,
    }
    return contrasts, fdr_family


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
        # These are DESCRIPTIVE event-only summaries. They intentionally do NOT
        # test whether the event mean differs from zero: that is not the H1
        # hypothesis. The confirmatory H1 test is the event-vs-control contrast
        # (see build_event_control_contrasts). A session-date bootstrap interval
        # around the event mean is reported as a descriptive interval only.
        show_descriptive_ci = (
            response in formal_responses and inference_n_sessions >= config.minimum_sample
        )
        if show_descriptive_ci:
            ci_low, ci_high = stats.session_date_block_bootstrap_ci(
                by_session,
                confidence=config.confidence,
                iterations=config.bootstrap_iterations,
                seed=config.seed,
                block_length=config.bootstrap_block_length,
            )
            ci_scope = "session_date_moving_block_descriptive"
        else:
            ci_low, ci_high = (None, None)
            ci_scope = None
        inferential_status = (
            "descriptive"
            if inference_n_sessions >= config.minimum_sample
            else "descriptive_small_sample"
        )
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
            "ci_scope": ci_scope,
            "positive_fraction": summary.positive_fraction,
            "p_value_uncorrected": None,
            "adjusted_p_value": None,
            "inferential_status": inferential_status,
            "missing": len(rows) - summary.count,
        }
    if any(
        metric["inferential_status"] == "descriptive_small_sample" for metric in metrics.values()
    ):
        warnings.append(
            f"fewer than {config.minimum_sample} valid session-date clusters; "
            "descriptive event-only summary"
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

    # The confirmatory H1 test is the event-vs-control contrast, not a one-sample
    # test of the event mean against zero. Build it here.
    contrasts, h1_contrast_family = build_event_control_contrasts(observations, config)

    fdr_families = [
        h1_contrast_family,
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
        "event_only_summary_kind": (
            "descriptive_event_only; not a test of the event mean against zero and "
            "not the H1 hypothesis test"
        ),
        "event_control_contrasts": contrasts,
        "primary_h1_endpoint_family": {
            "hypothesis_id": "H1",
            "hypothesis_family": H1_FAMILY,
            "contrast_kind": "event_vs_control_difference",
            "primary_responses": list(H1_RESPONSES),
            "secondary_responses": list(H1_SECONDARY_RESPONSES),
            "threshold": config.threshold,
            "attention_field": config.attention_field,
            "minimum_sample": config.minimum_sample,
            "predeclared": True,
        },
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

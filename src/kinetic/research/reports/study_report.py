"""Deterministic Markdown study report and machine-readable contrast exports.

This module turns the event-study summary (with its event-vs-control contrasts)
and dataset coverage into a human-readable study report plus flat
``event_control_contrasts.{json,csv}`` artifacts.

The wording here is deliberately constrained: it describes *associations* and
*differences*, never profitability, predictive alpha, causation, or trading
success. Interpretation phrases are drawn from a fixed vocabulary so the report
cannot silently overclaim.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from kinetic.research.datasets.builder import DatasetResult
from kinetic.research.event_studies.event_study import H1_RESPONSES, H1_SECONDARY_RESPONSES

# Fixed interpretation vocabulary. These are the ONLY result phrases used.
INTERP_SUPPORTS = "evidence supports an association"
INTERP_INCONCLUSIVE = "result is inconclusive"
INTERP_NO_DIFFERENCE = "no detectable difference"
INTERP_INSUFFICIENT = "insufficient sample"

_RESPONSE_LABELS = {
    "target_session_absolute_return": "target-session absolute return",
    "target_session_parkinson_variance": "target-session Parkinson variance",
    "target_session_benchmark_adjusted_return": "target-session benchmark-adjusted return",
    "target_through_plus_4_cumulative_benchmark_adjusted_return": (
        "target-through-plus-four cumulative benchmark-adjusted return"
    ),
}

CONTRAST_CSV_FIELDS = [
    "topic",
    "news_measurement_method",
    "grouping",
    "group_value",
    "response",
    "in_h1_family",
    "event_date_count",
    "control_date_count",
    "event_row_count",
    "control_row_count",
    "event_mean",
    "control_mean",
    "difference",
    "ratio",
    "ci_low",
    "ci_high",
    "ci_method",
    "p_value_uncorrected",
    "adjusted_p_value",
    "inferential_status",
    "interpretation",
]


def _fmt(value: Any, digits: int = 6) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{float(value):.{digits}g}"
    return str(value)


def interpret_contrast(contrast: dict[str, Any]) -> str:
    """Map a contrast to one fixed, non-overclaiming interpretation phrase."""
    if contrast["inferential_status"] != "eligible":
        return INTERP_INSUFFICIENT
    ci_low = contrast.get("ci_low")
    ci_high = contrast.get("ci_high")
    if ci_low is None or ci_high is None:
        return INTERP_INCONCLUSIVE
    # A difference CI that excludes zero (both bounds on the same side) is read as
    # support for an association; one that straddles zero is no detectable difference.
    if ci_low > 0.0 or ci_high < 0.0:
        return INTERP_SUPPORTS
    return INTERP_NO_DIFFERENCE


def contrast_rows(contrasts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten contrasts into deterministic CSV-ready rows with interpretations."""
    rows: list[dict[str, Any]] = []
    for contrast in contrasts:
        row = {
            field: contrast.get(field) for field in CONTRAST_CSV_FIELDS if field != "interpretation"
        }
        row["interpretation"] = interpret_contrast(contrast)
        rows.append(row)
    rows.sort(
        key=lambda r: (
            str(r["topic"]),
            str(r["news_measurement_method"]),
            0 if r["grouping"] == "pooled" else 1,
            str(r["group_value"]),
            str(r["response"]),
        )
    )
    return rows


def study_coverage(
    result: DatasetResult,
    *,
    requested_news_window: tuple[str, str] | None,
    requested_market_window: tuple[str, str] | None,
    study_window: tuple[str, str] | None,
    benchmark_symbols: Sequence[str],
    bigquery_cost: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the data-coverage block from a built dataset result."""
    observations = result.observations
    market = result.market_session_features
    daily = result.news_topic_daily_features

    market_sessions = sorted({m.session_date.isoformat() for m in market})
    symbols_present = sorted({m.symbol for m in market})
    obs_sessions = sorted({o.session_date.isoformat() for o in observations})

    missing_bars = sum(
        1 for o in observations if o.quality.get("market_coverage_status") == "market_bar_missing"
    )
    target_session_complete = sum(
        1 for o in observations if o.quality.get("target_session_complete")
    )
    target_through_plus_4_complete = sum(
        1 for o in observations if o.quality.get("target_through_plus_4_complete")
    )
    valid_attention_history = sum(
        1
        for o in observations
        if isinstance(o.inputs.get("news_attention_zscore_30"), (int, float))
        and not isinstance(o.inputs.get("news_attention_zscore_30"), bool)
    )
    benchmark_available = sum(1 for o in observations if o.quality.get("benchmark_available"))

    news_dates = sorted({d.feature_date.isoformat() for d in daily})

    lineage = {
        "market_provider": sorted({o.market_provider for o in observations}),
        "feed": sorted({o.feed for o in observations}),
        "adjustment": sorted({o.adjustment for o in observations}),
        "currency": sorted({o.currency for o in observations}),
        "timeframe": sorted({o.timeframe for o in observations}),
        "news_measurement_methods": list(result.manifest.news_measurement_methods),
        "alignment_policies": list(result.manifest.alignment_policies),
        "alignment_precision_counts": dict(result.manifest.alignment_precision_counts),
    }

    return {
        "requested_news_window": list(requested_news_window) if requested_news_window else None,
        "requested_market_window": (
            list(requested_market_window) if requested_market_window else None
        ),
        "declared_study_window": list(study_window) if study_window else None,
        "news_count_days": len(news_dates),
        "news_earliest_date": news_dates[0] if news_dates else None,
        "news_latest_date": news_dates[-1] if news_dates else None,
        "market_session_count": len(market_sessions),
        "market_earliest_date": market_sessions[0] if market_sessions else None,
        "market_latest_date": market_sessions[-1] if market_sessions else None,
        "observation_earliest_date": obs_sessions[0] if obs_sessions else None,
        "observation_latest_date": obs_sessions[-1] if obs_sessions else None,
        "symbols_present": symbols_present,
        "benchmark_symbols": sorted(set(benchmark_symbols)),
        "benchmark_symbols_present": sorted(set(benchmark_symbols) & set(symbols_present)),
        "benchmark_available_observations": benchmark_available,
        "missing_bars": missing_bars,
        "observation_count": len(observations),
        "target_session_complete_observations": target_session_complete,
        "target_through_plus_4_complete_observations": target_through_plus_4_complete,
        "observations_with_valid_30_session_attention": valid_attention_history,
        "lineage": lineage,
        "bigquery_cost": bigquery_cost,
    }


def _contrast_index(
    contrasts: Sequence[dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for contrast in contrasts:
        key = (str(contrast["grouping"]), str(contrast["group_value"]), str(contrast["response"]))
        index[key] = contrast
    return index


def _result_table(
    contrasts: Sequence[dict[str, Any]],
    responses: Sequence[str],
    group_order: Sequence[tuple[str, str, str]],
) -> list[str]:
    """Render a Markdown table for a set of (grouping, group_value, label) rows."""
    index = _contrast_index(contrasts)
    lines = [
        "| Group | Response | Event dates | Control dates | Event mean | Control mean "
        "| Difference | Ratio | Block-bootstrap 95% CI | Adjusted p | Status | Interpretation |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for grouping, group_value, label in group_order:
        for response in responses:
            contrast = index.get((grouping, group_value, response))
            if contrast is None:
                continue
            ci = (
                f"[{_fmt(contrast['ci_low'])}, {_fmt(contrast['ci_high'])}]"
                if contrast.get("ci_low") is not None
                else "n/a"
            )
            lines.append(
                "| {label} | {resp} | {ed} | {cd} | {em} | {cm} | {diff} | {ratio} | {ci} "
                "| {adj} | {status} | {interp} |".format(
                    label=label,
                    resp=_RESPONSE_LABELS.get(response, response),
                    ed=contrast["event_date_count"],
                    cd=contrast["control_date_count"],
                    em=_fmt(contrast["event_mean"]),
                    cm=_fmt(contrast["control_mean"]),
                    diff=_fmt(contrast["difference"]),
                    ratio=_fmt(contrast["ratio"]),
                    ci=ci,
                    adj=_fmt(contrast["adjusted_p_value"]),
                    status=contrast["inferential_status"],
                    interp=interpret_contrast(contrast),
                )
            )
    return lines


LIMITATIONS_BULLETS = [
    "Media coverage is a news-attention proxy, not observed investor attention.",
    "BigQuery daily counts use a daily approximation; intra-day publication timing is not modeled.",
    "Theme selection is researcher-defined and shapes which coverage is measured.",
    "Daily bars miss intraday reactions and intraday price discovery.",
    "Repeated or overlapping attention events create dependence across nearby sessions.",
    "Benchmark adjustment is a simple benchmark difference, not factor-model alpha.",
    "No transaction costs, slippage, or execution are modeled; no trading is performed.",
]


def render_study_report(
    *,
    title: str,
    topic: str,
    coverage: dict[str, Any],
    summary: dict[str, Any],
    config_params: dict[str, Any],
    generated_at: str,
) -> str:
    """Render the full Markdown study report deterministically."""
    contrasts = summary.get("event_control_contrasts", [])
    symbols = [
        s for s in coverage["symbols_present"] if s not in set(coverage["benchmark_symbols"])
    ]
    # Pooled first, then each instrument symbol (exclude benchmark-only symbols).
    instrument_symbols = sorted(
        {
            str(c["group_value"])
            for c in contrasts
            if c["grouping"] == "symbol" and str(c["group_value"]) in set(symbols)
        }
    )
    group_order: list[tuple[str, str, str]] = [("pooled", "all_symbols", "Pooled (all symbols)")]
    for sym in instrument_symbols:
        group_order.append(("symbol", sym, sym))

    threshold = summary.get("config", {}).get("threshold")
    attention_field = summary.get("config", {}).get("attention_field")
    minimum_sample = summary.get("config", {}).get("minimum_sample")

    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(
        "> Associations only. This report does not test profitability and is not "
        "evidence that news moved any price."
    )
    lines.append("")
    lines.append(f"- Topic: `{topic}`")
    lines.append(f"- Dataset version: `{config_params.get('dataset_version')}`")
    lines.append(f"- Generated at (fixed clock): `{generated_at}`")
    lines.append(
        f"- Event definition: `{attention_field} >= {threshold}` "
        "(lag-only; predeclared before inspecting market outcomes)"
    )
    lines.append(
        f"- Minimum independent session-date clusters for inference: `{minimum_sample}` per group"
    )
    lines.append("")

    # --- Data coverage -------------------------------------------------------
    lines.append("## Data coverage")
    lines.append("")
    cov = coverage
    lines.append(f"- Requested news measurement window: `{cov['requested_news_window']}`")
    lines.append(f"- Requested market-bar window: `{cov['requested_market_window']}`")
    lines.append(f"- Declared study window: `{cov['declared_study_window']}`")
    lines.append(
        f"- News count days: `{cov['news_count_days']}` "
        f"(earliest `{cov['news_earliest_date']}`, latest `{cov['news_latest_date']}`)"
    )
    lines.append(
        f"- Market sessions: `{cov['market_session_count']}` "
        f"(earliest `{cov['market_earliest_date']}`, latest `{cov['market_latest_date']}`)"
    )
    lines.append(f"- Symbols present: `{cov['symbols_present']}`")
    lines.append(
        f"- Benchmark coverage: benchmark(s) `{cov['benchmark_symbols']}`, "
        f"present `{cov['benchmark_symbols_present']}`, "
        f"observations with benchmark available `{cov['benchmark_available_observations']}`"
    )
    lines.append(f"- Missing bars (observations without a market bar): `{cov['missing_bars']}`")
    lines.append(f"- Observations: `{cov['observation_count']}`")
    lines.append(
        f"- Target-complete observations: session `{cov['target_session_complete_observations']}`, "
        f"through S+4 `{cov['target_through_plus_4_complete_observations']}`"
    )
    lines.append(
        "- Observations with valid 30-session attention history: "
        f"`{cov['observations_with_valid_30_session_attention']}`"
    )
    lines.append(f"- Provider/cache lineage: `{cov['lineage']}`")
    if cov.get("bigquery_cost"):
        bc = cov["bigquery_cost"]
        lines.append(
            "- BigQuery cost decision: "
            f"decision `{bc.get('decision')}`, estimated bytes `{bc.get('estimated_bytes')}`, "
            f"estimated GiB `{bc.get('estimated_gib')}`, "
            f"estimated USD `{bc.get('estimated_cost_usd')}`, dry_run `{bc.get('dry_run')}`"
        )
    else:
        lines.append("- BigQuery cost decision: `not available in this build`")
    lines.append("")

    # --- Event definition ----------------------------------------------------
    lines.append("## Event definition")
    lines.append("")
    lines.append(
        f"An attention event is a session whose lag-only 30-session attention "
        f"z-score `{attention_field}` is at or above `{threshold}`. This threshold "
        "was fixed as a predeclared rule before any market outcome was inspected. "
        "Control (ordinary) sessions are those with a defined 30-session attention "
        "z-score strictly below the threshold. Sessions without a defined 30-session "
        "history are neither event nor control and are excluded from the contrast."
    )
    lines.append("")

    # --- Primary H1 results --------------------------------------------------
    lines.append("## Primary H1 results (event vs control)")
    lines.append("")
    lines.append(
        "Primary responses are the target-session absolute return and target-session "
        "Parkinson variance. Each cell is an event-vs-control difference in "
        "session-date cluster means; pooled rows aggregate within session date first."
    )
    lines.append("")
    lines.extend(_result_table(contrasts, H1_RESPONSES, group_order))
    lines.append("")

    # --- Secondary outcomes --------------------------------------------------
    lines.append("## Secondary outcomes (not the primary test)")
    lines.append("")
    lines.append(
        "These benchmark-relative endpoints are reported for context only. They are "
        "not part of the primary H1 endpoint family and are never BH-adjusted inside it."
    )
    lines.append("")
    lines.extend(_result_table(contrasts, H1_SECONDARY_RESPONSES, group_order))
    lines.append("")

    # --- Interpretation ------------------------------------------------------
    lines.append("## Interpretation")
    lines.append("")
    lines.append(
        "Each contrast is summarized with one of a fixed set of phrases: "
        f"*{INTERP_SUPPORTS}*, *{INTERP_INCONCLUSIVE}*, *{INTERP_NO_DIFFERENCE}*, "
        f"or *{INTERP_INSUFFICIENT}*."
    )
    lines.append("")
    pooled_lines = []
    index = _contrast_index(contrasts)
    for response in H1_RESPONSES:
        contrast = index.get(("pooled", "all_symbols", response))
        if contrast is None:
            continue
        pooled_lines.append(
            f"- Pooled {_RESPONSE_LABELS.get(response, response)}: "
            f"{interpret_contrast(contrast)} "
            f"(event dates `{contrast['event_date_count']}`, "
            f"control dates `{contrast['control_date_count']}`)."
        )
    if pooled_lines:
        lines.extend(pooled_lines)
    else:
        lines.append("- No pooled primary contrast was available.")
    lines.append("")

    # --- Limitations ---------------------------------------------------------
    lines.append("## Limitations")
    lines.append("")
    for bullet in LIMITATIONS_BULLETS:
        lines.append(f"- {bullet}")
    lines.append("")

    return "\n".join(lines) + "\n"

"""Build leakage-aware joined research observations.

Each ``NewsMarketObservation`` couples a session-aligned news feature with the
market behavior of the *target* session (the first session that can react to the
news) and later sessions. Inputs are drawn strictly from information knowable
before the target session opens: the news window and the prior market session's
state. Same-session descriptive fields are kept as ``contemporaneous`` (never
predictors), and forward outcomes are ``targets`` with explicit completeness
flags.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Any

from research_data import stats
from research_data.calendar import MarketCalendar
from research_data.market_features import MarketSeries
from research_data.models import (
    COVERAGE_MISSING_BAR,
    COVERAGE_OK,
    NewsMarketObservation,
    SessionNewsFeature,
    TopicInstrumentMapping,
)

# Prior-market-state fields carried as inputs (all knowable before target open).
_PRIOR_FIELDS = (
    "simple_return",
    "trailing_return_5",
    "trailing_return_20",
    "trailing_close_to_close_vol_20",
    "rolling_parkinson_20",
    "volume_zscore_20",
    "log_volume",
    "dollar_volume",
    "amihud_illiquidity_proxy",
    "distance_from_mean_20",
    "prior_direction",
    "vwap_deviation",
)

# Same-session descriptive fields (prohibited as predictors of their own session).
_CONTEMPORANEOUS_FIELDS = (
    "volume",
    "log_volume",
    "dollar_volume",
    "trade_count",
    "vwap_deviation",
    "close_location",
    "volume_per_trade",
    "high_low_range",
    "true_range",
    "intraday_return",
)


@dataclass(frozen=True)
class JoinResult:
    observations: list[NewsMarketObservation]
    exclusions: dict[str, int]
    excluded_examples: list[dict[str, Any]]


def _observation_id(
    dataset_version: str,
    topic: str,
    instrument_id: str,
    session_date: date,
    mapping_version: str,
    method: str,
) -> str:
    payload = "|".join(
        [dataset_version, topic, instrument_id, session_date.isoformat(), mapping_version, method]
    )
    return "obs-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _forward_targets(
    series: MarketSeries,
    benchmark_series: MarketSeries | None,
    symbol: str,
    benchmark_symbol: str,
    session_date: date,
    horizon: int,
) -> dict[str, Any]:
    """Forward cumulative targets over ``horizon`` sessions starting at the target.

    Requires every future session to exist. A short horizon is reported as
    incomplete, never silently truncated.
    """
    prev = series.feature_by_offset(symbol, session_date, -1)
    offsets = [series.feature_by_offset(symbol, session_date, k) for k in range(horizon)]
    complete = prev is not None and all(f is not None for f in offsets)

    if horizon != 5:
        raise ValueError("Core V2 supports exactly five target sessions (S through S+4)")
    result: dict[str, Any] = {
        "target_through_plus_4_cumulative_return": None,
        "target_through_plus_4_cumulative_benchmark_adjusted_return": None,
        "target_through_plus_4_return_std": None,
        "target_through_plus_4_volume_ratio_to_prior_20": None,
        "target_through_plus_4_complete": complete,
    }
    if not complete or prev is None:
        return result

    base_close = prev.close
    end_close = offsets[-1].close if offsets[-1] else None
    if base_close and base_close > 0 and end_close:
        result["target_through_plus_4_cumulative_return"] = end_close / base_close - 1.0

    raw_returns = [_num(f.inputs.get("simple_return")) for f in offsets if f]
    inst_returns: list[float] = [r for r in raw_returns if r is not None]
    if len(inst_returns) >= 2:
        result["target_through_plus_4_return_std"] = stats.sample_std(inst_returns)

    # Forward volume shock vs the prior-20 mean volume ending before the target.
    prior_volumes: list[float] = []
    for k in range(1, 21):
        f = series.feature_by_offset(symbol, session_date, -k)
        if f is None:
            break
        prior_volumes.append(float(f.volume))
    fwd_volumes = [float(f.volume) for f in offsets if f]
    prior_mean = stats.mean(prior_volumes)
    fwd_mean = stats.mean(fwd_volumes)
    if prior_mean and prior_mean > 0 and fwd_mean is not None:
        result["target_through_plus_4_volume_ratio_to_prior_20"] = fwd_mean / prior_mean

    # Cumulative benchmark-adjusted return over S..S+4. This is not alpha
    # and does not use an estimated expected-return model.
    if benchmark_series is not None and benchmark_series.has_symbol(benchmark_symbol):
        car = 0.0
        ok = True
        for k in range(horizon):
            inst = series.feature_by_offset(symbol, session_date, k)
            bench = benchmark_series.feature_by_offset(benchmark_symbol, session_date, k)
            ri = _num(inst.inputs.get("simple_return")) if inst else None
            rb = _num(bench.inputs.get("simple_return")) if bench else None
            if ri is None or rb is None:
                ok = False
                break
            car += ri - rb
        if ok:
            result["target_through_plus_4_cumulative_benchmark_adjusted_return"] = car
    return result


def build_observations(
    *,
    session_news: list[SessionNewsFeature],
    market_series: MarketSeries,
    benchmark_series: MarketSeries,
    mappings: tuple[TopicInstrumentMapping, ...],
    calendar: MarketCalendar,
    dataset_version: str,
    forward_horizon: int = 5,
    retain_missing_market: bool = True,
) -> JoinResult:
    """Join session news features to market sessions via versioned mappings."""
    observations: list[NewsMarketObservation] = []
    exclusions: dict[str, int] = {}
    excluded_examples: list[dict[str, Any]] = []

    def bump(reason: str, example: dict[str, Any] | None = None) -> None:
        exclusions[reason] = exclusions.get(reason, 0) + 1
        if example is not None and len(excluded_examples) < 25:
            excluded_examples.append({"reason": reason, **example})

    for news in session_news:
        session_date = news.session_date
        active = [m for m in mappings if m.topic == news.topic and m.is_active_on(session_date)]
        if not active:
            bump("mapping_inactive", {"topic": news.topic, "session": session_date.isoformat()})
            continue

        for mapping in active:
            symbol = mapping.symbol
            instrument_feature = market_series.feature_at(symbol, session_date)
            market_missing = instrument_feature is None
            if market_missing and not retain_missing_market:
                bump("market_bar_missing", {"symbol": symbol, "session": session_date.isoformat()})
                continue

            prior = market_series.feature_by_offset(symbol, session_date, -1)
            bench_symbol = mapping.benchmark_symbol
            bench_feature = benchmark_series.feature_at(bench_symbol, session_date)

            # Inputs: news + prior market state + a few predeclared interactions.
            inputs: dict[str, Any] = {
                f"news_{k}": v for k, v in news.inputs.items() if k != "attention_history_len"
            }
            if prior is not None:
                for field_name in _PRIOR_FIELDS:
                    inputs[f"mkt_prior_{field_name}"] = prior.inputs.get(field_name)
            else:
                for field_name in _PRIOR_FIELDS:
                    inputs[f"mkt_prior_{field_name}"] = None

            attn30 = _num(news.inputs.get("attention_zscore_30"))
            prior_vol = _num(inputs.get("mkt_prior_trailing_close_to_close_vol_20"))
            dup_ratio = _num(news.inputs.get("duplicate_ratio"))
            eff_domains = _num(news.inputs.get("observed_copy_effective_domain_count"))
            inputs["inter_attn30_x_prior_vol"] = (
                attn30 * prior_vol if (attn30 is not None and prior_vol is not None) else None
            )
            inputs["inter_attn30_x_dup_ratio"] = (
                attn30 * dup_ratio if (attn30 is not None and dup_ratio is not None) else None
            )
            inputs["inter_attn30_x_observed_copy_effective_domains"] = (
                attn30 * eff_domains if (attn30 is not None and eff_domains is not None) else None
            )

            # Contemporaneous descriptive (same-session; not predictors).
            contemporaneous: dict[str, Any] = {}
            if instrument_feature is not None:
                for field_name in _CONTEMPORANEOUS_FIELDS:
                    contemporaneous[f"mkt_{field_name}"] = instrument_feature.inputs.get(
                        field_name, getattr(instrument_feature, field_name, None)
                    )

            # Targets (forward from the target session).
            targets: dict[str, Any] = {}
            if instrument_feature is not None:
                inst_ret = _num(instrument_feature.inputs.get("simple_return"))
                bench_ret = (
                    _num(bench_feature.inputs.get("simple_return")) if bench_feature else None
                )
                targets["target_session_return"] = inst_ret
                targets["target_session_log_return"] = _num(
                    instrument_feature.inputs.get("log_return")
                )
                targets["target_session_absolute_return"] = _num(
                    instrument_feature.inputs.get("absolute_return")
                )
                targets["target_session_high_low_range"] = _num(
                    instrument_feature.inputs.get("high_low_range")
                )
                targets["target_session_parkinson_variance"] = _num(
                    instrument_feature.inputs.get("parkinson_variance")
                )
                targets["target_session_benchmark_adjusted_return"] = (
                    inst_ret - bench_ret
                    if (inst_ret is not None and bench_ret is not None)
                    else None
                )
                targets["target_session_benchmark_return"] = bench_ret
                targets["target_session_complete"] = inst_ret is not None
                targets.update(
                    _forward_targets(
                        market_series,
                        benchmark_series,
                        symbol,
                        bench_symbol,
                        session_date,
                        forward_horizon,
                    )
                )
            else:
                targets["target_session_complete"] = False
                targets["target_through_plus_4_complete"] = False
            target_session_complete = bool(targets.pop("target_session_complete"))
            target_through_plus_4_complete = bool(targets.pop("target_through_plus_4_complete"))

            market_coverage = COVERAGE_MISSING_BAR if market_missing else COVERAGE_OK
            exclusion_reason = "market_bar_missing" if market_missing else None
            if market_missing:
                bump("market_bar_missing", {"symbol": symbol, "session": session_date.isoformat()})

            quality: dict[str, Any] = {
                "news_coverage_status": news.coverage_status,
                "market_coverage_status": market_coverage,
                "feature_capabilities": dict(sorted(news.feature_capabilities.items())),
                "news_history_len": news.inputs.get("attention_history_len", 0),
                "market_history_len": (
                    prior.inputs.get("market_history_len") if prior is not None else 0
                ),
                "history_sufficient": (prior is not None),
                "response_truncated": news.quality_flags.get("response_truncated", False),
                "exclusion_reason": exclusion_reason,
                "benchmark_available": bench_feature is not None,
                "target_session_complete": target_session_complete,
                "target_through_plus_4_complete": target_through_plus_4_complete,
            }

            lineage: dict[str, Any] = {
                "news_provider": news.provider,
                "news_measurement_method": news.news_measurement_method,
                "news_feature_version": news.feature_version,
                "alignment_policy": news.alignment_policy,
                "alignment_precision": news.alignment_precision,
                "alignment_downgraded": news.alignment_downgraded,
                "alignment_warning": news.alignment_warning,
                "availability_assumption": news.availability_assumption,
                "feature_window_start": news.to_dict()["feature_window_start"],
                "feature_window_end": news.to_dict()["feature_window_end"],
                "source_feature_dates": list(news.source_feature_dates),
                "mapping_version": mapping.mapping_version,
                "relationship": mapping.relationship,
                "exposure_type": mapping.exposure_type,
                "market_feature_version": market_series.feature_version,
                "benchmark_symbol": bench_symbol,
                "sector_benchmark_symbol": mapping.sector_benchmark_symbol,
            }

            observation = NewsMarketObservation(
                dataset_version=dataset_version,
                observation_id=_observation_id(
                    dataset_version,
                    news.topic,
                    mapping.instrument_id,
                    session_date,
                    mapping.mapping_version,
                    news.news_measurement_method,
                ),
                topic=news.topic,
                instrument_id=mapping.instrument_id,
                symbol=symbol,
                session_date=session_date,
                feature_available_at=news.feature_available_at,
                target_session_open=calendar.session_open(session_date),
                feature_cutoff=news.feature_cutoff,
                cutoff_buffer_seconds=news.cutoff_buffer_seconds,
                availability_assumption=news.availability_assumption,
                alignment_precision=news.alignment_precision,
                alignment_policy=news.alignment_policy,
                mapping_version=mapping.mapping_version,
                news_measurement_method=news.news_measurement_method,
                market_provider=(
                    instrument_feature.provider if instrument_feature else "unavailable"
                ),
                feed=instrument_feature.feed if instrument_feature else "unavailable",
                adjustment=instrument_feature.adjustment if instrument_feature else "unavailable",
                currency=instrument_feature.currency if instrument_feature else "unavailable",
                timeframe=instrument_feature.timeframe if instrument_feature else "unavailable",
                inputs=inputs,
                contemporaneous=contemporaneous,
                targets=targets,
                quality=quality,
                lineage=lineage,
            )
            observations.append(observation)

    observations.sort(
        key=lambda o: (
            o.session_date.isoformat(),
            o.topic,
            o.symbol,
            o.news_measurement_method,
        )
    )
    return JoinResult(
        observations=observations,
        exclusions=exclusions,
        excluded_examples=excluded_examples,
    )

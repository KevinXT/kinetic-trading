"""Tests for the event-versus-control contrast (the corrected H1 test).

These prove the confirmatory H1 test compares attention-event sessions to ordinary
(control) sessions rather than testing the event mean against zero, that pooled
comparisons cluster within session date, that symbol-specific comparisons never
mix symbols, that incomplete targets are excluded, that undersized groups get no
formal inference, that BH-FDR includes only eligible H1 contrasts, and that a
fixed seed is deterministic.
"""

from __future__ import annotations

from datetime import date

from research_data.calendar import MarketCalendar
from research_data.event_study import (
    EventStudyConfig,
    build_event_control_contrasts,
    run_event_study,
)
from research_data.models import AVAILABILITY_DAILY_PERIOD_END_PROXY, NewsMarketObservation

CAL = MarketCalendar()
THRESHOLD = 1.5


def _obs(
    session: date,
    symbol: str,
    z: float,
    abs_ret: float,
    *,
    parkinson: float | None = None,
    topic: str = "semiconductors",
    method: str = "bigquery_gdelt_counts",
    absolute_return_value: float | None = "__use_abs__",  # type: ignore[assignment]
) -> NewsMarketObservation:
    open_at = CAL.session_open(session)
    cutoff = open_at.replace(minute=open_at.minute - 5)
    park = parkinson if parkinson is not None else abs_ret**2
    abs_target = abs_ret if absolute_return_value == "__use_abs__" else absolute_return_value
    return NewsMarketObservation(
        dataset_version="v1",
        observation_id=f"obs-{session}-{symbol}-{z}",
        topic=topic,
        instrument_id=f"instrument-{symbol}",
        symbol=symbol,
        session_date=session,
        feature_available_at=cutoff,
        target_session_open=open_at,
        feature_cutoff=cutoff,
        cutoff_buffer_seconds=300,
        availability_assumption=AVAILABILITY_DAILY_PERIOD_END_PROXY,
        alignment_precision="daily_approximation",
        alignment_policy="conservative_calendar_day_v2",
        mapping_version="m",
        news_measurement_method=method,
        market_provider="alpaca",
        feed="iex",
        adjustment="all",
        currency="USD",
        timeframe="1Day",
        inputs={"news_attention_zscore_30": z},
        contemporaneous={},
        targets={
            "target_session_return": abs_ret,
            "target_session_absolute_return": abs_target,
            "target_session_benchmark_adjusted_return": abs_ret / 3.0,
            "target_session_parkinson_variance": park,
        },
        quality={"market_coverage_status": "measured", "target_session_complete": True},
        lineage={"mapping_version": "m", "relationship": "constituent"},
    )


def _sessions(n: int) -> list[date]:
    return CAL.sessions_between(date(2025, 8, 1), date(2026, 6, 30))[:n]


def _event_control_universe(
    *,
    n_event: int,
    n_control: int,
    event_abs: float,
    control_abs: float,
    symbols=("AMD",),
    jitter: float = 0.0,
):
    sessions = _sessions(n_event + n_control)
    event_sessions = sessions[:n_event]
    control_sessions = sessions[n_event : n_event + n_control]
    observations: list[NewsMarketObservation] = []
    for i, sess in enumerate(event_sessions):
        for sym in symbols:
            observations.append(_obs(sess, sym, 3.0, event_abs + jitter * (i % 3)))
    for i, sess in enumerate(control_sessions):
        for sym in symbols:
            observations.append(_obs(sess, sym, 0.4, control_abs + jitter * (i % 3)))
    return observations


def _config(minimum_sample: int = 15) -> EventStudyConfig:
    return EventStudyConfig(
        threshold=THRESHOLD,
        minimum_sample=minimum_sample,
        bootstrap_iterations=200,
        seed=12345,
        bootstrap_block_length=5,
    )


def _find(contrasts, grouping, group_value, response):
    for c in contrasts:
        if (
            c["grouping"] == grouping
            and c["group_value"] == group_value
            and c["response"] == response
        ):
            return c
    raise AssertionError(f"contrast not found: {grouping}/{group_value}/{response}")


def test_event_vs_control_difference_is_calculated() -> None:
    obs = _event_control_universe(n_event=16, n_control=16, event_abs=0.05, control_abs=0.01)
    contrasts, _ = build_event_control_contrasts(obs, _config())
    c = _find(contrasts, "pooled", "all_symbols", "target_session_absolute_return")
    assert c["event_mean"] == 0.05
    assert c["control_mean"] == 0.01
    assert abs(c["difference"] - 0.04) < 1e-12
    assert abs(c["ratio"] - 5.0) < 1e-9
    assert c["inferential_status"] == "eligible"
    # A difference this large should have a CI strictly above zero.
    assert c["ci_low"] is not None and c["ci_low"] > 0.0


def test_h1_is_not_tested_against_zero() -> None:
    obs = _event_control_universe(n_event=16, n_control=16, event_abs=0.05, control_abs=0.01)
    summary, _ = run_event_study(obs, _config())
    # Event-only group summaries never carry a one-sample vs-zero p-value.
    all_group = next(g for g in summary["groups"] if g["group_kind"] == "all")
    for metric in all_group["metrics"].values():
        assert metric["p_value_uncorrected"] is None
    # The H1 family is the event-vs-control difference, and it uses BOTH groups.
    h1 = next(f for f in summary["fdr_families"] if f["hypothesis_id"] == "H1")
    assert h1["contrast_kind"] == "event_vs_control_difference"
    c = _find(
        summary["event_control_contrasts"],
        "pooled",
        "all_symbols",
        "target_session_absolute_return",
    )
    # The tested quantity references the control mean, not just the event mean.
    assert c["control_mean"] is not None and c["control_row_count"] > 0


def test_pooled_contrast_aggregates_within_session_date_first() -> None:
    obs = _event_control_universe(
        n_event=16,
        n_control=16,
        event_abs=0.05,
        control_abs=0.01,
        symbols=("AMD", "NVDA", "SMH"),
    )
    contrasts, _ = build_event_control_contrasts(obs, _config())
    c = _find(contrasts, "pooled", "all_symbols", "target_session_absolute_return")
    # 16 event dates * 3 symbols = 48 rows, but only 16 independent session dates.
    assert c["event_date_count"] == 16
    assert c["event_row_count"] == 48
    assert c["control_date_count"] == 16
    assert c["control_row_count"] == 48


def test_symbol_specific_contrast_does_not_mix_symbols() -> None:
    sessions = _sessions(32)
    obs: list[NewsMarketObservation] = []
    for sess in sessions[:16]:
        obs.append(_obs(sess, "AMD", 3.0, 0.09))
        obs.append(_obs(sess, "NVDA", 3.0, 0.01))
    for sess in sessions[16:32]:
        obs.append(_obs(sess, "AMD", 0.4, 0.02))
        obs.append(_obs(sess, "NVDA", 0.4, 0.02))
    contrasts, _ = build_event_control_contrasts(obs, _config())
    amd = _find(contrasts, "symbol", "AMD", "target_session_absolute_return")
    nvda = _find(contrasts, "symbol", "NVDA", "target_session_absolute_return")
    # AMD event mean reflects only AMD event rows (0.09), NVDA only NVDA (0.01).
    assert abs(amd["event_mean"] - 0.09) < 1e-12
    assert abs(nvda["event_mean"] - 0.01) < 1e-12


def test_incomplete_targets_are_excluded() -> None:
    sessions = _sessions(34)
    obs: list[NewsMarketObservation] = []
    for sess in sessions[:17]:
        obs.append(_obs(sess, "AMD", 3.0, 0.05))
    for sess in sessions[17:34]:
        obs.append(_obs(sess, "AMD", 0.4, 0.01))
    # Blank out one event's absolute-return target.
    obs[0] = _obs(sessions[0], "AMD", 3.0, 0.05, absolute_return_value=None)
    contrasts, _ = build_event_control_contrasts(obs, _config())
    c = _find(contrasts, "pooled", "all_symbols", "target_session_absolute_return")
    # 17 events minus the one with a missing target -> 16 usable event rows/dates.
    assert c["event_row_count"] == 16
    assert c["event_date_count"] == 16
    # Parkinson variance still has all 17 (its target was not blanked).
    park = _find(contrasts, "pooled", "all_symbols", "target_session_parkinson_variance")
    assert park["event_row_count"] == 17


def test_undersized_group_gets_no_formal_inference() -> None:
    obs = _event_control_universe(n_event=5, n_control=5, event_abs=0.05, control_abs=0.01)
    contrasts, fdr = build_event_control_contrasts(obs, _config(minimum_sample=15))
    c = _find(contrasts, "pooled", "all_symbols", "target_session_absolute_return")
    assert c["inferential_status"] == "insufficient_sample"
    assert c["ci_low"] is None and c["ci_high"] is None
    assert c["p_value_uncorrected"] is None
    assert c["adjusted_p_value"] is None
    assert fdr["eligible_tests"] == 0


def test_bh_fdr_includes_only_eligible_h1_contrasts() -> None:
    obs = _event_control_universe(
        n_event=16,
        n_control=16,
        event_abs=0.05,
        control_abs=0.01,
        symbols=("AMD", "NVDA", "SMH"),
        jitter=0.002,
    )
    contrasts, fdr = build_event_control_contrasts(obs, _config())
    # Eligible primary H1 contrasts: 2 responses * (pooled + 3 symbols) = 8.
    assert fdr["eligible_tests"] == 8
    for result in fdr["results"]:
        assert result["response"] in (
            "target_session_absolute_return",
            "target_session_parkinson_variance",
        )
    # Secondary (benchmark) responses are never BH-adjusted.
    sec = _find(contrasts, "pooled", "all_symbols", "target_session_benchmark_adjusted_return")
    assert sec["in_h1_family"] is False
    assert sec["adjusted_p_value"] is None


def test_fixed_seed_produces_deterministic_contrasts() -> None:
    obs = _event_control_universe(n_event=16, n_control=16, event_abs=0.05, control_abs=0.01)
    a, _ = build_event_control_contrasts(obs, _config())
    b, _ = build_event_control_contrasts(obs, _config())
    assert a == b
    ca = _find(a, "pooled", "all_symbols", "target_session_absolute_return")
    cb = _find(b, "pooled", "all_symbols", "target_session_absolute_return")
    assert ca["ci_low"] == cb["ci_low"]
    assert ca["ci_high"] == cb["ci_high"]


def test_study_window_excludes_out_of_sample_observations() -> None:
    sessions = _sessions(32)
    obs = _event_control_universe(n_event=16, n_control=16, event_abs=0.05, control_abs=0.01)
    # Restrict the study window to only the first half of the event sessions.
    cfg = EventStudyConfig(
        threshold=THRESHOLD,
        minimum_sample=1,
        bootstrap_iterations=50,
        study_window_start=sessions[0].isoformat(),
        study_window_end=sessions[7].isoformat(),
    )
    contrasts, _ = build_event_control_contrasts(obs, cfg)
    c = _find(contrasts, "pooled", "all_symbols", "target_session_absolute_return")
    assert c["event_date_count"] == 8  # only sessions inside the declared window

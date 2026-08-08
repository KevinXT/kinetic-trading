#!/usr/bin/env python3
"""Generate committed offline fixtures for the semiconductor attention study.

Deterministic, no randomness, no network. Produces a *representative* year-long
BigQuery-style daily-count series and matching Alpaca-style daily bars so the
offline research build can exercise the full event-vs-control contrast with
enough independent event and control session-date clusters to pass the minimum
inference sample.

The design intentionally injects a mild, honest signal: on the sessions that
react to a coverage spike, the semiconductor names (AMD, NVDA, SMH) show larger
absolute moves and wider ranges than ordinary sessions, while the QQQ benchmark
moves less. This is synthetic data for pipeline validation only — it is not real
market history and proves nothing about real markets.

Usage:
    python scripts/generate_semiconductor_study_fixtures.py
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from research_data.calendar import DEFAULT_CALENDAR

NY = ZoneInfo("America/New_York")
OUT_DIR = Path("tests/fixtures/research")

# Market bars span extra history (for prior-20 / prev-session inputs) and extra
# tail (for S..S+4 target completion) around the declared study window.
MARKET_START = date(2025, 5, 15)
MARKET_END = date(2026, 7, 10)
# BigQuery daily counts are measured only over the declared study window.
COUNTS_START = date(2025, 7, 1)
COUNTS_END = date(2026, 6, 30)

SYMBOL_SPECS = {
    "SMH": (240.0, 5_000_000, True),
    "NVDA": (120.0, 40_000_000, True),
    "AMD": (160.0, 20_000_000, True),
    "QQQ": (480.0, 30_000_000, False),  # benchmark; muted event response
}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _round(value: float) -> float:
    return round(value, 4)


def _spike_calendar_days() -> list[date]:
    """Deterministic coverage-spike calendar days, spaced to stay above threshold.

    Spikes start well after the first 30 count-days (so a 30-session attention
    z-score is defined) and are spaced ~13 calendar days apart.
    """
    days: list[date] = []
    cursor = date(2025, 8, 15)
    while cursor <= COUNTS_END:
        days.append(cursor)
        cursor += timedelta(days=13)
    return days


def build_counts() -> list[dict]:
    spikes = set(_spike_calendar_days())
    rows: list[dict] = []
    cursor = COUNTS_START
    i = 0
    while cursor <= COUNTS_END:
        # Baseline coverage with deterministic, nonzero dispersion.
        base = 6 + (i % 5) + int(2 + 2 * math.sin(i / 4.0))
        count = 160 if cursor in spikes else base
        rows.append(
            {
                "provider": "bigquery_gdelt",
                "mode": "daily_counts",
                "topic": "semiconductors",
                "date": cursor.isoformat(),
                "article_count": count,
                "source_count": None,
                "avg_sentiment": None,
                "coverage_share": None,
                "query_terms": ["WB_2461_SEMICONDUCTORS", "semiconductor"],
                "ingested_at": "2026-07-15T00:00:00Z",
            }
        )
        cursor += timedelta(days=1)
        i += 1
    return rows


def _amplified_sessions() -> set[date]:
    """Sessions that react to a coverage spike (next session after each spike)."""
    sessions: set[date] = set()
    for spike_day in _spike_calendar_days():
        sessions.add(DEFAULT_CALENDAR.next_session(spike_day))
    return sessions


def build_bars() -> list[dict]:
    sessions = DEFAULT_CALENDAR.sessions_between(MARKET_START, MARKET_END)
    amplified = _amplified_sessions()
    bars: list[dict] = []
    for symbol, (base, base_vol, is_semi) in SYMBOL_SPECS.items():
        prev_close = base
        for i, sess in enumerate(sessions):
            event_session = sess in amplified
            # Ordinary deterministic drift.
            drift = 1.0 + 0.0006 * i + 0.010 * math.sin(i / 3.0)
            close = base * drift
            gap = 1.0 + 0.003 * math.sin(i / 2.0)
            open_ = prev_close * gap
            # Event sessions: amplify the semiconductor names' absolute move + range.
            range_factor = 1.010
            if event_session and is_semi:
                shock = 0.05 * (1.0 if (i % 2 == 0) else -1.0)  # signed, large move
                close = close * (1.0 + shock)
                range_factor = 1.045  # wider high-low band -> higher Parkinson variance
            elif event_session and not is_semi:
                close = close * (1.0 + 0.006 * (1.0 if (i % 2 == 0) else -1.0))
                range_factor = 1.012
            hi = max(open_, close) * range_factor
            lo = min(open_, close) / range_factor
            vol_factor = 1.0 + 0.15 * math.sin(i / 2.5)
            volume = int(base_vol * vol_factor)
            if event_session and is_semi:
                volume = int(volume * 1.8)
            vwap = (hi + lo + close) / 3.0
            ts = datetime.combine(sess, time(0, 0), tzinfo=NY).astimezone(timezone.utc)
            bars.append(
                {
                    "symbol": symbol,
                    "timestamp": _iso(ts),
                    "timeframe": "1Day",
                    "open": _round(open_),
                    "high": _round(hi),
                    "low": _round(lo),
                    "close": _round(close),
                    "volume": volume,
                    "vwap": _round(vwap),
                    "trade_count": max(1, volume // 50),
                    "provider": "alpaca",
                    "feed": "iex",
                    "adjustment": "all",
                    "currency": "USD",
                    "retrieved_at": "2026-07-15T00:00:00Z",
                }
            )
            prev_close = close
    return bars


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {
        "semiconductor_study_bigquery_counts.json": build_counts(),
        "semiconductor_study_market_bars.json": build_bars(),
    }
    for name, data in outputs.items():
        path = OUT_DIR / name
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path} ({len(data)} records)")
    print(f"spike calendar days: {len(_spike_calendar_days())}")
    print(f"amplified (reacting) sessions: {len(_amplified_sessions())}")


if __name__ == "__main__":
    main()

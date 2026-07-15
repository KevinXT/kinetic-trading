#!/usr/bin/env python3
"""Deterministically generate committed offline fixtures for the research layer.

Run once and commit the output. No randomness, no network. The fixtures are JSON
arrays (not .jsonl) so they are not swept up by the repo-wide ``*.jsonl``
gitignore rule.

The generated data intentionally exercises: ordinary weekdays, Friday->Monday and
weekend alignment, a market holiday (Memorial Day 2026-05-25), a high-attention
broad-coverage event, a high-syndication cluster, a duplicated article, a
multi-topic article, a missing market bar, insufficient early history, and an
incomplete forward horizon at the tail.

Usage:
    python scripts/generate_research_fixtures.py
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

START = date(2026, 5, 1)
END = date(2026, 6, 30)
EVENT_DAY = date(2026, 6, 15)  # Monday: high-attention broad-coverage event
SYNDICATION_DAY = date(2026, 6, 16)  # Tuesday: heavy syndication cluster
MULTI_TOPIC_DAY = date(2026, 6, 2)  # multi-topic + duplicated article
MISSING_BAR_DAY = date(2026, 6, 15)  # AMD bar intentionally missing

SEMI_QUERY = '(semiconductor OR GPU OR Nvidia OR TSMC OR "chip supply")'

BASELINE_DOMAINS = [
    "reuters.com",
    "bloomberg.com",
    "wsj.com",
    "cnbc.com",
    "ft.com",
    "barrons.com",
    "marketwatch.com",
    "seekingalpha.com",
]
BROAD_DOMAINS = [f"outlet{i:02d}.com" for i in range(30)]
BASELINE_COUNTRIES = ["United States", "United Kingdom", "Germany", "Japan", "Canada"]


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _published(day: date, hour: int, minute: int = 0) -> str:
    local = datetime.combine(day, time(hour, minute), tzinfo=NY)
    return _iso(local)


def _article(
    *,
    title: str,
    domain: str,
    day: date,
    hour: int,
    country: str = "United States",
    topics: list[str] | None = None,
    primary: str = "semiconductors",
    minute: int = 0,
) -> dict:
    topics = topics or ["semiconductors"]
    slug = title.lower().replace(" ", "-").replace(",", "")
    return {
        "provider": "gdelt",
        "query": SEMI_QUERY,
        "title": title,
        "url": f"https://www.{domain}/2026/{slug}",
        "mobile_url": "",
        "domain": domain,
        "language": "English",
        "source_country": country,
        "published_at": _published(day, hour, minute),
        "raw_seen_date": "",
        "image_url": "",
        "ingested_at": _published(day, hour, minute),
        "topics": topics,
        "topic_matches": {t: ["semiconductor"] for t in topics},
        "primary_topic": primary,
    }


def build_articles() -> list[dict]:
    articles: list[dict] = []
    sessions = DEFAULT_CALENDAR.sessions_between(START, END)

    # Baseline coverage: a small, varying number of pre-open articles per session.
    for i, sess in enumerate(sessions):
        count = 1 + (i % 3)  # 1, 2, or 3 -> nonzero dispersion for z-scores
        for j in range(count):
            articles.append(
                _article(
                    title=f"Chip sector update {sess.isoformat()} {j}",
                    domain=BASELINE_DOMAINS[(i + j) % len(BASELINE_DOMAINS)],
                    day=sess,
                    hour=8,
                    minute=j,
                    country=BASELINE_COUNTRIES[(i + j) % len(BASELINE_COUNTRIES)],
                )
            )

    # Weekend news before the event Monday -> folds into 2026-06-15 session.
    for wk_day, hour in ((date(2026, 6, 13), 12), (date(2026, 6, 14), 15)):
        articles.append(
            _article(
                title=f"Weekend chip briefing {wk_day.isoformat()}",
                domain="reuters.com",
                day=wk_day,
                hour=hour,
            )
        )

    # Consecutive non-session days before a holiday: Sat/Sun/Mon(Memorial Day)
    # all fold into Tuesday 2026-05-26.
    for hol_day, hour in (
        (date(2026, 5, 23), 10),
        (date(2026, 5, 24), 11),
        (date(2026, 5, 25), 9),
    ):
        articles.append(
            _article(
                title=f"Holiday-week chip note {hol_day.isoformat()}",
                domain="bloomberg.com",
                day=hol_day,
                hour=hour,
            )
        )

    # High-attention, broad independent coverage event (pre-open Monday).
    for k, domain in enumerate(BROAD_DOMAINS):
        articles.append(
            _article(
                title=f"Semiconductor demand surges report {k}",
                domain=domain,
                day=EVENT_DAY,
                hour=7,
                minute=k % 60,
                country=BASELINE_COUNTRIES[k % len(BASELINE_COUNTRIES)],
            )
        )

    # High-syndication cluster (same story reprinted across many domains).
    for k in range(10):
        articles.append(
            _article(
                title="Chipmaker announces new fab",  # identical title => syndication
                domain=BROAD_DOMAINS[k],
                day=SYNDICATION_DAY,
                hour=7,
                minute=k,
            )
        )

    # Multi-topic article (semiconductors + ai) -> fans out to the ai/QQQ mapping.
    articles.append(
        _article(
            title="AI chip roadmap accelerates",
            domain="cnbc.com",
            day=MULTI_TOPIC_DAY,
            hour=8,
            topics=["semiconductors", "ai"],
            primary="semiconductors",
        )
    )
    # Duplicated article (identical normalized title, same session/topic).
    dup = _article(
        title="Duplicate chip headline",
        domain="wsj.com",
        day=MULTI_TOPIC_DAY,
        hour=8,
    )
    articles.append(dup)
    articles.append({**dup, "url": dup["url"] + "?ref=2"})

    return articles


def _round(value: float) -> float:
    return round(value, 4)


def build_bars() -> list[dict]:
    sessions = DEFAULT_CALENDAR.sessions_between(START, END)
    specs = {
        "SMH": (240.0, 5_000_000),
        "NVDA": (120.0, 40_000_000),
        "AMD": (160.0, 20_000_000),
        "QQQ": (480.0, 30_000_000),
    }
    bars: list[dict] = []
    for symbol, (base, base_vol) in specs.items():
        prev_close = base
        for i, sess in enumerate(sessions):
            if symbol == "AMD" and sess == MISSING_BAR_DAY:
                continue  # intentionally missing market bar
            drift = 1.0 + 0.0008 * i + 0.015 * math.sin(i / 3.0)
            close = base * drift
            gap = 1.0 + 0.004 * math.sin(i / 2.0)
            open_ = prev_close * gap
            hi = max(open_, close) * 1.012
            lo = min(open_, close) * 0.988
            vol_factor = 1.0 + 0.15 * math.sin(i / 2.5)
            volume = int(base_vol * vol_factor)
            # Event-day volume bump for semiconductor names.
            if sess == EVENT_DAY and symbol in {"SMH", "NVDA"}:
                volume = int(volume * 2.2)
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
                    "retrieved_at": "2026-07-01T00:00:00Z",
                }
            )
            prev_close = close
    return bars


def build_counts() -> list[dict]:
    """BigQuery-style daily counts (only article_count is measurable)."""
    rows: list[dict] = []
    cursor = START
    i = 0
    while cursor <= END:
        count = 3 + (i % 4)
        if cursor == EVENT_DAY:
            count = 60
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
                "query_terms": ["ECON_SEMICONDUCTOR", "semiconductor"],
                "ingested_at": "2026-07-01T00:00:00Z",
            }
        )
        cursor += timedelta(days=1)
        i += 1
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {
        "semiconductors_articles.json": build_articles(),
        "market_bars.json": build_bars(),
        "bigquery_counts.json": build_counts(),
    }
    for name, data in outputs.items():
        path = OUT_DIR / name
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path} ({len(data)} records)")


if __name__ == "__main__":
    main()

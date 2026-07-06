"""
Append-only cost ledger.

Every cost decision (dry-run, block, cache hit, real execution) is written as a
single JSON line. The ledger is the audit trail the ``cost-report`` CLI reads
and the budget source the SafeBigQueryClient consults for daily / monthly caps.

v0.1 tracks *estimated* cost. Records are never mutated or deleted; the file is
strictly append-only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .policy import CostPolicy

JsonDict = Dict[str, Any]

DEFAULT_LEDGER_PATH = "data/cost/cost_ledger.jsonl"

# A decision counts as real spend only when the query actually executed.
DECISION_ALLOW = "ALLOW"


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string with trailing ``Z``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def billing_cycle_bounds(on: date, *, start_day: int = 1) -> tuple[date, date]:
    """
    Return ``(cycle_start, cycle_end)`` for the billing cycle containing ``on``.

    ``start_day`` is clamped to [1, 28] for predictable month math. The cycle
    runs from ``start_day`` of one month to the day before ``start_day`` of the
    next month (inclusive end).
    """
    start_day = max(1, min(28, int(start_day)))
    if on.day >= start_day:
        cycle_start = on.replace(day=start_day)
    else:
        # belongs to the previous month's cycle
        if on.month == 1:
            cycle_start = on.replace(year=on.year - 1, month=12, day=start_day)
        else:
            cycle_start = on.replace(month=on.month - 1, day=start_day)

    if cycle_start.month == 12:
        next_start = cycle_start.replace(year=cycle_start.year + 1, month=1)
    else:
        next_start = cycle_start.replace(month=cycle_start.month + 1)
    cycle_end = date.fromordinal(next_start.toordinal() - 1)
    return cycle_start, cycle_end


def billing_cycle_label(on: date, *, start_day: int = 1) -> str:
    """Human-stable cycle id, e.g. ``2026-05-01_to_2026-05-31``."""
    start, end = billing_cycle_bounds(on, start_day=start_day)
    return f"{start.isoformat()}_to_{end.isoformat()}"


@dataclass
class CostSummary:
    """Aggregated view of the ledger for a billing cycle."""

    billing_cycle: str
    today_estimated_usd: float
    cycle_estimated_usd: float
    monthly_cap_usd: float
    emergency_stop_usd: float
    remaining_monthly_usd: float
    emergency_stop_reached: bool
    provider_estimated_usd: Dict[str, float] = field(default_factory=dict)
    cache_hit_count: int = 0
    total_query_count: int = 0
    cache_hit_rate: float = 0.0
    blocked_query_count: int = 0
    most_expensive_job: Optional[str] = None
    most_expensive_job_usd: float = 0.0

    def to_dict(self) -> JsonDict:
        return {
            "billing_cycle": self.billing_cycle,
            "today_estimated_usd": round(self.today_estimated_usd, 6),
            "cycle_estimated_usd": round(self.cycle_estimated_usd, 6),
            "monthly_cap_usd": self.monthly_cap_usd,
            "emergency_stop_usd": self.emergency_stop_usd,
            "remaining_monthly_usd": round(self.remaining_monthly_usd, 6),
            "emergency_stop_reached": self.emergency_stop_reached,
            "provider_estimated_usd": {
                k: round(v, 6) for k, v in self.provider_estimated_usd.items()
            },
            "cache_hit_count": self.cache_hit_count,
            "total_query_count": self.total_query_count,
            "cache_hit_rate": round(self.cache_hit_rate, 4),
            "blocked_query_count": self.blocked_query_count,
            "most_expensive_job": self.most_expensive_job,
            "most_expensive_job_usd": round(self.most_expensive_job_usd, 6),
        }


class CostLedger:
    """Append-only JSONL ledger of cost decisions."""

    def __init__(self, path: str | Path = DEFAULT_LEDGER_PATH) -> None:
        self.path = Path(path)

    def append(self, record: JsonDict) -> JsonDict:
        """Append one record (creating parent dirs / file as needed).

        A ``timestamp`` is added if missing. The stored record is returned.
        """
        if not isinstance(record, dict):
            raise TypeError(
                f"CostLedger.append: record must be a dict, got {type(record).__name__}"
            )
        rec = dict(record)
        rec.setdefault("timestamp", utc_now_iso())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def read_all(self) -> List[JsonDict]:
        """Read every valid record. Missing file -> empty list."""
        if not self.path.is_file():
            return []
        records: List[JsonDict] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    records.append(obj)
        return records

    def summarize(self, policy: CostPolicy, *, on: Optional[date] = None) -> CostSummary:
        """
        Aggregate the ledger for the billing cycle containing ``on`` (today UTC
        by default).

        Spend totals count only executed queries (``decision == ALLOW``); dry
        runs and blocks are free, cache hits cost nothing.
        """
        today = on or datetime.now(timezone.utc).date()
        cycle_start, cycle_end = billing_cycle_bounds(
            today, start_day=policy.billing_cycle_start_day
        )
        cycle_label = f"{cycle_start.isoformat()}_to_{cycle_end.isoformat()}"

        today_usd = 0.0
        cycle_usd = 0.0
        provider_usd: Dict[str, float] = {}
        cache_hits = 0
        total = 0
        blocked = 0
        most_expensive_job: Optional[str] = None
        most_expensive_usd = 0.0

        for rec in self.read_all():
            total += 1
            decision = str(rec.get("decision", ""))
            if rec.get("cache_hit"):
                cache_hits += 1
            if decision.startswith("BLOCK"):
                blocked += 1

            rec_date = _record_date(rec)
            in_cycle = rec_date is not None and cycle_start <= rec_date <= cycle_end

            # Only executed queries count toward spend.
            if decision != DECISION_ALLOW:
                continue
            cost = float(rec.get("estimated_cost_usd") or 0.0)
            if in_cycle:
                cycle_usd += cost
                provider = str(rec.get("provider", "unknown"))
                provider_usd[provider] = provider_usd.get(provider, 0.0) + cost
            if rec_date == today:
                today_usd += cost
            if cost > most_expensive_usd:
                most_expensive_usd = cost
                most_expensive_job = rec.get("query_name")

        remaining = policy.monthly_cap_usd - cycle_usd
        emergency_reached = cycle_usd >= policy.emergency_stop_usd
        cache_rate = (cache_hits / total) if total else 0.0

        return CostSummary(
            billing_cycle=cycle_label,
            today_estimated_usd=today_usd,
            cycle_estimated_usd=cycle_usd,
            monthly_cap_usd=policy.monthly_cap_usd,
            emergency_stop_usd=policy.emergency_stop_usd,
            remaining_monthly_usd=remaining,
            emergency_stop_reached=emergency_reached,
            provider_estimated_usd=provider_usd,
            cache_hit_count=cache_hits,
            total_query_count=total,
            cache_hit_rate=cache_rate,
            blocked_query_count=blocked,
            most_expensive_job=most_expensive_job,
            most_expensive_job_usd=most_expensive_usd,
        )


def _record_date(rec: JsonDict) -> Optional[date]:
    """Parse the ledger ``timestamp`` into a UTC date, if possible."""
    ts = rec.get("timestamp")
    if not isinstance(ts, str) or not ts:
        return None
    text = ts.strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).date()
    except ValueError:
        # Fall back to a leading YYYY-MM-DD prefix.
        try:
            return date.fromisoformat(ts[:10])
        except ValueError:
            return None

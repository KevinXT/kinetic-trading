"""
Human-readable cost report rendering.

Pure formatting over a :class:`CostSummary` so it is trivially unit-testable and
reused by the ``trading_platform cost-report`` CLI.
"""

from __future__ import annotations

from datetime import date

from .ledger import CostSummary

_MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def _fmt_cycle(label: str) -> str:
    """``2026-05-01_to_2026-05-31`` -> ``May 1 → May 31``."""
    try:
        start_s, end_s = label.split("_to_")
        start = date.fromisoformat(start_s)
        end = date.fromisoformat(end_s)
        return f"{_MONTHS[start.month - 1]} {start.day} \u2192 {_MONTHS[end.month - 1]} {end.day}"
    except (ValueError, IndexError):
        return label


def _provider_label(name: str) -> str:
    if name.lower().startswith("bigquery"):
        return "BigQuery"
    return name


def _usd(value: float) -> str:
    return f"${value:,.2f}"


def format_cost_report(summary: CostSummary) -> str:
    """Render a :class:`CostSummary` as a fixed-width text block."""
    lines = []
    lines.append(f"Billing cycle: {_fmt_cycle(summary.billing_cycle)}")
    lines.append("")
    lines.append(f"Monthly cap:        {_usd(summary.monthly_cap_usd)}")
    lines.append(f"Emergency stop:     {_usd(summary.emergency_stop_usd)}")
    lines.append(f"Estimated used:     {_usd(summary.cycle_estimated_usd)}")
    lines.append(f"Remaining:          {_usd(summary.remaining_monthly_usd)}")
    lines.append(f"Today used:         {_usd(summary.today_estimated_usd)}")
    if summary.emergency_stop_reached:
        lines.append("")
        lines.append("** EMERGENCY STOP REACHED — executions are blocked. **")
    lines.append("")
    lines.append("Provider usage:")
    if summary.provider_estimated_usd:
        for provider, amount in sorted(summary.provider_estimated_usd.items()):
            label = (_provider_label(provider) + ":").ljust(20)
            lines.append(f"{label}{_usd(amount)}")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append(f"Cache hit rate:     {round(summary.cache_hit_rate * 100)}%")
    lines.append(f"Blocked queries:    {summary.blocked_query_count}")
    lines.append(f"Most expensive job: {summary.most_expensive_job or '(none)'}")
    return "\n".join(lines)

"""
Reporting-view registry, SQL loading/rendering, and CREATE-VIEW building.

This is the pure, side-effect-free half of the reporting layer: it knows which
dashboard views exist, where their ``.sql`` templates live, how to render a
template into concrete BigQuery Standard SQL, and how to wrap a rendered
``SELECT`` in a ``CREATE OR REPLACE VIEW`` statement.

No BigQuery access happens here. The runner (``build_views``) is the only place
that touches the network, via the sanctioned ``SafeBigQueryClient``.

SQL templates use a tiny ``{{ name }}`` placeholder syntax (no Jinja dependency).
Supported placeholders: ``source_table``, ``lookback_days``, ``top_n``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from common.errors import ConfigError

# Directory holding the reporting SQL templates (installed as package data).
SQL_DIR = Path(__file__).resolve().parent / "sql"

# Default rolling window / row caps. Kept small so dashboard queries stay cheap.
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_TOP_N = 25

_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


@dataclass(frozen=True)
class ReportingView:
    """One dashboard-ready BigQuery reporting view."""

    name: str
    filename: str
    title: str
    description: str
    required_columns: List[str] = field(default_factory=list)
    suggested_chart: str = ""

    @property
    def sql_path(self) -> Path:
        return SQL_DIR / self.filename


# The reporting views, in dashboard order. ``name`` doubles as the BigQuery view
# id (``<project>.<dataset>.<name>``). ``required_columns`` are the output
# aliases the dashboard depends on; tests assert every one appears in the SQL.
REPORTING_VIEWS: List[ReportingView] = [
    ReportingView(
        name="daily_event_volume",
        filename="daily_event_volume.sql",
        title="Daily event volume",
        description="GDELT record count per calendar day over the rolling window.",
        required_columns=["event_date", "record_count"],
        suggested_chart="Time series: record_count by event_date.",
    ),
    ReportingView(
        name="top_sources",
        filename="top_sources.sql",
        title="Top sources",
        description="Most active source domains (SourceCommonName) by record count.",
        required_columns=["source_domain", "record_count"],
        suggested_chart="Bar chart: record_count by source_domain (top N).",
    ),
    ReportingView(
        name="top_themes_or_entities",
        filename="top_themes_or_entities.sql",
        title="Top themes / entities",
        description="Most frequent normalized GDELT theme codes by record count.",
        required_columns=["entity_or_theme", "record_count"],
        suggested_chart="Bar chart / table: record_count by entity_or_theme (top N).",
    ),
    ReportingView(
        name="data_quality_summary",
        filename="data_quality_summary.sql",
        title="Data quality summary",
        description=(
            "Single-row data-quality metrics: total rows, missing required "
            "fields, duplicate ids, and latest record timestamp."
        ),
        required_columns=[
            "check_date",
            "total_rows",
            "missing_required_field_count",
            "duplicate_count",
            "latest_record_timestamp",
        ],
        suggested_chart="Scorecards: total_rows, missing_required_field_count, "
        "duplicate_count, latest_record_timestamp.",
    ),
]

_VIEWS_BY_NAME: Dict[str, ReportingView] = {v.name: v for v in REPORTING_VIEWS}


def get_reporting_views() -> List[ReportingView]:
    """Return the registered reporting views (copy of the module registry)."""
    return list(REPORTING_VIEWS)


def get_view(name: str) -> ReportingView:
    """Look up a single view by name, or raise ``ConfigError``."""
    try:
        return _VIEWS_BY_NAME[name]
    except KeyError as exc:
        known = ", ".join(sorted(_VIEWS_BY_NAME)) or "(none)"
        raise ConfigError(f"unknown reporting view {name!r}; known views: {known}.") from exc


def discover_sql_files() -> List[Path]:
    """Return the ``.sql`` template paths on disk, sorted by filename."""
    if not SQL_DIR.is_dir():
        raise ConfigError(f"reporting SQL directory not found: {SQL_DIR}")
    return sorted(SQL_DIR.glob("*.sql"))


def load_view_template(view: ReportingView) -> str:
    """Read a view's raw SQL template text."""
    path = view.sql_path
    if not path.is_file():
        raise ConfigError(f"reporting SQL template not found for {view.name!r}: {path}")
    return path.read_text(encoding="utf-8")


def render_template(text: str, context: Dict[str, object]) -> str:
    """
    Render ``{{ name }}`` placeholders in ``text`` from ``context``.

    Raises ``ConfigError`` if the template references a placeholder that is not
    provided, so a typo fails fast instead of emitting broken SQL.
    """
    missing: List[str] = []

    def _sub(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in context:
            missing.append(key)
            return match.group(0)
        return str(context[key])

    rendered = _PLACEHOLDER_RE.sub(_sub, text)
    if missing:
        raise ConfigError(
            "reporting SQL template references unknown placeholder(s): " f"{sorted(set(missing))}."
        )
    return rendered


def render_view_sql(
    view: ReportingView,
    *,
    source_table: str,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    top_n: int = DEFAULT_TOP_N,
) -> str:
    """Render a view's ``SELECT`` template into concrete BigQuery SQL."""
    if not source_table or not str(source_table).strip():
        raise ConfigError("render_view_sql: source_table must be a non-empty string.")
    lookback = _positive_int(lookback_days, field="lookback_days")
    top = _positive_int(top_n, field="top_n")
    template = load_view_template(view)
    return render_template(
        template,
        {
            "source_table": str(source_table).strip(),
            "lookback_days": lookback,
            "top_n": top,
        },
    ).strip()


def qualified_view_id(project_id: str, dataset: str, view_name: str) -> str:
    """Build the fully-qualified ``project.dataset.view`` id."""
    parts = [str(project_id).strip(), str(dataset).strip(), str(view_name).strip()]
    if not all(parts):
        raise ConfigError(
            "qualified_view_id requires non-empty project_id, dataset, and view_name "
            f"(got {parts!r})."
        )
    return ".".join(parts)


def build_create_view_statement(view_id: str, select_sql: str) -> str:
    """
    Wrap a rendered ``SELECT`` in a ``CREATE OR REPLACE VIEW`` DDL statement.

    ``view_id`` is the fully-qualified ``project.dataset.view`` id. The inner
    SELECT should already have passed ``validate_bigquery_sql``; the DDL wrapper
    itself is a controlled, code-built statement (view creation scans no data).
    """
    if not view_id or not view_id.strip():
        raise ConfigError("build_create_view_statement: view_id must be non-empty.")
    body = select_sql.strip().rstrip(";")
    return f"CREATE OR REPLACE VIEW `{view_id.strip()}` AS\n{body}"


def _positive_int(value: object, *, field: str) -> int:
    try:
        out = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"reporting {field} must be a positive integer, got {value!r}.") from exc
    if out <= 0:
        raise ConfigError(f"reporting {field} must be a positive integer, got {out}.")
    return out

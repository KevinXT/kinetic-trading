"""
GDELT historical daily-count SQL builder (BigQuery Standard SQL).

Builds a bounded, aggregate-only query for *broad historical measurement*:
one row per day with an article count, filtered by term matches across a few
GDELT GKG text columns. The table comes from config (never hardcoded here at
call sites) so the same builder works against partitioned or other GKG tables.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from common.date_windows import (
    to_gdelt_gkg_end_datetime_int,
    to_gdelt_gkg_start_datetime_int,
)

# Bump when the generated SQL shape changes so cache keys invalidate cleanly.
# v2: V2Themes/Themes use delimiter-aware REGEXP_CONTAINS matching (was LIKE).
# v3: V2Themes/Themes use GDELT-recommended normalized UNNEST matching
#     (SPLIT(RTRIM(REGEXP_REPLACE(col, r',\\d+;', ';'), ';'), ';')) + theme IN (...),
#     which is more robust than raw REGEXP_CONTAINS and matches GDELT's own
#     normalization. This invalidates v2 (and the earlier 0-row) cache entries.
# v4: GKG DATE filter now uses 14-digit YYYYMMDDHHMMSS bounds (the GKG DATE column
#     is a datetime int, not an 8-digit YYYYMMDD). v3 emitted logically-wrong
#     8-digit bounds that matched no rows, so v4 makes those 0-row caches
#     unreachable.
QUERY_BUILDER_VERSION = "v4"

# Default pseudo-column used to prune partitions on gdelt-bq.gdeltv2.gkg_partitioned.
DEFAULT_PARTITION_COLUMN = "_PARTITIONTIME"
DEFAULT_PARTITION_TYPE = "timestamp"

# GKG columns scanned for term matches by default.
#
# Default to *only* ``V2Themes`` (compact theme codes) to keep bytes scanned —
# and therefore cost — low. Broad free-text columns such as ``AllNames`` are far
# larger and can multiply the scan cost, so they must be opted into explicitly
# via ``search_columns`` in config.
DEFAULT_SEARCH_COLUMNS = ["V2Themes"]

# GKG columns that hold semicolon-delimited theme codes rather than free text.
# Each entry looks like ``THEME_CODE,<charOffset>;THEME_CODE,<charOffset>;...``
# (e.g. ``ECON_INFLATION,1234;TAX_FNCACT_PRESIDENT,5678``). A plain equality or
# anchored match returns nothing, so these columns are normalized the way GDELT
# recommends — strip the numeric offsets, split on ``;``, and match whole theme
# codes via ``theme IN (...)`` inside an ``EXISTS`` over the ``UNNEST``.
THEME_CODE_COLUMNS = frozenset({"V2Themes", "Themes"})

# Default search patterns for theme discovery (case-insensitive substring match
# against normalized theme codes). Configurable from YAML.
DEFAULT_THEME_SEARCH_PATTERNS = [
    "inflation",
    "econ",
    "interest",
    "cost",
    "central_bank",
    "prices",
]

# Default theme column and row cap for theme discovery.
DEFAULT_THEME_COLUMN = "V2Themes"
DEFAULT_THEME_DISCOVERY_LIMIT = 100


def _to_date(value: str) -> date:
    """Parse ``YYYY-MM-DD`` or ``YYYYMMDD`` (str/int) into a ``date``."""
    text = str(value).strip().replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"invalid date {value!r}; expected YYYY-MM-DD or YYYYMMDD.")
    return date(int(text[0:4]), int(text[4:6]), int(text[6:8]))


def _sanitize_term(term: str) -> str:
    """Lowercase + escape a term for safe use inside a LIKE literal."""
    # Column is wrapped in LOWER(), so compare lowercase. Escape single quotes
    # and drop backslashes to keep the literal well-formed for v0.1.
    return term.strip().lower().replace("\\", "").replace("'", "''")


def _string_literal(term: str) -> str:
    """
    Render ``term`` as a safe single-quoted SQL string literal.

    Backslashes are dropped and single quotes are doubled (``''``) so the term
    cannot break out of the literal. Ordinary theme codes like ``ECON_INFLATION``
    are unchanged. Used for the ``theme IN (...)`` list, which compares against
    whole, normalized theme codes.
    """
    cleaned = term.strip().replace("\\", "").replace("'", "''")
    return f"'{cleaned}'"


def _normalized_themes_expr(column: str) -> str:
    """
    GDELT-recommended normalization of a theme-code column into an array of
    whole theme codes: strip the trailing ``,<offset>`` from each entry, drop a
    trailing delimiter, then split on ``;``.

    ``ECON_INFLATION,12;TAX_FNCACT_PRESIDENT,34;`` -> ``[ECON_INFLATION,
    TAX_FNCACT_PRESIDENT]``.
    """
    return f"SPLIT(RTRIM(REGEXP_REPLACE({column}, r',\\d+;', ';'), ';'), ';')"


def _theme_match_condition(column: str, terms: List[str]) -> str:
    """
    Build a normalized-UNNEST match predicate for theme-code ``column``.

    Splits the semicolon-delimited ``CODE,offset`` field into whole theme codes
    and matches any of ``terms`` exactly via ``theme IN (...)``. This is more
    robust than raw ``REGEXP_CONTAINS`` (it mirrors GDELT's own normalization)
    and naturally supports matching several bundle themes in one column.
    """
    in_list = ", ".join(_string_literal(t) for t in terms)
    return (
        "EXISTS (\n"
        "      SELECT 1\n"
        f"      FROM UNNEST({_normalized_themes_expr(column)}) AS theme\n"
        f"      WHERE theme IN ({in_list})\n"
        "    )"
    )


def _partition_clause(
    partition_filter: Optional[Dict[str, Any]], start: date, end: date
) -> str:
    """
    Build a partition-pruning predicate for ``partition_filter`` config.

    The ``DATE`` column on ``gdelt-bq.gdeltv2.gkg_partitioned`` is an ordinary
    integer field, so filtering on it does **not** prune partitions — BigQuery
    still scans the whole table. Constraining the partition pseudo-column
    (``_PARTITIONTIME`` by default) is what actually limits bytes scanned.

    Returns an empty string when the filter is disabled/absent. The end bound is
    exclusive and set to ``end + 1 day`` so the requested ``end`` day is fully
    included.
    """
    if not partition_filter or not partition_filter.get("enabled"):
        return ""

    column = str(partition_filter.get("column") or DEFAULT_PARTITION_COLUMN).strip()
    if not column:
        raise ValueError("partition_filter.column must be non-empty when enabled.")
    ptype = str(partition_filter.get("type") or DEFAULT_PARTITION_TYPE).strip().lower()

    start_iso = start.isoformat()
    end_exclusive_iso = (end + timedelta(days=1)).isoformat()

    if ptype == "date":
        cast = "DATE"
    elif ptype == "timestamp":
        cast = "TIMESTAMP"
    else:
        raise ValueError(
            f"partition_filter.type {ptype!r} is not supported; use 'timestamp' or 'date'."
        )

    return (
        f"{column} >= {cast}(\"{start_iso}\")\n"
        f"  AND {column} < {cast}(\"{end_exclusive_iso}\")\n  AND "
    )


def build_gdelt_daily_counts_query(
    table: str,
    start_date: str,
    end_date: str,
    query_terms: List[str],
    topic: Optional[str] = None,
    search_columns: Optional[List[str]] = None,
    partition_filter: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Build a daily-count query for ``table`` over ``[start_date, end_date]``.

    Args:
        table: Fully-qualified table, e.g. ``gdelt-bq.gdeltv2.gkg_partitioned``
            (supplied from config).
        start_date / end_date: ``YYYY-MM-DD`` or ``YYYYMMDD``.
        query_terms: Non-empty list of phrases/codes to match (OR'd across
            columns). For theme-code columns (``V2Themes`` / ``Themes``) the terms
            are matched as whole theme codes via a normalized ``UNNEST`` +
            ``theme IN (...)`` (GDELT's recommended normalization); for other
            columns each term is a case-insensitive substring ``LIKE``.
        topic: Optional label, included as a SQL comment for artifact context.
        search_columns: GKG columns to scan; defaults to ``["V2Themes"]``.
            Scanning broad free-text columns (e.g. ``AllNames``) dramatically
            increases bytes scanned, so they must be requested explicitly.
        partition_filter: Optional mapping ``{enabled, column, type}``. When
            ``enabled`` is true, a partition-pruning predicate on the partition
            pseudo-column (default ``_PARTITIONTIME``) is added so BigQuery scans
            only the requested days instead of the whole table. The logical
            ``DATE BETWEEN`` filter is always kept as well.

    Raises:
        ValueError: empty terms or an invalid/inverted date range.
    """
    terms = [t.strip() for t in (query_terms or []) if isinstance(t, str) and t.strip()]
    if not terms:
        raise ValueError("build_gdelt_daily_counts_query: query_terms must be non-empty.")

    columns = list(search_columns) if search_columns else list(DEFAULT_SEARCH_COLUMNS)
    if not columns:
        raise ValueError("build_gdelt_daily_counts_query: search_columns must be non-empty.")

    start_d = _to_date(start_date)
    end_d = _to_date(end_date)
    if start_d > end_d:
        raise ValueError(f"invalid date range: start {start_d} is after end {end_d}.")
    # GKG DATE is a 14-digit YYYYMMDDHHMMSS int; 8-digit bounds match no rows.
    start_dt = to_gdelt_gkg_start_datetime_int(start_d)
    end_dt = to_gdelt_gkg_end_datetime_int(end_d)

    # Theme-code columns get one normalized-UNNEST predicate matching any term;
    # free-text columns get a case-insensitive substring LIKE per term.
    conditions: List[str] = []
    for column in columns:
        if column in THEME_CODE_COLUMNS:
            conditions.append(_theme_match_condition(column, terms))
        else:
            for term in terms:
                conditions.append(f"LOWER({column}) LIKE '%{_sanitize_term(term)}%'")

    where_terms = "\n    OR ".join(conditions)
    topic_comment = f"-- topic: {topic}\n" if topic else ""
    partition_predicate = _partition_clause(partition_filter, start_d, end_d)

    return (
        f"{topic_comment}"
        "SELECT\n"
        "  DATE AS date,\n"
        "  COUNT(*) AS article_count\n"
        f"FROM `{table}`\n"
        f"WHERE {partition_predicate}DATE BETWEEN {start_dt} AND {end_dt}\n"
        "  AND (\n"
        f"    {where_terms}\n"
        "  )\n"
        "GROUP BY date\n"
        "ORDER BY date"
    )


def build_gdelt_theme_discovery_query(
    table: str,
    start_date: str,
    end_date: str,
    *,
    topic: Optional[str] = None,
    search_patterns: Optional[List[str]] = None,
    theme_column: str = DEFAULT_THEME_COLUMN,
    partition_filter: Optional[Dict[str, Any]] = None,
    limit: int = DEFAULT_THEME_DISCOVERY_LIMIT,
) -> str:
    """
    Build a theme-discovery / debug query over ``[start_date, end_date]``.

    Instead of counting articles for known theme codes, this lists which actual
    GDELT theme codes appear in the window and how often, filtered to codes whose
    (case-insensitive) text matches any of ``search_patterns``. It uses GDELT's
    recommended ``V2Themes`` normalization (strip ``,<offset>``, split on ``;``)
    so theme codes are inspected cleanly.

    Args:
        table: Fully-qualified table (from config).
        start_date / end_date: ``YYYY-MM-DD`` or ``YYYYMMDD``.
        topic: Optional label, emitted as a SQL comment for artifact context.
        search_patterns: Substring patterns to keep (OR'd). Defaults to
            :data:`DEFAULT_THEME_SEARCH_PATTERNS`. Each is lowercased and escaped.
        theme_column: Theme-code column to normalize/inspect (default
            ``V2Themes``).
        partition_filter: Same shape as the daily-count builder. When enabled, a
            ``_PARTITIONTIME`` predicate prunes partitions; ``DATE BETWEEN`` is
            always kept too.
        limit: Maximum number of theme rows returned (must be positive).

    Raises:
        ValueError: empty patterns, invalid/inverted date range, or non-positive
            ``limit``.
    """
    patterns = [
        p.strip() for p in (search_patterns or DEFAULT_THEME_SEARCH_PATTERNS)
        if isinstance(p, str) and p.strip()
    ]
    if not patterns:
        raise ValueError("build_gdelt_theme_discovery_query: search_patterns must be non-empty.")

    column = str(theme_column).strip() or DEFAULT_THEME_COLUMN

    start_d = _to_date(start_date)
    end_d = _to_date(end_date)
    if start_d > end_d:
        raise ValueError(f"invalid date range: start {start_d} is after end {end_d}.")
    # GKG DATE is a 14-digit YYYYMMDDHHMMSS int; 8-digit bounds match no rows.
    start_dt = to_gdelt_gkg_start_datetime_int(start_d)
    end_dt = to_gdelt_gkg_end_datetime_int(end_d)

    try:
        limit_i = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"limit must be a positive integer, got {limit!r}.") from exc
    if limit_i <= 0:
        raise ValueError(f"limit must be a positive integer, got {limit_i}.")

    topic_comment = f"-- topic: {topic}\n" if topic else ""
    partition_predicate = _partition_clause(partition_filter, start_d, end_d)
    like_clauses = "\n   OR ".join(
        f"LOWER(theme) LIKE '%{_sanitize_term(p)}%'" for p in patterns
    )

    return (
        f"{topic_comment}"
        "WITH nested AS (\n"
        "  SELECT\n"
        f"    {_normalized_themes_expr(column)} AS themes\n"
        f"  FROM `{table}`\n"
        f"  WHERE {partition_predicate}DATE BETWEEN {start_dt} AND {end_dt}\n"
        f"    AND LENGTH({column}) > 1\n"
        ")\n"
        "SELECT\n"
        "  theme,\n"
        "  COUNT(*) AS cnt\n"
        "FROM nested, UNNEST(themes) AS theme\n"
        f"WHERE {like_clauses}\n"
        "GROUP BY theme\n"
        "ORDER BY cnt DESC\n"
        f"LIMIT {limit_i}"
    )

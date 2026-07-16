"""
GDELT historical daily-count SQL builder (BigQuery Standard SQL).

Builds a bounded, aggregate-only query for *broad historical measurement*:
one row per day with an article count, filtered by term matches across a few
GDELT GKG text columns. The table comes from config (never hardcoded here at
call sites) so the same builder works against partitioned or other GKG tables.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

from common.date_windows import (
    to_gdelt_gkg_end_datetime_int,
    to_gdelt_gkg_start_datetime_int,
)

from news_data.bigquery.seed_matching import (
    SeedTerm,
    matched_seeds_array_expr,
    resolve_seed_terms,
    seed_match_predicate,
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

# ── theme-name matching modes ────────────────────────────────────────────────
#
# ``substring`` (the historical, default behavior) matches any theme code whose
# text *contains* the pattern: ``LOWER(theme) LIKE '%pattern%'``. That is why the
# bare pattern ``foundry`` matched the occupation theme ``TAX_FNCACT_FOUNDRYMAN``
# (its normalized text ``tax_fncact_foundryman`` contains ``foundry``).
#
# ``token`` matches the pattern only as a whole ``_``-delimited segment inside a
# theme code, so ``foundry`` does NOT match ``FOUNDRYMAN`` but DOES match a code
# like ``..._FOUNDRY`` or ``FOUNDRY_...``. ``exact`` matches the entire code.
# Existing callers keep ``substring`` behavior unless they opt in explicitly.
THEME_MATCH_SUBSTRING = "substring"
THEME_MATCH_TOKEN = "token"
THEME_MATCH_EXACT = "exact"
THEME_MATCH_MODES = frozenset({THEME_MATCH_SUBSTRING, THEME_MATCH_TOKEN, THEME_MATCH_EXACT})
DEFAULT_THEME_MATCH_MODE = THEME_MATCH_SUBSTRING

# Token/exact patterns must be delimiter-safe. Normalized GDELT theme codes are
# ``[A-Z0-9_]+``, so a safe lowercase pattern is ``[a-z0-9_]+`` only. Restricting
# the alphabet makes it impossible to inject regex metacharacters into the
# generated ``REGEXP_CONTAINS`` literal (no untrusted regex is interpolated).
_SAFE_THEME_PATTERN_RE = re.compile(r"^[a-z0-9_]+$")

# Seeded (record-based) theme discovery: which free-text GKG columns are scanned
# for seed terms (company / topic names), and the default candidate row cap.
DEFAULT_SEED_SEARCH_COLUMNS = ["V2Organizations"]
DEFAULT_SEED_SAMPLE_COLUMN = "SourceCommonName"
# Stable per-record identifier used for unique-record counting + sample ids.
DEFAULT_SEED_RECORD_ID_COLUMN = "GKGRECORDID"
# GDELT GKG document identifier — the source article URL/identifier for a record.
# Emitted ONLY inside the small representative-record sample (not aggregated), so
# reviewers can audit the sampled evidence. It is a wide column; its marginal
# scan cost is verified against the cost policy by a real dry run.
DEFAULT_DOCUMENT_ID_COLUMN = "DocumentIdentifier"
DEFAULT_SEEDED_DISCOVERY_LIMIT = 200
DEFAULT_SEEDED_SAMPLE_SIZE = 3

# Bump when the seeded-discovery SQL shape changes so cache keys invalidate.
# seeded-v1: raw substring seed match, COUNT(*) over unnested themes (double
#            counts within-record theme repeats), alphabetical sample sources.
# seeded-v2: token-boundary matching for company aliases + substring for
#            industry phrases (see seed_matching); per-record theme dedup +
#            COUNT(DISTINCT GKGRECORDID) unique-record counting; representative
#            sample sources via APPROX_TOP_COUNT (most frequent, not
#            alphabetical); emits source_count and the total_seed_records
#            denominator so seeded prevalence is computable.
SEEDED_QUERY_BUILDER_VERSION = "seeded-v2"

# Bump when the seeded *scoring* SQL shape changes so cache keys invalidate.
# scoring-v1: seed vs background counts via two scalar subqueries over `base`
#             (risked re-scanning the partition set 2-3x -> 2-3x bytes billed).
# scoring-v3: single-scan design. Two synthetic theme codes are appended to each
#             record's theme array so one GROUP BY yields, in ONE table pass:
#             per-theme seed/background counts; the two denominators (the
#             __TOTAL__ row, carried by every record); and per-canonical-seed
#             corpus record counts (the __SEED__<canonical> rows). Adds weekly
#             stability buckets, top-source/top-day concentration counts,
#             per-theme per-seed hit counts, and sample record ids — all without
#             a second scan. Association metrics, CIs, p-values, BH-FDR and both
#             rankings are computed offline in theme_scoring / the scoring task.
# scoring-v4: fixes a concentration defect — APPROX_TOP_COUNT counts the NULL
#             bucket (non-seed rows map to NULL), which outranked every real
#             value, forcing top_source_share/top_day_share to 1.0. Now requests
#             one extra slot and the scorer skips the NULL bucket to take the
#             first real source/day. Metric math (lift/CI/p-value) was unaffected.
# scoring-v5: statistical-integrity pass. (1) Returns the *complete* support-
#             qualified family (candidate_limit safety cap only) so BH-FDR runs
#             over every tested candidate, not an association-ranked top-N.
#             (2) Adds EXACT top-source / top-day concentration counts
#             (exact_top_source_count / exact_top_day_count) from one extra
#             seed-only aggregation grain over the same base scan, alongside the
#             approximate APPROX_TOP_COUNT values (provenance kept). (3) Adds
#             corpus-level record-seed-match count + multi-seed record count so
#             seed-composition shares have unambiguous denominators. (4) Emits a
#             hash-sampled (FARM_FINGERPRINT) sample_records struct array for
#             deterministic representative-record evidence. The disjoint non-seed
#             inferential 2x2 is derived offline (background_record_count is the
#             whole-corpus count; non-seed = whole - seed), needing no extra scan.
# scoring-v6: fail-closed + auditability pass. (1) Emits an INDEPENDENT support-
#             qualified family count (qualified_count CTE over per_theme, NOT
#             subject to the outer safety-cap LIMIT) so BH can refuse to run on a
#             truncated family. (2) Adds the GKG DocumentIdentifier (source URL)
#             to the small hash-sampled sample_records struct so representative
#             evidence is independently auditable. (3) all_matched_seeds is
#             carried per sampled record. Sparse-table Fisher-exact test selection
#             is an OFFLINE change (theme_scoring); no SQL impact.
SCORING_QUERY_BUILDER_VERSION = "scoring-v6"

# Safety cap on returned candidate rows. Chosen well above the true support-
# qualified family size (a few hundred to low thousands) so the FULL family is
# returned for offline BH-FDR; result_truncated flags if it is ever reached.
# Bytes billed are unaffected by row count (BigQuery bills scanned bytes, and a
# post-GROUP-BY LIMIT does not reduce the scan), so a high cap is cost-free.
DEFAULT_SCORING_CANDIDATE_LIMIT = 5000

# Synthetic theme codes injected per record so denominators + per-seed corpus
# diagnostics fall out of the same single-scan GROUP BY. Chosen so they cannot
# collide with a real normalized GDELT code (which is [A-Z0-9_], never leading
# double underscore in practice) and are trivially filtered out downstream.
SCORING_TOTAL_SENTINEL = "__TOTAL__"
SCORING_SEED_SENTINEL_PREFIX = "__SEED__"


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


def _safe_token_pattern(pattern: str) -> str:
    """
    Validate + normalize a pattern for ``token``/``exact`` theme matching.

    Returns the lowercase pattern when it is delimiter-safe (``[a-z0-9_]+``);
    raises otherwise so no regex metacharacter can ever reach the generated SQL.
    """
    p = str(pattern).strip().lower()
    if not _SAFE_THEME_PATTERN_RE.match(p):
        raise ValueError(
            f"theme match pattern {pattern!r} is not delimiter-safe; token/exact "
            "modes accept only lowercase letters, digits, and underscores "
            "(e.g. 'integrated_circuit'). Use match_mode='substring' for free-form "
            "patterns."
        )
    return p


def _theme_match_predicate(pattern: str, match_mode: str) -> str:
    """
    Build a single per-pattern predicate against the unnested ``theme`` alias.

    - ``substring``: ``LOWER(theme) LIKE '%pattern%'`` (historical default).
    - ``exact``: ``LOWER(theme) = 'pattern'``.
    - ``token``: ``REGEXP_CONTAINS(LOWER(theme), r'(^|_)pattern(_|$)')`` so the
      pattern only matches a whole ``_``-delimited segment (``foundry`` will not
      match ``foundryman``). The pattern is restricted to ``[a-z0-9_]+`` first,
      so the regex literal is never built from untrusted text.
    """
    if match_mode == THEME_MATCH_SUBSTRING:
        return f"LOWER(theme) LIKE '%{_sanitize_term(pattern)}%'"
    safe = _safe_token_pattern(pattern)
    if match_mode == THEME_MATCH_EXACT:
        return f"LOWER(theme) = '{safe}'"
    if match_mode == THEME_MATCH_TOKEN:
        return f"REGEXP_CONTAINS(LOWER(theme), r'(^|_){safe}(_|$)')"
    raise ValueError(
        f"unsupported theme match_mode {match_mode!r}; expected one of "
        f"{sorted(THEME_MATCH_MODES)}."
    )


def _partition_clause(partition_filter: Optional[Dict[str, Any]], start: date, end: date) -> str:
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
        f'{column} >= {cast}("{start_iso}")\n'
        f'  AND {column} < {cast}("{end_exclusive_iso}")\n  AND '
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
    match_mode: str = DEFAULT_THEME_MATCH_MODE,
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
        search_patterns: Patterns to keep (OR'd). Defaults to
            :data:`DEFAULT_THEME_SEARCH_PATTERNS`.
        theme_column: Theme-code column to normalize/inspect (default
            ``V2Themes``).
        partition_filter: Same shape as the daily-count builder. When enabled, a
            ``_PARTITIONTIME`` predicate prunes partitions; ``DATE BETWEEN`` is
            always kept too.
        limit: Maximum number of theme rows returned (must be positive).
        match_mode: How each pattern is matched against a normalized theme code
            (:data:`THEME_MATCH_MODES`). ``substring`` (default) is the historical
            ``LIKE '%pattern%'`` behavior; ``token`` matches only a whole
            ``_``-delimited segment (so ``foundry`` will not match
            ``FOUNDRYMAN``); ``exact`` matches the whole code. The mode is emitted
            as a SQL comment for artifact/lineage transparency.

    Raises:
        ValueError: empty patterns, invalid/inverted date range, non-positive
            ``limit``, an unknown ``match_mode``, or a token/exact pattern that is
            not delimiter-safe.
    """
    patterns = [
        p.strip()
        for p in (search_patterns or DEFAULT_THEME_SEARCH_PATTERNS)
        if isinstance(p, str) and p.strip()
    ]
    if not patterns:
        raise ValueError("build_gdelt_theme_discovery_query: search_patterns must be non-empty.")

    mode = str(match_mode).strip().lower() or DEFAULT_THEME_MATCH_MODE
    if mode not in THEME_MATCH_MODES:
        raise ValueError(
            f"build_gdelt_theme_discovery_query: match_mode {match_mode!r} is not "
            f"supported; expected one of {sorted(THEME_MATCH_MODES)}."
        )

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
    mode_comment = f"-- match_mode: {mode}\n"
    partition_predicate = _partition_clause(partition_filter, start_d, end_d)
    like_clauses = "\n   OR ".join(_theme_match_predicate(p, mode) for p in patterns)

    return (
        f"{topic_comment}"
        f"{mode_comment}"
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


DEFAULT_SCORING_MIN_SUPPORT = 25


def build_gdelt_seeded_theme_scoring_query(
    table: str,
    start_date: str,
    end_date: str,
    *,
    topic: Optional[str] = None,
    seed_terms: Optional[Sequence[str]] = None,
    seed_specs: Optional[Sequence[dict]] = None,
    seed_search_columns: Optional[List[str]] = None,
    theme_column: str = DEFAULT_THEME_COLUMN,
    sample_source_column: str = DEFAULT_SEED_SAMPLE_COLUMN,
    record_id_column: str = DEFAULT_SEED_RECORD_ID_COLUMN,
    document_id_column: str = DEFAULT_DOCUMENT_ID_COLUMN,
    partition_filter: Optional[Dict[str, Any]] = None,
    limit: int = DEFAULT_SEEDED_DISCOVERY_LIMIT,
    sample_size: int = DEFAULT_SEEDED_SAMPLE_SIZE,
    min_support: int = DEFAULT_SCORING_MIN_SUPPORT,
    candidate_limit: int = DEFAULT_SCORING_CANDIDATE_LIMIT,
) -> str:
    """
    Build a **single-scan** seed-vs-background theme-scoring query.

    Background prevalence is what turns raw co-occurrence frequency into an
    association signal (lift). Crucially it comes almost for free: BigQuery bills
    by *bytes scanned*, and a row-level ``WHERE`` on the seed match does not
    reduce scanned bytes — only partition pruning does. So this query scans the
    same columns/partitions as seeded discovery **once**.

    ``scoring-v3`` keeps it to a single table pass by appending two synthetic
    theme codes to every record's theme array before the ``GROUP BY theme``:

    - ``__TOTAL__`` — carried by *every* record, so its aggregated row yields the
      two denominators ``total_seed_records`` / ``total_background_records``
      without a second scan or a scalar sub-query over ``base``;
    - ``__SEED__<canonical>`` — one per matched canonical seed on the record, so
      those rows yield per-seed *corpus* record counts (seed match diagnostics).

    Per real theme it computes: ``seed_record_count`` (unique seeded records
    carrying it), ``background_record_count`` (unique records overall), weekly
    stability buckets (``wk_0``..``wk_{n-1}``), ``top_source_count`` /
    ``top_day_count`` (concentration), per-seed hit counts (``seed_hits``),
    representative ``sample_sources`` / ``sample_record_ids``, and first/last day.

    A ``HAVING seed_record_count >= min_support`` floor drops real themes too rare
    to score stably; the synthetic rows are exempt (``STARTS_WITH(theme,'__')``)
    and always sort first so the row cap never evicts a denominator.
    """
    seeds: List[SeedTerm] = resolve_seed_terms(seed_specs=seed_specs, seed_terms=seed_terms)

    columns = [
        str(c).strip()
        for c in (seed_search_columns or DEFAULT_SEED_SEARCH_COLUMNS)
        if str(c).strip()
    ]
    if not columns:
        raise ValueError(
            "build_gdelt_seeded_theme_scoring_query: seed_search_columns must be non-empty."
        )

    theme_col = str(theme_column).strip() or DEFAULT_THEME_COLUMN
    source_col = str(sample_source_column).strip() or DEFAULT_SEED_SAMPLE_COLUMN
    rec_id_col = str(record_id_column).strip() or DEFAULT_SEED_RECORD_ID_COLUMN
    doc_id_col = str(document_id_column).strip() or DEFAULT_DOCUMENT_ID_COLUMN

    start_d = _to_date(start_date)
    end_d = _to_date(end_date)
    if start_d > end_d:
        raise ValueError(f"invalid date range: start {start_d} is after end {end_d}.")
    start_dt = to_gdelt_gkg_start_datetime_int(start_d)
    end_dt = to_gdelt_gkg_end_datetime_int(end_d)
    start_iso = start_d.isoformat()
    n_weeks = ((end_d - start_d).days // 7) + 1

    try:
        limit_i = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"limit must be a positive integer, got {limit!r}.") from exc
    if limit_i <= 0:
        raise ValueError(f"limit must be a positive integer, got {limit_i}.")
    try:
        sample_i = int(sample_size)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"sample_size must be a positive integer, got {sample_size!r}.") from exc
    if sample_i <= 0:
        raise ValueError(f"sample_size must be a positive integer, got {sample_i}.")
    try:
        min_support_i = int(min_support)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"min_support must be a non-negative integer, got {min_support!r}."
        ) from exc
    if min_support_i < 0:
        raise ValueError(f"min_support must be non-negative, got {min_support_i}.")
    try:
        candidate_limit_i = int(candidate_limit)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"candidate_limit must be a positive integer, got {candidate_limit!r}."
        ) from exc
    if candidate_limit_i <= 0:
        raise ValueError(f"candidate_limit must be a positive integer, got {candidate_limit_i}.")

    seed_blob = _seed_blob_expr(columns)
    seed_flag = (
        "(\n      "
        + " OR\n      ".join(seed_match_predicate(seed_blob, s) for s in seeds)
        + "\n    )"
    )
    matched_seeds_expr = matched_seeds_array_expr(seed_blob, seeds)

    # Fixed weekly-bucket seed counts. week_idx is stable per record (computed in
    # `base`); each bucket counts DISTINCT seeded records so within-record theme
    # repeats never inflate it.
    weekly_cols = ",\n".join(
        f"  COUNT(DISTINCT IF(is_seed AND week_idx = {w}, rec_id, NULL)) AS wk_{w}"
        for w in range(n_weeks)
    )

    # Per-theme per-canonical-seed record counts. Themes are de-duplicated per
    # record, so within a theme group each record is exactly one row and COUNTIF
    # equals a distinct-record count.
    seed_hits = ",\n    ".join(
        f"STRUCT('{s.canonical}' AS seed, "
        f"COUNTIF(is_seed AND '{s.canonical}' IN UNNEST(matched_seeds)) AS n)"
        for s in seeds
    )

    # Synthetic theme injection: __TOTAL__ (every record) + __SEED__<canonical>
    # (one per matched seed). Real themes come first, then the sentinels, so a
    # record with no theme text still contributes to the denominators.
    themes_plus = (
        "ARRAY_CONCAT(\n"
        "      themes,\n"
        f"      ['{SCORING_TOTAL_SENTINEL}'],\n"
        f"      ARRAY(SELECT CONCAT('{SCORING_SEED_SENTINEL_PREFIX}', s) "
        "FROM UNNEST(matched_seeds) AS s)\n"
        "    )"
    )

    topic_comment = f"-- topic: {topic}\n" if topic else ""
    partition_predicate = _partition_clause(partition_filter, start_d, end_d)
    # Return the COMPLETE support-qualified family (so offline BH-FDR is exact),
    # capped only by a high safety limit; +1 __TOTAL__ + one row per seed for the
    # synthetic denominator/diagnostic rows the task strips back out.
    effective_limit = candidate_limit_i + 1 + len(seeds)

    # Weekly columns projected back out of the per_theme CTE by the final SELECT.
    weekly_passthrough = ",\n".join(f"  p.wk_{w}" for w in range(n_weeks))

    return (
        f"{topic_comment}"
        "-- seeded-vs-background theme scoring in one scan (human-review candidates)\n"
        "WITH base AS (\n"
        "  SELECT\n"
        f"    CAST({rec_id_col} AS STRING) AS rec_id,\n"
        "    DIV(DATE, 1000000) AS day,\n"
        "    DIV(DATE_DIFF(\n"
        "      PARSE_DATE('%Y%m%d', CAST(DIV(DATE, 1000000) AS STRING)),\n"
        f"      DATE '{start_iso}', DAY), 7) AS week_idx,\n"
        f"    {source_col} AS source,\n"
        f"    {doc_id_col} AS doc_id,\n"
        f"    ARRAY(SELECT DISTINCT th FROM UNNEST({_normalized_themes_expr(theme_col)}) AS th\n"
        "          WHERE LENGTH(th) > 1) AS themes,\n"
        f"    {seed_flag} AS is_seed,\n"
        f"    {matched_seeds_expr} AS matched_seeds\n"
        f"  FROM `{table}`\n"
        f"  WHERE {partition_predicate}DATE BETWEEN {start_dt} AND {end_dt}\n"
        f"    AND LENGTH({theme_col}) > 1\n"
        "),\n"
        "labeled AS (\n"
        "  SELECT\n"
        "    rec_id, day, week_idx, source, doc_id, is_seed, matched_seeds,\n"
        f"    {themes_plus} AS themes_plus\n"
        "  FROM base\n"
        "),\n"
        "exploded AS (\n"
        "  SELECT theme, rec_id, day, week_idx, source, doc_id, is_seed, matched_seeds\n"
        "  FROM labeled, UNNEST(themes_plus) AS theme\n"
        "  WHERE LENGTH(theme) > 1\n"
        "),\n"
        # EXACT concentration over SEEDED records only (real themes), so the
        # top-source / top-day shares near the screening thresholds do not depend
        # on APPROX_TOP_COUNT's sketch. Derived from the same base scan.
        "seed_dim AS (\n"
        "  SELECT theme, rec_id, source, day\n"
        "  FROM exploded\n"
        "  WHERE is_seed AND NOT STARTS_WITH(theme, '__')\n"
        "),\n"
        "src_top AS (\n"
        "  SELECT theme, MAX(c) AS exact_top_source_count FROM (\n"
        "    SELECT theme, source, COUNT(DISTINCT rec_id) AS c\n"
        "    FROM seed_dim WHERE source IS NOT NULL GROUP BY theme, source\n"
        "  ) GROUP BY theme\n"
        "),\n"
        "day_top AS (\n"
        "  SELECT theme, MAX(c) AS exact_top_day_count FROM (\n"
        "    SELECT theme, day, COUNT(DISTINCT rec_id) AS c\n"
        "    FROM seed_dim WHERE day IS NOT NULL GROUP BY theme, day\n"
        "  ) GROUP BY theme\n"
        "),\n"
        "per_theme AS (\n"
        "  SELECT\n"
        "    theme,\n"
        "    COUNT(DISTINCT IF(is_seed, rec_id, NULL)) AS seed_record_count,\n"
        "    COUNT(DISTINCT rec_id) AS background_record_count,\n"
        "    COUNT(DISTINCT IF(is_seed, day, NULL)) AS distinct_day_count,\n"
        "    COUNT(DISTINCT IF(is_seed, week_idx, NULL)) AS periods_present,\n"
        "    COUNT(DISTINCT IF(is_seed, source, NULL)) AS source_count,\n"
        # Corpus-level seed-composition denominators (only meaningful on the
        # __TOTAL__ row, where each record appears exactly once): total number of
        # record-seed matches and the count of records matching >1 canonical seed.
        "    SUM(IF(is_seed, ARRAY_LENGTH(matched_seeds), 0)) AS seed_match_count,\n"
        "    COUNT(DISTINCT IF(is_seed AND ARRAY_LENGTH(matched_seeds) > 1, rec_id, NULL))"
        " AS multi_seed_record_count,\n"
        "    MIN(IF(is_seed, day, NULL)) AS first_seen,\n"
        "    MAX(IF(is_seed, day, NULL)) AS last_seen,\n"
        f"{weekly_cols},\n"
        # APPROX_TOP_COUNT returns ARRAY<STRUCT<value, count>>. The NULL bucket
        # (non-seed rows) can rank first, so one extra slot is requested and the
        # scorer skips it. Kept alongside the EXACT counts for provenance.
        f"    APPROX_TOP_COUNT(IF(is_seed, source, NULL), {sample_i + 1}) AS top_sources,\n"
        "    APPROX_TOP_COUNT(IF(is_seed, CAST(day AS STRING), NULL), "
        f"{sample_i + 1}) AS top_days,\n"
        f"    [\n    {seed_hits}\n  ] AS seed_hits,\n"
        "    ARRAY_AGG(IF(is_seed, rec_id, NULL) IGNORE NULLS\n"
        f"        ORDER BY rec_id LIMIT {sample_i}) AS sample_record_ids,\n"
        # Deterministic hash-sampled representative records (FARM_FINGERPRINT of
        # the GKGRECORDID) with the fields needed for auditable record-level
        # evidence, including the GKG DocumentIdentifier (source URL).
        "    ARRAY_AGG(IF(is_seed,"
        " STRUCT(rec_id, source, day, doc_id, matched_seeds), NULL)"
        " IGNORE NULLS\n"
        f"        ORDER BY FARM_FINGERPRINT(rec_id) LIMIT {sample_i}) AS sample_records\n"
        "  FROM exploded\n"
        "  GROUP BY theme\n"
        f"  HAVING seed_record_count >= {min_support_i} OR STARTS_WITH(theme, '__')\n"
        "),\n"
        # INDEPENDENT support-qualified family size: counts every qualified REAL
        # theme in per_theme, computed BEFORE (and unaffected by) the outer
        # safety-cap LIMIT. The offline scorer compares this against the cap to
        # prove the family is complete; if it exceeds the cap, BH-FDR is refused.
        "qualified_count AS (\n"
        "  SELECT COUNTIF(NOT STARTS_WITH(theme, '__')) AS n FROM per_theme\n"
        ")\n"
        "SELECT\n"
        "  qc.n AS total_support_qualified_candidates,\n"
        "  p.theme AS theme_code,\n"
        "  p.seed_record_count,\n"
        "  p.background_record_count,\n"
        "  p.distinct_day_count,\n"
        "  p.periods_present,\n"
        "  p.source_count,\n"
        "  p.seed_match_count,\n"
        "  p.multi_seed_record_count,\n"
        "  p.first_seen,\n"
        "  p.last_seen,\n"
        f"{weekly_passthrough},\n"
        "  p.top_sources,\n"
        "  p.top_days,\n"
        "  p.seed_hits,\n"
        "  p.sample_record_ids,\n"
        "  p.sample_records,\n"
        "  src_top.exact_top_source_count,\n"
        "  day_top.exact_top_day_count\n"
        "FROM per_theme p\n"
        "CROSS JOIN qualified_count qc\n"
        "LEFT JOIN src_top ON src_top.theme = p.theme\n"
        "LEFT JOIN day_top ON day_top.theme = p.theme\n"
        # Deterministic order. Sentinels first so the safety cap never evicts a
        # denominator; then an association proxy (seed/background is exactly
        # proportional to lift); support and theme code break ties. NOTE: the
        # LIMIT is only a safety cap — the support floor in HAVING defines the
        # family, and it is returned in full for offline BH-FDR.
        "ORDER BY STARTS_WITH(p.theme, '__') DESC,\n"
        "  SAFE_DIVIDE(p.seed_record_count, p.background_record_count) DESC,\n"
        "  p.seed_record_count DESC, p.theme\n"
        f"LIMIT {effective_limit}"
    )


def _seed_blob_expr(columns: List[str]) -> str:
    """
    Build a lowercased concatenation of the seed search columns.

    ``LOWER(CONCAT(IFNULL(V2Organizations,''), ' ', IFNULL(AllNames,'')))`` — the
    single text blob each seed term is matched against. Only the requested
    columns are referenced, so an operator controls exactly which (and how many)
    free-text columns are scanned, keeping bytes-scanned predictable.
    """
    parts = ", ' ', ".join(f"IFNULL({c}, '')" for c in columns)
    return f"LOWER(CONCAT({parts}))"


def build_gdelt_seeded_theme_discovery_query(
    table: str,
    start_date: str,
    end_date: str,
    *,
    topic: Optional[str] = None,
    seed_terms: Optional[Sequence[str]] = None,
    seed_specs: Optional[Sequence[dict]] = None,
    seed_search_columns: Optional[List[str]] = None,
    theme_column: str = DEFAULT_THEME_COLUMN,
    sample_source_column: str = DEFAULT_SEED_SAMPLE_COLUMN,
    record_id_column: str = DEFAULT_SEED_RECORD_ID_COLUMN,
    partition_filter: Optional[Dict[str, Any]] = None,
    limit: int = DEFAULT_SEEDED_DISCOVERY_LIMIT,
    sample_size: int = DEFAULT_SEEDED_SAMPLE_SIZE,
) -> str:
    """
    Build a *seed-record* theme-discovery query over ``[start_date, end_date]``.

    Substring theme-name discovery fails when GDELT exposes no clean taxonomy code
    containing the topic word. This alternative works from *records* instead of
    theme names: it selects GKG rows whose free-text seed columns mention any
    seed term (e.g. ``NVIDIA``, ``TSMC``, ``semiconductor``), extracts the theme
    codes carried by those records, and ranks candidate themes by how many seeded
    *records* carry them. The output is a human-review candidate list — frequency
    of co-occurrence is a ranking signal, not proof of semantic relevance.

    ``seeded-v2`` fixes three defects of the first version (see
    :data:`SEEDED_QUERY_BUILDER_VERSION`):

    1. **Safe matching.** Company aliases are matched only as whole tokens (so
       ``intel`` no longer matches ``intelligence``); industry phrases keep
       substring matching. See :mod:`news_data.bigquery.seed_matching`.
    2. **Unique-record counting.** Themes are de-duplicated *within* each record
       and counted with ``COUNT(DISTINCT GKGRECORDID)``, so a theme repeated at
       several character offsets in one document counts once.
    3. **Representative samples.** ``sample_sources`` are the most frequent
       sources (``APPROX_TOP_COUNT``), not the alphabetically-first three.

    Each candidate row has: ``theme_code``, ``record_count`` (unique seeded
    records carrying it), ``distinct_day_count``, ``first_seen``/``last_seen``
    (``YYYYMMDD`` day ints), ``matched_seed_terms`` (canonical seed labels present
    in those records), ``sample_sources`` (most frequent source domains),
    ``source_count`` (distinct sources), and ``total_seed_records`` (the corpus
    denominator, identical on every row).

    Args:
        table / start_date / end_date / topic / partition_filter: as elsewhere.
        seed_terms: Legacy flat seed list; each term is auto-classified as a
            company alias (boundary match) or industry phrase (substring).
        seed_specs: Structured seeds ``[{canonical, kind, aliases?}]``; wins over
            ``seed_terms`` when provided.
        seed_search_columns: GKG free-text columns scanned for seeds; defaults to
            :data:`DEFAULT_SEED_SEARCH_COLUMNS` (``V2Organizations`` only, to keep
            bytes scanned low). Broad columns (``AllNames``) must be opted in.
        theme_column: Theme-code column to normalize/inspect (default ``V2Themes``).
        sample_source_column: Column used for sample source identifiers.
        record_id_column: Stable per-record id column for unique-record counting.
        limit: Maximum candidate rows returned (must be positive).
        sample_size: Max sample sources kept per candidate.

    Raises:
        ValueError: empty/invalid seeds/columns, invalid date range, or
            non-positive ``limit`` / ``sample_size``.
    """
    seeds: List[SeedTerm] = resolve_seed_terms(seed_specs=seed_specs, seed_terms=seed_terms)

    columns = [
        str(c).strip()
        for c in (seed_search_columns or DEFAULT_SEED_SEARCH_COLUMNS)
        if str(c).strip()
    ]
    if not columns:
        raise ValueError(
            "build_gdelt_seeded_theme_discovery_query: seed_search_columns must be non-empty."
        )

    theme_col = str(theme_column).strip() or DEFAULT_THEME_COLUMN
    source_col = str(sample_source_column).strip() or DEFAULT_SEED_SAMPLE_COLUMN
    rec_id_col = str(record_id_column).strip() or DEFAULT_SEED_RECORD_ID_COLUMN

    start_d = _to_date(start_date)
    end_d = _to_date(end_date)
    if start_d > end_d:
        raise ValueError(f"invalid date range: start {start_d} is after end {end_d}.")
    start_dt = to_gdelt_gkg_start_datetime_int(start_d)
    end_dt = to_gdelt_gkg_end_datetime_int(end_d)

    try:
        limit_i = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"limit must be a positive integer, got {limit!r}.") from exc
    if limit_i <= 0:
        raise ValueError(f"limit must be a positive integer, got {limit_i}.")
    try:
        sample_i = int(sample_size)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"sample_size must be a positive integer, got {sample_size!r}.") from exc
    if sample_i <= 0:
        raise ValueError(f"sample_size must be a positive integer, got {sample_i}.")

    seed_blob = _seed_blob_expr(columns)
    # Per-seed match predicate (boundary for company aliases, substring for
    # phrases) and the array of canonical labels each record matched.
    seed_filter = " OR\n        ".join(seed_match_predicate(seed_blob, s) for s in seeds)
    matched_seeds_expr = matched_seeds_array_expr(seed_blob, seeds)

    topic_comment = f"-- topic: {topic}\n" if topic else ""
    partition_predicate = _partition_clause(partition_filter, start_d, end_d)

    # DIV(DATE, 1000000) turns the 14-digit YYYYMMDDHHMMSS int into a YYYYMMDD day.
    # Themes are de-duplicated per record so a theme repeated at multiple char
    # offsets in one document is not double-counted.
    return (
        f"{topic_comment}"
        "-- seeded record-based theme discovery (human-review candidates)\n"
        "WITH matched AS (\n"
        "  SELECT\n"
        f"    CAST({rec_id_col} AS STRING) AS rec_id,\n"
        "    DIV(DATE, 1000000) AS day,\n"
        f"    {source_col} AS source,\n"
        f"    ARRAY(SELECT DISTINCT th FROM UNNEST({_normalized_themes_expr(theme_col)}) AS th\n"
        "          WHERE LENGTH(th) > 1) AS themes,\n"
        f"    {matched_seeds_expr} AS matched_seeds\n"
        f"  FROM `{table}`\n"
        f"  WHERE {partition_predicate}DATE BETWEEN {start_dt} AND {end_dt}\n"
        f"    AND LENGTH({theme_col}) > 1\n"
        "    AND (\n"
        f"        {seed_filter}\n"
        "    )\n"
        ")\n"
        "SELECT\n"
        "  theme_code,\n"
        "  record_count,\n"
        "  distinct_day_count,\n"
        "  first_seen,\n"
        "  last_seen,\n"
        "  ARRAY(SELECT DISTINCT ms FROM UNNEST(all_matched_seeds) AS ms ORDER BY ms)\n"
        "    AS matched_seed_terms,\n"
        "  sample_sources,\n"
        "  source_count,\n"
        "  (SELECT COUNT(DISTINCT rec_id) FROM matched) AS total_seed_records\n"
        "FROM (\n"
        "  SELECT\n"
        "    theme AS theme_code,\n"
        "    COUNT(DISTINCT rec_id) AS record_count,\n"
        "    COUNT(DISTINCT day) AS distinct_day_count,\n"
        "    COUNT(DISTINCT source) AS source_count,\n"
        "    MIN(day) AS first_seen,\n"
        "    MAX(day) AS last_seen,\n"
        "    ARRAY_CONCAT_AGG(matched_seeds) AS all_matched_seeds,\n"
        f"    ARRAY(SELECT t.value FROM UNNEST(APPROX_TOP_COUNT(source, {sample_i})) AS t\n"
        "          WHERE t.value IS NOT NULL) AS sample_sources\n"
        "  FROM matched, UNNEST(themes) AS theme\n"
        "  WHERE LENGTH(theme) > 1\n"
        "  GROUP BY theme\n"
        "  ORDER BY record_count DESC, theme\n"
        f"  LIMIT {limit_i}\n"
        ")\n"
        "ORDER BY record_count DESC, theme_code"
    )

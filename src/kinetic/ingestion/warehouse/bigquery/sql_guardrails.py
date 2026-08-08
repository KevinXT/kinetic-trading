"""
Defensive SQL guardrails for the BigQuery provider path.

These checks are intentionally simple and conservative. They are a safety net,
not a full SQL parser. The only SQL this project sends to BigQuery is built by
``gdelt_queries.build_gdelt_daily_counts_query``; these guardrails make sure
nothing dangerous or accidentally expensive slips through.

Rules (v0.1):
  - Reject ``SELECT *`` (forces explicit, narrow column lists).
  - Require a ``WHERE`` clause.
  - Require a recognizable date filter so historical scans stay bounded:
    ``DATE BETWEEN`` / ``_PARTITIONTIME`` / ``_TABLE_SUFFIX``.
  - If ``allowed_tables`` is given, the SQL must reference one of them.
  - Reject multi-statement SQL (a ``;`` followed by more SQL).
  - Reject DDL/DML keywords (DELETE/UPDATE/INSERT/CREATE/DROP/MERGE/ALTER).
"""

from __future__ import annotations

import re
from typing import List, Optional

# Date-filter patterns that keep a historical GDELT scan bounded.
_DATE_FILTER_TOKENS = ("DATE BETWEEN", "_PARTITIONTIME", "_TABLE_SUFFIX")

# Forbidden DDL/DML statements (whole-word match, case-insensitive).
_FORBIDDEN_KEYWORDS = (
    "DELETE",
    "UPDATE",
    "INSERT",
    "CREATE",
    "DROP",
    "MERGE",
    "ALTER",
)

_SELECT_STAR_RE = re.compile(r"SELECT\s+\*", re.IGNORECASE)


def _strip_string_literals(sql: str) -> str:
    """
    Replace the contents of single-quoted string literals with spaces.

    Used so the multi-statement (``;``) check inspects only SQL *code*, not
    characters inside literals. Theme-matching predicates legitimately embed
    ``;`` inside ``REGEXP_CONTAINS(..., r'(^|;)CODE(,|;|$)')`` literals, which must
    not be mistaken for statement separators. Handles ``\\'`` escapes and ``''``
    doubled-quote escapes. This is a conservative scanner, not a full parser.
    """
    out: List[str] = []
    i = 0
    n = len(sql)
    in_str = False
    while i < n:
        ch = sql[i]
        if not in_str:
            out.append(ch)
            if ch == "'":
                in_str = True
            i += 1
        else:
            if ch == "\\" and i + 1 < n:
                out.append("  ")
                i += 2
            elif ch == "'" and i + 1 < n and sql[i + 1] == "'":
                out.append("  ")
                i += 2
            elif ch == "'":
                out.append("'")
                in_str = False
                i += 1
            else:
                out.append(" ")
                i += 1
    return "".join(out)


def validate_bigquery_sql(sql: str, allowed_tables: Optional[List[str]] = None) -> None:
    """
    Validate ``sql`` against the v0.1 guardrails.

    Returns ``None`` when the query is acceptable; raises ``ValueError`` with a
    clear message otherwise.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("SQL guardrail: query must be a non-empty string.")

    text = sql.strip()
    upper = text.upper()

    if _SELECT_STAR_RE.search(text):
        raise ValueError("SQL guardrail: 'SELECT *' is not allowed; select explicit columns.")

    if "WHERE" not in upper:
        raise ValueError("SQL guardrail: query must include a WHERE clause to bound the scan.")

    if not any(token in upper for token in _DATE_FILTER_TOKENS):
        raise ValueError(
            "SQL guardrail: query must include a date filter "
            f"({', '.join(_DATE_FILTER_TOKENS)}) to bound a historical scan."
        )

    # Reject multi-statement SQL: a semicolon followed by more SQL. Inspect a
    # copy with string-literal contents removed so semicolons inside literals
    # (e.g. REGEXP_CONTAINS theme patterns) are not mistaken for separators.
    code_only = _strip_string_literals(text)
    without_trailing = code_only.rstrip().rstrip(";")
    if ";" in without_trailing:
        raise ValueError(
            "SQL guardrail: multi-statement SQL (';' followed by more SQL) " "is not allowed."
        )

    for keyword in _FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", upper):
            raise ValueError(
                f"SQL guardrail: DDL/DML keyword {keyword!r} is not allowed; "
                "this path is read-only."
            )

    if allowed_tables:
        if not any(table and table in text for table in allowed_tables):
            raise ValueError(
                "SQL guardrail: query must reference one of the allowed tables: "
                f"{allowed_tables}."
            )

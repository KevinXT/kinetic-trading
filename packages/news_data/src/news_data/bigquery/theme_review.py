"""
Turn theme-discovery output into a human-review worksheet.

Discovery (name-based ``theme_discovery.csv`` or seeded
``seeded_theme_candidates.csv``) only *lists* candidate theme codes. A human must
still decide which codes genuinely represent the topic and which are false
positives (occupation, geographic, generic-technology, or broad-economic themes).

This module produces a review worksheet — one row per candidate with a
``keep_or_reject`` decision column an operator fills in — kept deliberately
separate from ``configs/gdelt_theme_bundles.yaml``. Nothing here ever writes to
the bundle: promotion into a bundle stays a manual edit.

Known false positives (e.g. ``TAX_FNCACT_FOUNDRYMAN``) are pre-filled as
``reject`` with a rationale so a reviewer is not asked to re-adjudicate a code
that was already rejected.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, Iterable, List

JsonDict = Dict[str, Any]

REVIEW_FIELDS = [
    "theme_code",
    "count",
    "keep_or_reject",
    "rationale",
    "ambiguity_notes",
    "source_run_id",
]

# Codes already reviewed and rejected as semiconductor false positives. Encoded
# as review metadata (not hard-coded into any query) so the list is auditable.
DEFAULT_KNOWN_REJECTED: Dict[str, str] = {
    "TAX_FNCACT_FOUNDRYMAN": (
        "False-positive substring match on 'foundry'; occupation 'foundryman', "
        "not semiconductor fabrication or chip foundries."
    ),
}

# Column aliases so both discovery artifact shapes are accepted.
_THEME_KEYS = ("theme_code", "theme")
_COUNT_KEYS = ("count", "record_count", "cnt")


def _first(row: JsonDict, keys: Iterable[str], default: Any = "") -> Any:
    for key in keys:
        if key in row and str(row[key]).strip():
            return row[key]
    return default


def build_review_rows(
    candidates: Iterable[JsonDict],
    *,
    source_run_id: str,
    known_rejected: Dict[str, str] | None = None,
) -> List[JsonDict]:
    """
    Build review worksheet rows from discovery candidates.

    ``candidates`` may use either artifact shape (``theme``/``count`` or
    ``theme_code``/``record_count``). Codes in ``known_rejected`` are pre-filled
    with ``keep_or_reject='reject'`` and the stored rationale; every other code is
    left blank for the reviewer. Rows preserve input order.
    """
    rejected = {k.upper(): v for k, v in (known_rejected or DEFAULT_KNOWN_REJECTED).items()}
    rows: List[JsonDict] = []
    for cand in candidates:
        theme = str(_first(cand, _THEME_KEYS)).strip()
        if not theme:
            continue
        count = _first(cand, _COUNT_KEYS, default="")
        rationale = ""
        keep_or_reject = ""
        ambiguity = ""
        if theme.upper() in rejected:
            keep_or_reject = "reject"
            rationale = rejected[theme.upper()]
            ambiguity = "known false positive (pre-filled)"
        rows.append(
            {
                "theme_code": theme,
                "count": count,
                "keep_or_reject": keep_or_reject,
                "rationale": rationale,
                "ambiguity_notes": ambiguity,
                "source_run_id": source_run_id,
            }
        )
    return rows


def read_discovery_csv(path: str | Path) -> List[JsonDict]:
    """Read a discovery artifact CSV (either shape) into a list of dict rows."""
    p = Path(path)
    with p.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def write_review_worksheet(rows: Iterable[JsonDict], path: str | Path) -> None:
    """Write review rows to ``path`` with the canonical worksheet columns."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in REVIEW_FIELDS})

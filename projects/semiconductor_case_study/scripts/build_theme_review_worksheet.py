#!/usr/bin/env python3
"""Turn theme-discovery output into a human-review worksheet.

Reads a discovery artifact CSV (name-based ``theme_discovery.csv`` or seeded
``seeded_theme_candidates.csv``) and writes a review worksheet with a
``keep_or_reject`` column for a human to fill in. Known false positives (e.g.
TAX_FNCACT_FOUNDRYMAN) are pre-filled as ``reject``.

This never edits configs/gdelt_theme_bundles.yaml — promoting a reviewed code
into a bundle stays a deliberate manual edit.

Usage:
    python scripts/build_theme_review_worksheet.py \
        --input  warehouse/runs/.../artifacts/theme_discovery.csv \
        --run-id semiconductors_theme_discovery_refined_30d_execute \
        --output theme_review_worksheet.csv
"""

from __future__ import annotations

import argparse

from kinetic.research.reports.theme_review import (
    build_review_rows,
    read_discovery_csv,
    write_review_worksheet,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Discovery artifact CSV path.")
    parser.add_argument("--run-id", required=True, help="Source discovery run id (lineage).")
    parser.add_argument("--output", required=True, help="Review worksheet CSV to write.")
    args = parser.parse_args()

    candidates = read_discovery_csv(args.input)
    rows = build_review_rows(candidates, source_run_id=args.run_id)
    write_review_worksheet(rows, args.output)
    rejected = sum(1 for r in rows if r["keep_or_reject"] == "reject")
    print(
        f"Wrote {len(rows)} candidate(s) to {args.output} "
        f"({rejected} pre-filled as reject). Review each and populate the bundle "
        "manually — this tool never edits the bundle."
    )


if __name__ == "__main__":
    main()

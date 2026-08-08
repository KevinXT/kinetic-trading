"""
Optional: export real BigQuery reporting-view results to local JSON files.

This is a convenience tool for writing reporting-view result arrays to a local
directory for inspection or BI tooling. It does not serve a web dashboard.

It reuses the same cost-aware path as everything else (``SafeBigQueryClient``,
SQL guardrails, cost policy + ledger) and is **dry-run by default**: it estimates
cost and writes nothing unless you pass ``--execute`` together with the typed
confirmation (``reporting.cost_controls.execute_query: "ENABLE"`` or ``--yes``).

Usage::

    # Estimate only (writes nothing):
    python -m kinetic.ingestion.warehouse.bigquery.reporting.export_dashboard_data --dry-run

    # Fetch real rows and write JSON files under the ignored local export dir:
    python -m kinetic.ingestion.warehouse.bigquery.reporting.export_dashboard_data --execute --yes

Each view is written as ``<out_dir>/<view_name>.json`` -- a JSON array of row
objects whose keys are exactly the reporting-view output aliases.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from kinetic.ingestion.cost.ledger import CostLedger
from kinetic.ingestion.cost.policy import load_cost_policy
from kinetic.core.errors import TradingSystemError

from kinetic.ingestion.warehouse.bigquery.client import SafeBigQueryClient

from .build_views import resolve_settings
from .views import get_reporting_views, render_view_sql

_DEFAULT_CONFIG_PATH = "configs/reporting.yaml"
_DEFAULT_OUT_DIR = "data/local_only/reporting_exports"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="export-dashboard-data",
        description="Export BigQuery reporting-view results to the dashboard JSON files.",
    )
    parser.add_argument("--config", default=_DEFAULT_CONFIG_PATH, help="Reporting config YAML.")
    parser.add_argument(
        "--out-dir",
        default=_DEFAULT_OUT_DIR,
        help=f"Directory to write <view>.json into. Defaults to {_DEFAULT_OUT_DIR}.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="execute",
        action="store_false",
        help="Estimate only; write nothing (default).",
    )
    mode.add_argument(
        "--execute",
        dest="execute",
        action="store_true",
        help="Run the queries and write the JSON files (requires confirmation).",
    )
    parser.set_defaults(execute=False)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Provide the typed execute confirmation for --execute from the CLI.",
    )
    parser.add_argument("--project", default=None, help="Override BigQuery project id.")
    parser.add_argument("--dataset", default=None, help="Override destination dataset.")
    parser.add_argument("--source-table", default=None, help="Override source GDELT table.")
    parser.add_argument("--lookback-days", type=int, default=None, help="Override rolling window.")
    parser.add_argument("--top-n", type=int, default=None, help="Override top-N row cap.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    try:
        settings = resolve_settings(args)
        policy = load_cost_policy(settings.cost_policy_path)

        execute_query = settings.execute_query
        if args.execute:
            if args.yes:
                execute_query = policy.execute_confirmation_value
            if (
                policy.require_execute_confirmation
                and execute_query != policy.execute_confirmation_value
            ):
                raise TradingSystemError(
                    "refusing to run export without typed confirmation. Set "
                    f"reporting.cost_controls.execute_query to "
                    f"{policy.execute_confirmation_value!r}, or pass --yes."
                )

        ledger = CostLedger(settings.ledger_path)
        client = SafeBigQueryClient(
            project_id=settings.project_id, usd_per_tib=policy.bigquery_usd_per_tib
        )
        out_dir = Path(args.out_dir)
        max_bytes = settings.maximum_bytes_billed or policy.default_max_query_bytes

        written: List[str] = []
        for view in get_reporting_views():
            select_sql = render_view_sql(
                view,
                source_table=settings.source_table,
                lookback_days=settings.lookback_days,
                top_n=settings.top_n,
            )
            result = client.safe_query(
                sql=select_sql,
                query_name=f"dashboard_export:{view.name}",
                topic="reporting",
                cost_policy=policy,
                ledger=ledger,
                allowed_tables=[settings.source_table],
                dry_run=not args.execute,
                maximum_bytes_billed=max_bytes,
                execute_query=execute_query,
                use_cache=False,
            )
            est = result.estimate
            gib = (est.estimated_gib if est else 0.0) or 0.0
            cost = (est.estimated_cost_usd if est else 0.0) or 0.0
            print(f"[{result.decision}] {view.name}: {gib:.3f} GiB (~${cost:.6f})")

            if args.execute and result.has_rows:
                out_dir.mkdir(parents=True, exist_ok=True)
                path = out_dir / f"{view.name}.json"
                path.write_text(json.dumps(result.rows, indent=2, default=str) + "\n", "utf-8")
                written.append(str(path))
    except TradingSystemError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.execute:
        print(f"\nWrote {len(written)} file(s):")
        for p in written:
            print(f"  {p}")
    else:
        print("\nDry-run only; no files written. Pass --execute --yes to write real data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

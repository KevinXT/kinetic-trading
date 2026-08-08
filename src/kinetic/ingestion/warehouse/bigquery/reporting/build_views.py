"""
CLI runner: validate and (optionally) create the BigQuery reporting views.

Usage::

    # Dry-run (default): render + guardrail-validate every view and, when a real
    # project/credentials are configured, take a BigQuery dry-run cost estimate.
    python -m kinetic.ingestion.warehouse.bigquery.reporting.build_views --dry-run

    # Create/replace the views in BigQuery. Requires typed confirmation:
    # reporting.cost_controls.execute_query == cost_policy.execute_confirmation_value
    # ("ENABLE"), or pass --yes.
    python -m kinetic.ingestion.warehouse.bigquery.reporting.build_views --create

Design:

* Reuses the sanctioned ``SafeBigQueryClient`` (dry-run first, guardrails,
  ``maximum_bytes_billed`` always enforced) and the shared cost policy/ledger.
* Defaults to dry-run; nothing is created unless ``--create`` is passed AND the
  typed confirmation is present.
* Each reporting ``SELECT`` is validated with ``validate_bigquery_sql`` before
  any network call, so a bad template fails locally with a clear message.
* BigQuery is injected in tests (``client=...``) so unit tests need no network.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from kinetic.core.config import (
    DEFAULT_LOCAL_CONFIG_PATH,
    apply_local_overrides,
    load_config,
)
from kinetic.ingestion.cost.ledger import CostLedger
from kinetic.ingestion.cost.policy import CostPolicy, load_cost_policy
from kinetic.core.errors import ConfigError, TradingSystemError

from kinetic.ingestion.warehouse.bigquery.client import SafeBigQueryClient
from kinetic.ingestion.warehouse.bigquery.sql_guardrails import validate_bigquery_sql

from .views import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_TOP_N,
    ReportingView,
    build_create_view_statement,
    get_reporting_views,
    qualified_view_id,
    render_view_sql,
)

JsonDict = Dict[str, Any]

_DEFAULT_CONFIG_PATH = "configs/reporting.yaml"
_DEFAULT_COST_POLICY_PATH = "configs/cost_policy.yaml"
_DEFAULT_LEDGER_PATH = "warehouse/cost/cost_ledger.jsonl"
_DEFAULT_SOURCE_TABLE = "gdelt-bq.gdeltv2.gkg_partitioned"


@dataclass
class ViewResult:
    """Outcome of processing one reporting view."""

    name: str
    view_id: str
    validated: bool = False
    estimated_bytes: Optional[int] = None
    estimated_cost_usd: Optional[float] = None
    created: bool = False
    status: str = "ok"
    message: str = ""

    def to_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "view_id": self.view_id,
            "validated": self.validated,
            "estimated_bytes": self.estimated_bytes,
            "estimated_cost_usd": self.estimated_cost_usd,
            "created": self.created,
            "status": self.status,
            "message": self.message,
        }


@dataclass
class ReportingSettings:
    """Resolved reporting settings (from config + CLI overrides)."""

    project_id: str
    dataset: str
    source_table: str = _DEFAULT_SOURCE_TABLE
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    top_n: int = DEFAULT_TOP_N
    maximum_bytes_billed: Optional[int] = None
    execute_query: str = ""
    cost_policy_path: str = _DEFAULT_COST_POLICY_PATH
    ledger_path: str = _DEFAULT_LEDGER_PATH


# ── core (testable; BigQuery injected) ──────────────────────────────────────


def process_view(
    view: ReportingView,
    *,
    settings: ReportingSettings,
    policy: CostPolicy,
    client: SafeBigQueryClient,
    ledger: Optional[CostLedger] = None,
    create: bool = False,
    estimate: bool = True,
) -> ViewResult:
    """
    Validate one view and optionally dry-run estimate / create it.

    Steps: render SELECT -> guardrail-validate -> (optional) BigQuery dry-run
    estimate -> (optional) CREATE OR REPLACE VIEW. Failures are captured on the
    returned :class:`ViewResult` (status="error") rather than raising, so one bad
    view does not abort the whole run.
    """
    view_id = qualified_view_id(settings.project_id, settings.dataset, view.name)
    result = ViewResult(name=view.name, view_id=view_id)

    try:
        select_sql = render_view_sql(
            view,
            source_table=settings.source_table,
            lookback_days=settings.lookback_days,
            top_n=settings.top_n,
        )
        validate_bigquery_sql(select_sql, [settings.source_table])
        result.validated = True
    except (TradingSystemError, ValueError) as exc:
        result.status = "error"
        result.message = f"validation failed: {exc}"
        return result

    max_bytes = settings.maximum_bytes_billed or policy.default_max_query_bytes

    if estimate:
        try:
            est = client.dry_run(select_sql, maximum_bytes_billed=max_bytes)
            result.estimated_bytes = est.total_bytes_processed
            result.estimated_cost_usd = round(est.estimated_cost_usd, 8)
            if ledger is not None:
                ledger.append(
                    {
                        "provider": "bigquery_reporting",
                        "query_name": f"reporting_view:{view.name}",
                        "topic": "reporting",
                        "dry_run": True,
                        "estimated_bytes": est.total_bytes_processed,
                        "estimated_cost_usd": round(est.estimated_cost_usd, 8),
                        "maximum_bytes_billed": max_bytes,
                        "decision": "DRY_RUN_ONLY",
                        "cache_hit": False,
                        "status": "success",
                        "message": f"reporting view dry-run: {view.name}",
                    }
                )
        except TradingSystemError as exc:
            result.status = "error"
            result.message = f"dry-run estimate failed: {exc}"
            return result

    if create:
        try:
            create_sql = build_create_view_statement(view_id, select_sql)
            client.run_query(create_sql, maximum_bytes_billed=max_bytes)
            result.created = True
            result.message = f"created/replaced view {view_id}"
        except TradingSystemError as exc:
            result.status = "error"
            result.message = f"create failed: {exc}"
            return result

    return result


def build_report(
    *,
    settings: ReportingSettings,
    policy: CostPolicy,
    client: SafeBigQueryClient,
    ledger: Optional[CostLedger] = None,
    create: bool = False,
    estimate: bool = True,
    views: Optional[Sequence[ReportingView]] = None,
) -> List[ViewResult]:
    """Process every reporting view and return the per-view results."""
    selected = list(views) if views is not None else get_reporting_views()
    return [
        process_view(
            v,
            settings=settings,
            policy=policy,
            client=client,
            ledger=ledger,
            create=create,
            estimate=estimate,
        )
        for v in selected
    ]


def format_report(results: Sequence[ViewResult], *, create: bool) -> str:
    """Render a human-readable per-view summary."""
    lines: List[str] = []
    header = "Creating reporting views" if create else "Validating reporting views (dry-run)"
    lines.append(header)
    lines.append("=" * len(header))
    for r in results:
        icon = "OK " if r.status == "ok" else "ERR"
        lines.append(f"[{icon}] {r.name} -> {r.view_id}")
        if r.estimated_bytes is not None:
            gib = r.estimated_bytes / (1024**3)
            lines.append(f"       dry-run estimate: {gib:.3f} GiB (~${r.estimated_cost_usd:.6f})")
        if r.created:
            lines.append("       created/replaced view")
        if r.message:
            lines.append(f"       {r.message}")
    ok = sum(1 for r in results if r.status == "ok")
    lines.append("")
    lines.append(f"{ok}/{len(results)} views ok" + (", created." if create else "."))
    return "\n".join(lines)


# ── config resolution + CLI ─────────────────────────────────────────────────


def resolve_settings(
    args: argparse.Namespace,
    *,
    local_path: str | Path = DEFAULT_LOCAL_CONFIG_PATH,
) -> ReportingSettings:
    """
    Merge config file (+ optional local overrides) with CLI flags into settings.

    ``configs/local.yaml`` is deep-merged over the config the same way the
    pipeline runner does it, so a real ``providers.bigquery.project_id`` can live
    outside the committed configs. Tests pass a non-existent ``local_path`` to
    keep resolution independent of the developer's local overrides.
    """
    cfg: JsonDict = {}
    config_path = Path(args.config)
    if config_path.is_file():
        cfg = apply_local_overrides(load_config(config_path), local_path=local_path)
    elif args.config != _DEFAULT_CONFIG_PATH:
        raise ConfigError(f"reporting config not found: {config_path}")

    bq = (cfg.get("providers", {}) or {}).get("bigquery", {}) or {}
    reporting = cfg.get("reporting", {}) or {}
    cost_controls = reporting.get("cost_controls", {}) or {}

    project_id = args.project or bq.get("project_id") or ""
    dataset = args.dataset or reporting.get("dataset") or ""
    source_table = args.source_table or bq.get("table") or _DEFAULT_SOURCE_TABLE

    lookback_days = args.lookback_days or int(reporting.get("lookback_days", DEFAULT_LOOKBACK_DAYS))
    top_n = args.top_n or int(reporting.get("top_n", DEFAULT_TOP_N))

    max_bytes = cost_controls.get("maximum_bytes_billed")
    max_bytes = int(max_bytes) if max_bytes is not None else None

    if not project_id or project_id == "YOUR_PROJECT_ID":
        raise ConfigError(
            "reporting requires a real BigQuery project id. Set "
            "providers.bigquery.project_id in the config (or configs/local.yaml), "
            "or pass --project."
        )
    if not dataset:
        raise ConfigError(
            "reporting requires a destination dataset. Set reporting.dataset in the "
            "config, or pass --dataset."
        )

    return ReportingSettings(
        project_id=project_id,
        dataset=dataset,
        source_table=source_table,
        lookback_days=lookback_days,
        top_n=top_n,
        maximum_bytes_billed=max_bytes,
        execute_query=str(cost_controls.get("execute_query", "")),
        cost_policy_path=cost_controls.get("cost_policy_path", _DEFAULT_COST_POLICY_PATH),
        ledger_path=cost_controls.get("ledger_path", _DEFAULT_LEDGER_PATH),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build-views",
        description="Validate and optionally create BigQuery reporting views for Looker Studio.",
    )
    parser.add_argument(
        "--config",
        default=_DEFAULT_CONFIG_PATH,
        help=f"Reporting config YAML. Defaults to {_DEFAULT_CONFIG_PATH}.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="create",
        action="store_false",
        help="Validate + dry-run estimate only (default). Creates nothing.",
    )
    mode.add_argument(
        "--create",
        dest="create",
        action="store_true",
        help="Create/replace the reporting views in BigQuery (requires confirmation).",
    )
    parser.set_defaults(create=False)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Provide the typed execute confirmation for --create from the CLI.",
    )
    parser.add_argument(
        "--no-estimate",
        dest="estimate",
        action="store_false",
        help="Skip the BigQuery dry-run estimate (local render + guardrail validation only).",
    )
    parser.set_defaults(estimate=True)
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

        if args.create:
            confirmed = args.yes or (
                policy.require_execute_confirmation
                and settings.execute_query == policy.execute_confirmation_value
            )
            if policy.require_execute_confirmation and not confirmed:
                raise ConfigError(
                    "refusing to create views without typed confirmation. Set "
                    f"reporting.cost_controls.execute_query to "
                    f"{policy.execute_confirmation_value!r}, or pass --yes."
                )

        ledger = CostLedger(settings.ledger_path)
        client = SafeBigQueryClient(
            project_id=settings.project_id, usd_per_tib=policy.bigquery_usd_per_tib
        )
        results = build_report(
            settings=settings,
            policy=policy,
            client=client,
            ledger=ledger,
            create=args.create,
            estimate=args.estimate,
        )
    except TradingSystemError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(format_report(results, create=args.create))
    return 0 if all(r.status == "ok" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

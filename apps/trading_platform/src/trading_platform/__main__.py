from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common.cost.ledger import CostLedger
from common.cost.policy import load_cost_policy
from common.cost.report import format_cost_report
from common.errors import TradingSystemError
from pipeline_core.engine.runner import run_plan_from_file

_DEFAULT_LEDGER_PATH = "data/cost/cost_ledger.jsonl"
_DEFAULT_POLICY_PATH = "configs/cost_policy.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-platform",
        description="Run a Kinetic Trading YAML pipeline plan.",
    )
    parser.add_argument(
        "config",
        type=Path,
        help="Path to a YAML pipeline config, for example configs/demo.yaml.",
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=Path("experiments"),
        help="Directory where run outputs are written. Defaults to experiments/.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional fixed run id. Useful for tests or repeatable demos.",
    )
    return parser


def build_cost_report_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-platform cost-report",
        description="Summarize estimated cloud query spend from the cost ledger.",
    )
    parser.add_argument(
        "--ledger-path",
        default=_DEFAULT_LEDGER_PATH,
        help=f"Path to the cost ledger JSONL. Defaults to {_DEFAULT_LEDGER_PATH}.",
    )
    parser.add_argument(
        "--policy-path",
        default=_DEFAULT_POLICY_PATH,
        help=f"Path to the cost policy YAML. Defaults to {_DEFAULT_POLICY_PATH}.",
    )
    return parser


def run_cost_report(argv: list[str]) -> None:
    parser = build_cost_report_parser()
    args = parser.parse_args(argv)
    try:
        policy = load_cost_policy(args.policy_path)
    except TradingSystemError as e:
        parser.exit(status=1, message=f"error: {e}\n")
    ledger = CostLedger(args.ledger_path)
    summary = ledger.summarize(policy)
    print(format_cost_report(summary))


def main() -> None:
    argv = sys.argv[1:]

    # Subcommand: cost-report. Kept separate so the existing positional config
    # form (`trading-platform configs/demo.yaml`) is unchanged.
    if argv and argv[0] == "cost-report":
        run_cost_report(argv[1:])
        return

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        ctx = run_plan_from_file(
            args.config,
            runs_root=args.runs_root,
            run_id=args.run_id,
        )
    except TradingSystemError as e:
        parser.exit(status=1, message=f"error: {e}\n")

    print(f"completed run: {ctx.run_name}")
    print(f"run_id: {ctx.run_id}")
    print(f"outputs: {ctx.run_dir}")


if __name__ == "__main__":
    main()

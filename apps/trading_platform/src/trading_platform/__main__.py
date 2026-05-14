from __future__ import annotations

import argparse
from pathlib import Path

from common.errors import TradingSystemError
from pipeline_core.engine.runner import run_plan_from_file


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


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

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

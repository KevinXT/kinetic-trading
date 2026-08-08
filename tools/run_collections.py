"""Run every YAML collection config under a root directory, one after another.

A supporting tool, not part of pipeline execution: it shells out to the
``kinetic`` CLI once per config so a failing collection cannot take the rest of
the batch down with it, and throttles between runs to stay inside GDELT's rate
limits.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

# ── discovery ────────────────────────────────────────────────────────────


def discover_configs(root: Path) -> list[Path]:
    """Recursively find all .yaml files under *root*, sorted for determinism."""
    return sorted(root.rglob("*.yaml"))


# ── execution ────────────────────────────────────────────────────────────


def run_config(config_path: Path) -> subprocess.CompletedProcess[str]:
    """Run a single config through the kinetic CLI."""
    return subprocess.run(
        [sys.executable, "-m", "kinetic.interface.cli.app", "run", str(config_path)],
        capture_output=True,
        text=True,
    )


def run_all(
    configs: list[Path],
    *,
    sleep_seconds: float = 6.0,
    max_configs: int | None = None,
) -> dict[str, object]:
    """Execute *configs* sequentially with throttling and failure tolerance.

    Returns a summary dict with counts and timing.
    """
    if max_configs is not None:
        configs = configs[:max_configs]

    total = len(configs)
    succeeded: list[Path] = []
    failed: list[Path] = []
    wall_start = time.monotonic()

    for idx, cfg in enumerate(configs, start=1):
        label = str(cfg)
        print(f"[{idx}/{total}] Running {label}")

        t0 = time.monotonic()
        result = run_config(cfg)
        elapsed = time.monotonic() - t0

        if result.returncode == 0:
            print(f"  Completed in {elapsed:.2f}s")
            succeeded.append(cfg)
        else:
            print(f"  FAILED ({label}) in {elapsed:.2f}s")
            stderr = result.stderr.strip()
            if stderr:
                print(f"  stderr: {stderr}")
            failed.append(cfg)

        if idx < total and sleep_seconds > 0:
            print(f"  Sleeping {sleep_seconds}s...")
            time.sleep(sleep_seconds)

    wall_elapsed = time.monotonic() - wall_start

    summary = {
        "total": total,
        "succeeded": len(succeeded),
        "failed": len(failed),
        "failed_configs": [str(p) for p in failed],
        "elapsed_s": round(wall_elapsed, 2),
    }

    print()
    print("─── summary ───")
    print(f"  total:     {summary['total']}")
    print(f"  succeeded: {summary['succeeded']}")
    print(f"  failed:    {summary['failed']}")
    print(f"  elapsed:   {summary['elapsed_s']}s")
    if failed:
        print("  failed configs:")
        for p in failed:
            print(f"    - {p}")

    return summary


# ── cli ──────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run YAML collection configs through the trading platform pipeline.",
    )
    parser.add_argument(
        "--collections-root",
        type=Path,
        default=Path("configs/collections"),
        help="Root directory to scan for .yaml configs (default: configs/collections).",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=6.0,
        help="Seconds to wait between config runs (default: 6).",
    )
    parser.add_argument(
        "--max-configs",
        type=int,
        default=None,
        help="Stop after running this many configs.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    root: Path = args.collections_root
    if not root.is_dir():
        parser.exit(status=1, message=f"error: {root} is not a directory\n")

    configs = discover_configs(root)
    if not configs:
        parser.exit(status=1, message=f"error: no .yaml files found under {root}\n")

    print(f"Discovered {len(configs)} config(s) under {root}\n")
    run_all(configs, sleep_seconds=args.sleep_seconds, max_configs=args.max_configs)


if __name__ == "__main__":
    main()

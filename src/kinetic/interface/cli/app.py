"""The ``kinetic`` command-line interface.

Every command here does three things and no more: parse arguments, call one
service, format the result. Domain logic lives in the subsystem that owns it —
this module must never fetch from a provider, compute a feature, evaluate a
model, or place an order.

A future ``kinetic terminal`` will call exactly these services. That is the point
of keeping the commands this thin: the interactive UI is a second front end onto
:func:`kinetic.bootstrap.build_default_registry`,
:func:`kinetic.core.config.load_runtime_config` and
:func:`kinetic.core.pipeline.runner.run_pipeline`, not a rewrite of them.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer

from kinetic import __version__
from kinetic.core.errors import TradingSystemError
from kinetic.core.pipeline.runner import DEFAULT_RUNS_ROOT

app = typer.Typer(
    name="kinetic",
    help="Kinetic Trading — market-intelligence and trading-research platform.",
    no_args_is_help=True,
    add_completion=False,
)

pipeline_app = typer.Typer(help="Run and inspect pipelines.", no_args_is_help=True)
task_app = typer.Typer(help="Inspect the task registry.", no_args_is_help=True)
config_app = typer.Typer(help="Validate and migrate pipeline configs.", no_args_is_help=True)
cost_app = typer.Typer(help="Inspect estimated cloud spend.", no_args_is_help=True)

app.add_typer(pipeline_app, name="pipeline")
app.add_typer(task_app, name="task")
app.add_typer(config_app, name="config")
app.add_typer(cost_app, name="cost")

DEFAULT_LEDGER_PATH = "warehouse/cost/cost_ledger.jsonl"
DEFAULT_POLICY_PATH = "configs/cost_policy.yaml"


def _fail(message: str) -> None:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


# invoke_without_command lets `kinetic --version` exit here rather than tripping
# the group's "Missing command" error. Bare `kinetic` still prints help, because
# the Typer app sets no_args_is_help.
@app.callback(invoke_without_command=True)
def _root(
    version: bool = typer.Option(
        False, "--version", help="Show the installed version and exit.", is_eager=True
    ),
) -> None:
    if version:
        typer.echo(f"kinetic {__version__}")
        raise typer.Exit()


def _run_pipeline(config: Path, runs_root: Path, run_id: Optional[str]) -> None:
    from kinetic.bootstrap import build_default_registry
    from kinetic.core.pipeline.runner import run_pipeline_from_file

    if not config.exists():
        _fail(f"config not found: {config}")

    try:
        ctx = run_pipeline_from_file(
            config,
            registry=build_default_registry(),
            runs_root=runs_root,
            run_id=run_id,
        )
    except TradingSystemError as e:
        _fail(str(e))
        return

    typer.echo(f"completed run: {ctx.run_name}")
    typer.echo(f"run_id: {ctx.run_id}")
    typer.echo(f"outputs: {ctx.run_dir}")


@app.command("run")
def run(
    config: Path = typer.Argument(..., help="Path to a YAML pipeline config."),
    runs_root: Path = typer.Option(
        Path(DEFAULT_RUNS_ROOT), "--runs-root", help="Where run outputs are written."
    ),
    run_id: Optional[str] = typer.Option(
        None, "--run-id", help="Fixed run id. Use for byte-stable reruns of offline pipelines."
    ),
) -> None:
    """Run a pipeline config. Shorthand for ``kinetic pipeline run``."""
    _run_pipeline(config, runs_root, run_id)


@pipeline_app.command("run")
def pipeline_run(
    config: Path = typer.Argument(..., help="Path to a YAML pipeline config."),
    runs_root: Path = typer.Option(
        Path(DEFAULT_RUNS_ROOT), "--runs-root", help="Where run outputs are written."
    ),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Fixed run id."),
) -> None:
    """Run a pipeline config."""
    _run_pipeline(config, runs_root, run_id)


@task_app.command("list")
def task_list(
    show_deprecated: bool = typer.Option(
        False, "--show-deprecated", help="Also list deprecated task-name aliases."
    ),
) -> None:
    """List every task identifier the default registry provides."""
    from kinetic.bootstrap import build_default_registry

    registry = build_default_registry()
    for task_id in registry.task_ids():
        typer.echo(task_id)

    if show_deprecated:
        aliases = registry.deprecated_aliases()
        if aliases:
            typer.echo("")
            typer.echo("deprecated aliases:")
            for alias in sorted(aliases):
                typer.echo(f"  {alias} -> {aliases[alias]}")


@config_app.command("validate")
def config_validate(
    config: Path = typer.Argument(..., help="Path to a YAML pipeline config."),
    strict_task_names: bool = typer.Option(
        False,
        "--strict-task-names",
        help="Fail when the config uses a deprecated task name.",
    ),
) -> None:
    """Check that a config parses, and that every task it names is registered."""
    from kinetic.bootstrap import build_default_registry
    from kinetic.core.config import load_runtime_config
    from kinetic.core.pipeline.plan import parse_plan

    if not config.exists():
        _fail(f"config not found: {config}")

    try:
        cfg = load_runtime_config(config)
        plan = parse_plan(cfg, source=config)
    except TradingSystemError as e:
        _fail(str(e))
        return

    registry = build_default_registry(include_legacy_aliases=not strict_task_names)
    problems: list[str] = []
    deprecated: list[tuple[str, str]] = []
    for index, step in enumerate(plan.steps):
        target = registry.alias_target(step.task)
        if target is not None:
            deprecated.append((step.task, target))
        if step.task not in registry:
            problems.append(f"  step {index + 1}: unknown task {step.task!r}")

    for old, current in deprecated:
        typer.secho(
            f"warning: task name {old!r} is deprecated; use {current!r}.",
            fg=typer.colors.YELLOW,
            err=True,
        )

    if problems:
        typer.secho(f"error: {config}: unknown task(s)", fg=typer.colors.RED, err=True)
        for problem in problems:
            typer.secho(problem, fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.echo(f"{config}: valid — {len(plan)} step(s)")
    for index, step in enumerate(plan.steps):
        typer.echo(f"  {index + 1}. {registry.canonical_id(step.task)}")


@config_app.command("migrate")
def config_migrate(
    config: Path = typer.Argument(..., help="A config using the legacy pipeline shape."),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write here instead of stdout."
    ),
) -> None:
    """Rewrite a legacy ``ingest``/``transform``/``strategy`` config into ``pipeline.steps``."""
    import yaml

    from kinetic.bootstrap import LEGACY_TASK_ALIASES
    from kinetic.core.config import load_yaml
    from kinetic.core.pipeline.legacy_plan import to_current_shape

    if not config.exists():
        _fail(f"config not found: {config}")

    try:
        migrated = to_current_shape(load_yaml(config), source=config)
    except TradingSystemError as e:
        _fail(str(e))
        return

    for step in migrated["pipeline"]["steps"]:
        step["task"] = LEGACY_TASK_ALIASES.get(step["task"], step["task"])

    text = yaml.safe_dump(migrated, sort_keys=False, allow_unicode=True)
    if output is None:
        typer.echo(text)
    else:
        output.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {output}")


@cost_app.command("report")
def cost_report(
    ledger_path: Path = typer.Option(
        Path(DEFAULT_LEDGER_PATH), "--ledger-path", help="Path to the cost ledger JSONL."
    ),
    policy_path: Path = typer.Option(
        Path(DEFAULT_POLICY_PATH), "--policy-path", help="Path to the cost policy YAML."
    ),
) -> None:
    """Summarize estimated cloud query spend from the cost ledger."""
    from kinetic.ingestion.cost.ledger import CostLedger
    from kinetic.ingestion.cost.policy import load_cost_policy
    from kinetic.ingestion.cost.report import format_cost_report

    try:
        policy = load_cost_policy(str(policy_path))
    except TradingSystemError as e:
        _fail(str(e))
        return

    ledger = CostLedger(str(ledger_path))
    typer.echo(format_cost_report(ledger.summarize(policy)))


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    sys.exit(app())

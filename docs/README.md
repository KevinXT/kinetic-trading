# Documentation

The root [README](../README.md) covers what Kinetic Trading is, what works, and
how to install and run it. Everything below goes deeper.

## Start here

| Doc | Purpose |
| --- | --- |
| [Platform overview](architecture/platform-overview.md) | The eight subsystems and what each one is for. Read this first |
| [Execution flow](architecture/execution-flow.md) | One real pipeline traced from the command line to artifacts on disk |
| [Development](getting-started/development.md) | Setup, validation, tests, and where new code goes |

## Architecture

| Doc | Purpose |
| --- | --- |
| [Platform overview](architecture/platform-overview.md) | Subsystem map; what works, what is experimental, what does not exist |
| [Target structure](architecture/target-structure.md) | The directory layout, and what is deliberately absent |
| [Dependency rules](architecture/dependency-rules.md) | The layering, why each rule exists, and what the automated checks cannot express |
| [Data lifecycle](architecture/data-lifecycle.md) | raw → normalized → curated → features → predictions, and the point-in-time rules |
| [Execution flow](architecture/execution-flow.md) | CLI → config → bootstrap → registry → runner → task → artifacts |
| [Migration map](architecture/migration-map.md) | Where every pre-0.2 module went, and why |
| [Refactor report](architecture/refactor-report.md) | What the 0.2 consolidation changed, measured |
| [Refactor progress](architecture/refactor-progress.md) | Phase-by-phase status, including the session history behind this attempt |

## Getting started

| Doc | Purpose |
| --- | --- |
| [Development](getting-started/development.md) | Setup, `make validate`, test layout, adding a task or config |
| [Adding a provider](getting-started/adding-a-provider.md) | Exactly which files a new provider needs, worked through FRED |

## Concepts

| Doc | Purpose |
| --- | --- |
| [Configuration](concepts/configuration.md) | YAML `include:` chains, deep merge, local overrides |
| [Run context](concepts/run-context.md) | `RunContext`, the `ctx.state` contract, artifact layout |
| [Financial data](concepts/financial-data.md) | Provider-neutral contracts, the Alpaca path, instrument identity, caching, storage |
| [News × market dataset](concepts/news-market-dataset.md) | The research question, alignment policies, leakage design, artifacts and limitations |

## Reference

| Doc | Purpose |
| --- | --- |
| [Dependencies](reference/dependencies.md) | What is declared and why; the optional extras |
| [Compatibility](reference/compatibility.md) | Deprecated task names and config shape, with removal dates |
| [Looker Studio setup](reference/looker-studio-dashboard.md) | BigQuery reporting views and connecting them to Looker Studio |

## Case studies

| Doc | Purpose |
| --- | --- |
| [Semiconductor case study](../projects/semiconductor_case_study/README.md) | What was tried, what failed, what was built — including a negative result that stands |

## Development notes

| Doc | Purpose |
| --- | --- |
| [TODO](development/TODO.md) | Implementation checklist |
| [Production hardening](development/production-hardening.md) | The hygiene foundation and its deferred follow-ons |
| [Product vision](product/strategy-copilot-vision.md) | Long-term direction — aspirational, not a description of current code |
| [Notes](notes/README.md) | Scratch notes |

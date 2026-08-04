# Documentation

Product notes, development checklists, and technical guides for kinetic-trading.

| Doc | Purpose | Status |
|-----|---------|--------|
| [Product vision](product/strategy-copilot-vision.md) | Strategy Copilot — principles, roadmap, architecture intent | aspirational |
| [Development TODO](development/TODO.md) | Implementation checklist (engine, tasks, configs) | active |
| [Production hardening](development/production-hardening.md) | Merged hygiene foundation (PR #4) and deferred follow-ons | current |
| [Config builder](guides/config-builder.md) | YAML `include:` / merge behavior (`common.config_builder`) | current |
| [Looker Studio setup](looker_studio_dashboard.md) | BI reporting views + how to connect them to Looker Studio | current |
| [Run context](pipeline/run-context.md) | `RunContext`, state contract, and artifact layout | current |
| [Financial data architecture](architecture/financial-data.md) | Provider-neutral contracts, Alpaca path, identity, cache, and storage | current |
| [News×market dataset design](research/news_market_dataset_design.md) | Research question, alignment, hypotheses, feature/leakage design, artifacts, and limitations | current |
| [Semiconductor theme scoring](research/semiconductor-theme-scoring/) | Curated 30-day BigQuery scoring snapshot and negative identity-layer verdict | current |
| [Dependencies](reference/dependencies.md) | Per-package dependency declarations | current |
| [Notes](notes/README.md) | Scratch / informal notes | — |

The root [README.md](../README.md) covers setup, project status, and repo layout.

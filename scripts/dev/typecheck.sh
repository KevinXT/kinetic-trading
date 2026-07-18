#!/usr/bin/env bash
# Scoped mypy — keep in sync with .github/workflows/ci.yml.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

exec mypy \
  packages/common/src \
  packages/market_data/src \
  packages/pipeline_core/src \
  packages/research_data/src \
  packages/news_data/src/news_data/article \
  packages/news_data/src/news_data/entity \
  packages/news_data/src/news_data/dedupe \
  apps/relevance_annotation_ui/app_config.py \
  apps/relevance_annotation_ui/annotation_store.py \
  apps/relevance_annotation_ui/view_models.py \
  apps/relevance_annotation_ui/pilot_service.py

#!/usr/bin/env bash
# Scoped Black check — keep in sync with .github/workflows/ci.yml.
# Full-tree black is not yet clean; only check the market-data/research scopes.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

exec black --check \
  packages/market_data \
  packages/research_data \
  packages/common/src/common/cache.py \
  tests/test_alpaca_cache.py \
  tests/test_alpaca_client.py \
  tests/test_alpaca_config.py \
  tests/test_alpaca_normalize.py \
  tests/test_alpaca_registration.py \
  tests/test_alpaca_task.py \
  tests/test_financial_jsonl_store.py \
  tests/test_market_data_models.py \
  tests/test_imports.py \
  tests/test_research_stats.py \
  tests/test_research_calendar.py \
  tests/test_research_alignment.py \
  tests/test_research_models.py \
  tests/test_research_catalog.py \
  tests/test_research_mappings.py \
  tests/test_research_news_features.py \
  tests/test_research_market_features.py \
  tests/test_research_join_leakage.py \
  tests/test_research_event_study.py \
  tests/test_research_manifest.py \
  tests/test_research_task_integration.py \
  packages/news_data/src/news_data/article \
  packages/news_data/src/news_data/entity \
  packages/news_data/src/news_data/dedupe \
  tests/test_relevance_article_normalize.py \
  tests/test_relevance_entity_matching.py \
  tests/test_relevance_dedupe.py \
  tests/test_relevance_annotation_metrics.py \
  tests/test_relevance_splits_sampling.py \
  tests/test_relevance_benchmark_integration.py \
  tests/test_pilot_planning.py \
  tests/test_pilot_rights_eligibility_sampling.py \
  tests/test_pilot_agreement_duplicates.py \
  tests/test_pilot_integration.py \
  packages/research_data/src/research_data/real_corpus_pilot_task.py \
  apps/relevance_annotation_ui \
  tests/test_relevance_annotation_ui_store.py \
  tests/test_relevance_annotation_ui_service.py \
  tests/test_relevance_annotation_ui_integration.py \
  tests/test_relevance_annotation_ui_apptest.py

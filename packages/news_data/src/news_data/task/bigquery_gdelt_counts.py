"""
Pipeline task: cost-aware historical GDELT daily counts via BigQuery.

This task is the only place the pipeline reaches BigQuery, and it never does so
directly — every query goes through :class:`SafeBigQueryClient`. By default it
is a *dry run*: it estimates cost, writes artifacts, logs a ledger decision, and
fetches no data. Real execution requires a typed confirmation in the config.

YAML shape::

    providers:
      bigquery:
        project_id: "YOUR_PROJECT_ID"
        table: "gdelt-bq.gdeltv2.gkg_partitioned"

    pipeline:
      ingest:
        source: bigquery_gdelt_counts
        query_name: inflation_rates_bigquery_30d
        topic: inflation_rates
        query_terms: ["Federal Reserve", "inflation", "interest rates"]
        # OR use a named bundle of GDELT theme codes (see
        # configs/gdelt_theme_bundles.yaml). If both query_terms and theme_bundle
        # are set they are merged (de-duplicated, query_terms first).
        # theme_bundle: inflation_rates
        search_columns: ["V2Themes"]   # optional; defaults to ["V2Themes"]
        partition_filter:               # optional; prunes partitions to cut cost
          enabled: true
          column: "_PARTITIONTIME"
          type: "timestamp"
        window: {preset: last_30_days, bucket: 1d}
        cost_controls:
          dry_run: true
          maximum_bytes_billed: 1000000000
          execute_query: ""
          cost_policy_path: "configs/cost_policy.yaml"
          ledger_path: "data/cost/cost_ledger.jsonl"
          use_cache: true
"""

from __future__ import annotations

import csv
from typing import Any, Dict, List

from common.cost.ledger import CostLedger
from common.cost.policy import load_cost_policy
from common.date_windows import resolve_date_window, to_yyyymmdd
from common.errors import ConfigError, CostGuardrailError, PipelineError

from news_data.bigquery.cache import bigquery_cache_path, build_cache_key
from news_data.bigquery.client import SafeBigQueryClient
from news_data.bigquery.gdelt_queries import (
    DEFAULT_SEARCH_COLUMNS,
    QUERY_BUILDER_VERSION,
    build_gdelt_daily_counts_query,
)
from news_data.bigquery.normalize_counts import normalize_gdelt_daily_counts
from news_data.bigquery.theme_bundles import DEFAULT_THEME_BUNDLES_PATH, resolve_query_terms

JsonDict = Dict[str, Any]

PROVIDER = "bigquery_gdelt"
_DEFAULT_TABLE = "gdelt-bq.gdeltv2.gkg_partitioned"
_DEFAULT_COST_POLICY_PATH = "configs/cost_policy.yaml"
_DEFAULT_LEDGER_PATH = "data/cost/cost_ledger.jsonl"

# Artifact filenames (kept stable for tooling / docs).
ART_SQL = "bigquery_sql.sql"
ART_ESTIMATE = "bigquery_dry_run_estimate.json"
ART_DECISION = "bigquery_cost_decision.json"
ART_SUMMARY = "bigquery_summary.json"
ART_ROWS_JSONL = "bigquery_daily_counts.jsonl"
ART_ROWS_CSV = "bigquery_daily_counts.csv"


def bigquery_gdelt_counts_task(ctx, params: JsonDict) -> None:
    bq_cfg = (ctx.cfg.get("providers", {}) or {}).get("bigquery", {}) or {}
    project_id = bq_cfg.get("project_id")
    table = bq_cfg.get("table") or _DEFAULT_TABLE

    topic = str(params.get("topic", "")).strip()
    if not topic:
        raise PipelineError("bigquery_gdelt_counts requires a non-empty 'topic'.")
    query_name = str(params.get("query_name") or topic).strip()

    # Resolve query terms from explicit query_terms and/or a named theme_bundle.
    # - query_terms only: existing behavior.
    # - theme_bundle only: the bundle's themes become the query terms.
    # - both: merged (de-duplicated, query_terms first).
    theme_bundle = params.get("theme_bundle")
    bundles_path = params.get("theme_bundles_path", DEFAULT_THEME_BUNDLES_PATH)
    try:
        query_terms: List[str] = resolve_query_terms(
            query_terms=params.get("query_terms"),
            theme_bundle=str(theme_bundle).strip() if theme_bundle else None,
            bundles_path=bundles_path,
        )
    except ConfigError as err:
        raise PipelineError(str(err)) from err
    if not query_terms:
        raise PipelineError(
            "bigquery_gdelt_counts requires a non-empty 'query_terms' list or a 'theme_bundle'."
        )

    # Default to the compact V2Themes column to keep bytes scanned low. Broad
    # free-text columns (e.g. AllNames) must be opted into explicitly in config.
    search_columns: List[str] = [
        str(c).strip()
        for c in (params.get("search_columns") or DEFAULT_SEARCH_COLUMNS)
        if str(c).strip()
    ]
    if not search_columns:
        raise PipelineError("bigquery_gdelt_counts requires a non-empty 'search_columns' list.")

    # Optional partition pruning. The DATE column on gkg_partitioned does not
    # prune partitions, so constraining the partition pseudo-column is what keeps
    # bytes scanned (and cost) bounded for wide date ranges.
    partition_filter: JsonDict = params.get("partition_filter", {}) or {}

    window = params.get("window", {}) or {}
    bucket = str(window.get("bucket", "1d"))
    if bucket != "1d":
        raise PipelineError(
            f"bigquery_gdelt_counts v0.1 only supports bucket '1d', got {bucket!r}."
        )

    cost_controls: JsonDict = params.get("cost_controls", {}) or {}
    cost_policy_path = cost_controls.get("cost_policy_path", _DEFAULT_COST_POLICY_PATH)
    ledger_path = cost_controls.get("ledger_path", _DEFAULT_LEDGER_PATH)
    policy = load_cost_policy(cost_policy_path)
    ledger = CostLedger(ledger_path)

    dry_run = bool(cost_controls.get("dry_run", True))
    execute_query = str(cost_controls.get("execute_query", ""))
    use_cache = bool(cost_controls.get("use_cache", True))
    maximum_bytes_billed = int(
        cost_controls.get("maximum_bytes_billed", policy.default_max_query_bytes)
    )

    # 1-2. Resolve window and convert to YYYYMMDD for the GDELT DATE field.
    start_date, end_date = resolve_date_window(window)
    start_str = to_yyyymmdd(start_date)
    end_str = to_yyyymmdd(end_date)

    # 3. Build SQL.
    sql = build_gdelt_daily_counts_query(
        table=table,
        start_date=start_str,
        end_date=end_str,
        query_terms=query_terms,
        topic=topic,
        search_columns=search_columns,
        partition_filter=partition_filter,
    )
    # Always persist the SQL artifact first (even if execution is blocked).
    (ctx.artifacts_dir / ART_SQL).write_text(sql + "\n", encoding="utf-8")

    # 4. Stable cache key.
    cache_key = build_cache_key(
        provider=PROVIDER,
        table=table,
        topic=topic,
        query_terms=query_terms,
        start_date=start_str,
        end_date=end_str,
        builder_version=QUERY_BUILDER_VERSION,
        search_columns=search_columns,
        partition_filter=partition_filter,
    )
    cache_path = bigquery_cache_path(cache_key)

    client = SafeBigQueryClient(project_id=project_id, usd_per_tib=policy.bigquery_usd_per_tib)

    # 5. Cost-guarded execution.
    try:
        result = client.safe_query(
            sql=sql,
            query_name=query_name,
            topic=topic,
            cost_policy=policy,
            cost_controls=cost_controls,
            project_id=project_id,
            cache_key=cache_key,
            cache_path=cache_path,
            ledger=ledger,
            allowed_tables=[table],
            dry_run=dry_run,
            maximum_bytes_billed=maximum_bytes_billed,
            execute_query=execute_query,
            use_cache=use_cache,
        )
    except CostGuardrailError as err:
        # Write decision/estimate artifacts even when blocked, then re-raise.
        decision = getattr(err, "decision", "BLOCKED")
        estimate = getattr(err, "estimate", None)
        _write_estimate_artifact(ctx, estimate, maximum_bytes_billed)
        _write_decision_artifact(
            ctx,
            decision=decision,
            cache_hit=False,
            message=str(err),
            maximum_bytes_billed=maximum_bytes_billed,
        )
        ctx.write_json(
            ART_SUMMARY,
            _summary(
                query_name=query_name,
                topic=topic,
                table=table,
                start_str=start_str,
                end_str=end_str,
                dry_run=dry_run,
                estimate=estimate,
                maximum_bytes_billed=maximum_bytes_billed,
                decision=decision,
                cache_hit=False,
                row_count=0,
                artifacts=[ART_SQL, ART_ESTIMATE, ART_DECISION, ART_SUMMARY],
                note=str(err),
            ),
        )
        raise

    # 6. Always write estimate + decision artifacts.
    _write_estimate_artifact(ctx, result.estimate, maximum_bytes_billed)
    _write_decision_artifact(
        ctx,
        decision=result.decision,
        cache_hit=result.cache_hit,
        message=result.message,
        maximum_bytes_billed=maximum_bytes_billed,
    )

    artifacts = [ART_SQL, ART_ESTIMATE, ART_DECISION, ART_SUMMARY]

    if result.has_rows:
        # 8 (execute/cache): normalize, expose to downstream, write data artifacts.
        rows = normalize_gdelt_daily_counts(result.rows, topic=topic, query_terms=query_terms)
        ctx.state["topic_daily_features"] = rows
        ctx.write_jsonl(ART_ROWS_JSONL, rows)
        _write_rows_csv(ctx, rows)
        artifacts += [ART_ROWS_JSONL, ART_ROWS_CSV]
        note = "Loaded from cache." if result.cache_hit else f"Executed BigQuery; {len(rows)} rows."
        row_count = len(rows)
    else:
        # 7 (dry-run only): no rows into state, clear note.
        row_count = 0
        note = "No data fetched because dry_run=true (dry-run estimate only)."

    ctx.write_json(
        ART_SUMMARY,
        _summary(
            query_name=query_name,
            topic=topic,
            table=table,
            start_str=start_str,
            end_str=end_str,
            dry_run=dry_run,
            estimate=result.estimate,
            maximum_bytes_billed=maximum_bytes_billed,
            decision=result.decision,
            cache_hit=result.cache_hit,
            row_count=row_count,
            artifacts=artifacts,
            note=note,
        ),
    )


def _write_estimate_artifact(ctx, estimate, maximum_bytes_billed: int) -> None:
    payload = (
        estimate.to_dict()
        if estimate is not None
        else {
            "total_bytes_processed": None,
            "estimated_gib": None,
            "estimated_tib": None,
            "estimated_cost_usd": None,
            "maximum_bytes_billed": maximum_bytes_billed,
            "note": "No dry-run estimate (served from cache).",
        }
    )
    ctx.write_json(ART_ESTIMATE, payload)


def _write_decision_artifact(
    ctx, *, decision: str, cache_hit: bool, message, maximum_bytes_billed: int
) -> None:
    ctx.write_json(
        ART_DECISION,
        {
            "decision": decision,
            "cache_hit": cache_hit,
            "maximum_bytes_billed": maximum_bytes_billed,
            "message": message,
        },
    )


def _write_rows_csv(ctx, rows: List[JsonDict]) -> None:
    path = ctx.artifacts_dir / ART_ROWS_CSV
    fields = ["date", "topic", "article_count", "provider", "mode"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fields})


def _summary(
    *,
    query_name: str,
    topic: str,
    table: str,
    start_str: str,
    end_str: str,
    dry_run: bool,
    estimate,
    maximum_bytes_billed: int,
    decision: str,
    cache_hit: bool,
    row_count: int,
    artifacts: List[str],
    note: str,
) -> JsonDict:
    return {
        "query_name": query_name,
        "topic": topic,
        "table": table,
        "start_date": start_str,
        "end_date": end_str,
        "dry_run": dry_run,
        "estimated_bytes": estimate.total_bytes_processed if estimate else None,
        "estimated_gib": round(estimate.estimated_gib, 4) if estimate else None,
        "estimated_cost_usd": (round(estimate.estimated_cost_usd, 8) if estimate else None),
        "maximum_bytes_billed": maximum_bytes_billed,
        "decision": decision,
        "cache_hit": cache_hit,
        "row_count": row_count,
        "output_artifacts": artifacts,
        "note": note,
    }

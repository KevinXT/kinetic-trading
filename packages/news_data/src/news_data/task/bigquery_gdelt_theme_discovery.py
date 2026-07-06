"""
Pipeline task: GDELT theme discovery / debug via BigQuery.

This task answers "which GDELT theme codes actually appear in this window?" so a
broad research topic (e.g. ``inflation_rates``) can be turned into a transparent,
validated set of theme codes instead of relying on a single guessed code. It is
a *debug/discovery* path, separate from the daily-count flow and from the GDELT
DOC/artlist flow.

Like the daily-count task, every query is routed through
:class:`SafeBigQueryClient` (dry-run first, ``maximum_bytes_billed`` enforced,
cost policy + ledger + cache + SQL guardrails reused). It is a *dry run* by
default and fetches no data without a typed confirmation in the config.

YAML shape::

    providers:
      bigquery:
        project_id: "YOUR_PROJECT_ID"
        table: "gdelt-bq.gdeltv2.gkg_partitioned"

    pipeline:
      ingest:
        source: bigquery_gdelt_theme_discovery
        query_name: debug_gdelt_themes_inflation_30d_partition
        topic: inflation_rates
        theme_search_patterns: [inflation, econ, interest, cost, central_bank, prices]
        partition_filter: {enabled: true, column: "_PARTITIONTIME", type: "timestamp"}
        window: {preset: last_30_days, bucket: 1d}
        limit: 100
        cost_controls:
          dry_run: true
          maximum_bytes_billed: 12000000000
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
from common.errors import CostGuardrailError, PipelineError

from news_data.bigquery.cache import bigquery_cache_path, build_cache_key
from news_data.bigquery.client import SafeBigQueryClient
from news_data.bigquery.gdelt_queries import (
    DEFAULT_THEME_COLUMN,
    DEFAULT_THEME_DISCOVERY_LIMIT,
    DEFAULT_THEME_SEARCH_PATTERNS,
    QUERY_BUILDER_VERSION,
    build_gdelt_theme_discovery_query,
)

JsonDict = Dict[str, Any]

PROVIDER = "bigquery_gdelt_theme_discovery"
_DEFAULT_TABLE = "gdelt-bq.gdeltv2.gkg_partitioned"
_DEFAULT_COST_POLICY_PATH = "configs/cost_policy.yaml"
_DEFAULT_LEDGER_PATH = "data/cost/cost_ledger.jsonl"

# Artifact filenames (kept stable for tooling / docs).
ART_SQL = "bigquery_sql.sql"
ART_ESTIMATE = "bigquery_dry_run_estimate.json"
ART_DECISION = "bigquery_cost_decision.json"
ART_SUMMARY = "bigquery_summary.json"
ART_THEMES_JSONL = "theme_discovery.jsonl"
ART_THEMES_CSV = "theme_discovery.csv"


def bigquery_gdelt_theme_discovery_task(ctx, params: JsonDict) -> None:
    bq_cfg = (ctx.cfg.get("providers", {}) or {}).get("bigquery", {}) or {}
    project_id = bq_cfg.get("project_id")
    table = bq_cfg.get("table") or _DEFAULT_TABLE

    topic = str(params.get("topic", "")).strip()
    if not topic:
        raise PipelineError("bigquery_gdelt_theme_discovery requires a non-empty 'topic'.")
    query_name = str(params.get("query_name") or topic).strip()

    search_patterns: List[str] = [
        str(p).strip()
        for p in (params.get("theme_search_patterns") or DEFAULT_THEME_SEARCH_PATTERNS)
        if str(p).strip()
    ]
    if not search_patterns:
        raise PipelineError(
            "bigquery_gdelt_theme_discovery requires a non-empty 'theme_search_patterns' list."
        )

    theme_column = str(params.get("theme_column") or DEFAULT_THEME_COLUMN).strip()

    partition_filter: JsonDict = params.get("partition_filter", {}) or {}

    window = params.get("window", {}) or {}
    try:
        limit = int(params.get("limit", DEFAULT_THEME_DISCOVERY_LIMIT))
    except (TypeError, ValueError) as exc:
        raise PipelineError(f"bigquery_gdelt_theme_discovery 'limit' must be an integer: {exc}.")
    if limit <= 0:
        raise PipelineError("bigquery_gdelt_theme_discovery 'limit' must be a positive integer.")

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

    # Resolve window and convert to YYYYMMDD for the GDELT DATE field.
    start_date, end_date = resolve_date_window(window)
    start_str = to_yyyymmdd(start_date)
    end_str = to_yyyymmdd(end_date)

    # Build SQL.
    sql = build_gdelt_theme_discovery_query(
        table=table,
        start_date=start_str,
        end_date=end_str,
        topic=topic,
        search_patterns=search_patterns,
        theme_column=theme_column,
        partition_filter=partition_filter,
        limit=limit,
    )
    (ctx.artifacts_dir / ART_SQL).write_text(sql + "\n", encoding="utf-8")

    # Stable cache key. Patterns + theme column + limit + builder version all
    # change the result set, so they are part of the key. ``query_name`` and the
    # generated SQL hash are also folded in: any change to the discovery query
    # produces a fresh key and can never reuse a stale cached result.
    cache_key = build_cache_key(
        provider=PROVIDER,
        table=table,
        topic=topic,
        query_terms=search_patterns,
        start_date=start_str,
        end_date=end_str,
        builder_version=f"{QUERY_BUILDER_VERSION}-theme-discovery-limit{limit}",
        search_columns=[theme_column],
        partition_filter=partition_filter,
        query_name=query_name,
        sql=sql,
    )
    cache_path = bigquery_cache_path(cache_key)

    client = SafeBigQueryClient(project_id=project_id, usd_per_tib=policy.bigquery_usd_per_tib)

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
                search_patterns=search_patterns,
                note=str(err),
            ),
        )
        raise

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
        rows = _normalize_theme_rows(result.rows)
        ctx.state["gdelt_theme_discovery"] = rows
        ctx.write_jsonl(ART_THEMES_JSONL, rows)
        _write_themes_csv(ctx, rows)
        artifacts += [ART_THEMES_JSONL, ART_THEMES_CSV]
        note = (
            "Loaded from cache." if result.cache_hit else f"Executed BigQuery; {len(rows)} themes."
        )
        row_count = len(rows)
    else:
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
            search_patterns=search_patterns,
            note=note,
        ),
    )


def _normalize_theme_rows(rows: List[JsonDict]) -> List[JsonDict]:
    """Normalize raw ``{theme, cnt}`` rows into ``{theme, count}`` rows."""
    out: List[JsonDict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        theme = str(row.get("theme", "")).strip()
        raw_count = row.get("cnt", row.get("count", 0))
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            count = 0
        out.append({"theme": theme, "count": count})
    return out


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


def _write_themes_csv(ctx, rows: List[JsonDict]) -> None:
    path = ctx.artifacts_dir / ART_THEMES_CSV
    fields = ["theme", "count"]
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
    search_patterns: List[str],
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
        "theme_search_patterns": list(search_patterns),
        "note": note,
    }

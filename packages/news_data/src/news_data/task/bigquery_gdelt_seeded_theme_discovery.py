"""
Pipeline task: GDELT *seeded* (record-based) theme discovery via BigQuery.

Theme-name substring discovery fails when GDELT exposes no clean taxonomy code
containing the topic word (e.g. there may be no ``*SEMICONDUCTOR*`` code even
though semiconductor coverage clearly exists). This task takes the opposite
approach: it finds GKG *records* that mention semiconductor-specific seed terms
(company / topic names such as ``NVIDIA``, ``TSMC``, ``semiconductor``), extracts
the theme codes those records carry, and ranks candidate themes by how many
seeded records carry them.

The output is a **human-review candidate list**, never an automatic bundle
update. Co-occurrence frequency is a ranking signal, not proof of semantic
relevance — a reviewer still decides which codes represent the topic.

Like every BigQuery path here, it is routed through :class:`SafeBigQueryClient`
(dry-run first, ``maximum_bytes_billed`` enforced, cost policy + ledger + SQL
guardrails reused) and is a *dry run* by default.

YAML shape::

    pipeline:
      ingest:
        source: bigquery_gdelt_seeded_theme_discovery
        query_name: semiconductors_seeded_theme_discovery_30d_partition
        topic: semiconductors
        seed_terms: [semiconductor, chipmaker, TSMC, NVIDIA, ASML, ...]
        seed_search_columns: [V2Organizations]
        partition_filter: {enabled: true, column: "_PARTITIONTIME", type: "timestamp"}
        window: {preset: last_30_days, bucket: 1d}
        limit: 200
        cost_controls: {...}
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
    DEFAULT_SEED_SAMPLE_COLUMN,
    DEFAULT_SEED_SEARCH_COLUMNS,
    DEFAULT_SEEDED_DISCOVERY_LIMIT,
    DEFAULT_SEEDED_SAMPLE_SIZE,
    DEFAULT_THEME_COLUMN,
    SEEDED_QUERY_BUILDER_VERSION,
    build_gdelt_seeded_theme_discovery_query,
)

JsonDict = Dict[str, Any]

PROVIDER = "bigquery_gdelt_seeded_theme_discovery"
_DEFAULT_TABLE = "gdelt-bq.gdeltv2.gkg_partitioned"
_DEFAULT_COST_POLICY_PATH = "configs/cost_policy.yaml"
_DEFAULT_LEDGER_PATH = "data/cost/cost_ledger.jsonl"

# Matching mode label recorded in lineage (seed terms are matched as
# case-insensitive substrings against the free-text seed columns).
MATCHING_MODE = "seed_record_substring"

# Artifact filenames (kept stable for tooling / docs).
ART_SQL = "seeded_theme_discovery_sql.sql"
ART_ESTIMATE = "bigquery_dry_run_estimate.json"
ART_DECISION = "bigquery_cost_decision.json"
ART_SUMMARY = "seeded_theme_discovery_summary.json"
ART_CANDIDATES_JSONL = "seeded_theme_candidates.jsonl"
ART_CANDIDATES_CSV = "seeded_theme_candidates.csv"

_CSV_FIELDS = [
    "theme_code",
    "record_count",
    "distinct_day_count",
    "matched_seed_terms",
    "sample_sources",
    "first_seen",
    "last_seen",
    "source_count",
    "total_seed_records",
]

_ZERO_CANDIDATE_WARNING = (
    "No seeded theme candidates were found. Widen the seed terms or the window "
    "before adding any theme code to the bundle; never guess codes."
)


def bigquery_gdelt_seeded_theme_discovery_task(ctx, params: JsonDict) -> None:
    bq_cfg = (ctx.cfg.get("providers", {}) or {}).get("bigquery", {}) or {}
    project_id = bq_cfg.get("project_id")
    table = bq_cfg.get("table") or _DEFAULT_TABLE

    topic = str(params.get("topic", "")).strip()
    if not topic:
        raise PipelineError("bigquery_gdelt_seeded_theme_discovery requires a non-empty 'topic'.")
    query_name = str(params.get("query_name") or topic).strip()

    seed_terms: List[str] = [
        str(s).strip() for s in (params.get("seed_terms") or []) if str(s).strip()
    ]
    # Optional structured seeds ``[{canonical, kind, aliases?}]`` (wins over the
    # flat list). Either must resolve to at least one seed.
    seed_specs = params.get("seed_specs") or None
    if not seed_terms and not seed_specs:
        raise PipelineError(
            "bigquery_gdelt_seeded_theme_discovery requires a non-empty 'seed_terms' "
            "list or 'seed_specs'."
        )

    seed_search_columns: List[str] = [
        str(c).strip()
        for c in (params.get("seed_search_columns") or DEFAULT_SEED_SEARCH_COLUMNS)
        if str(c).strip()
    ]
    theme_column = str(params.get("theme_column") or DEFAULT_THEME_COLUMN).strip()
    sample_source_column = str(
        params.get("sample_source_column") or DEFAULT_SEED_SAMPLE_COLUMN
    ).strip()

    partition_filter: JsonDict = params.get("partition_filter", {}) or {}
    window = params.get("window", {}) or {}

    try:
        limit = int(params.get("limit", DEFAULT_SEEDED_DISCOVERY_LIMIT))
    except (TypeError, ValueError) as exc:
        raise PipelineError(
            f"bigquery_gdelt_seeded_theme_discovery 'limit' must be an integer: {exc}."
        )
    if limit <= 0:
        raise PipelineError("bigquery_gdelt_seeded_theme_discovery 'limit' must be positive.")

    try:
        sample_size = int(params.get("sample_size", DEFAULT_SEEDED_SAMPLE_SIZE))
    except (TypeError, ValueError) as exc:
        raise PipelineError(
            f"bigquery_gdelt_seeded_theme_discovery 'sample_size' must be an integer: {exc}."
        )
    if sample_size <= 0:
        raise PipelineError("bigquery_gdelt_seeded_theme_discovery 'sample_size' must be positive.")

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

    start_date, end_date = resolve_date_window(window)
    start_str = to_yyyymmdd(start_date)
    end_str = to_yyyymmdd(end_date)

    sql = build_gdelt_seeded_theme_discovery_query(
        table=table,
        start_date=start_str,
        end_date=end_str,
        topic=topic,
        seed_terms=seed_terms or None,
        seed_specs=seed_specs,
        seed_search_columns=seed_search_columns,
        theme_column=theme_column,
        sample_source_column=sample_source_column,
        partition_filter=partition_filter,
        limit=limit,
        sample_size=sample_size,
    )
    (ctx.artifacts_dir / ART_SQL).write_text(sql + "\n", encoding="utf-8")

    cache_key = build_cache_key(
        provider=PROVIDER,
        table=table,
        topic=topic,
        query_terms=seed_terms,
        start_date=start_str,
        end_date=end_str,
        builder_version=f"{SEEDED_QUERY_BUILDER_VERSION}-limit{limit}-sample{sample_size}",
        search_columns=seed_search_columns,
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
                seed_terms=seed_terms,
                seed_search_columns=seed_search_columns,
                partition_filter=partition_filter,
                note=str(err),
                warnings=[f"blocked: {decision}"],
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
    warnings: List[str] = []

    if result.has_rows:
        rows = _normalize_candidate_rows(result.rows)
        ctx.state["gdelt_seeded_theme_discovery"] = rows
        ctx.write_jsonl(ART_CANDIDATES_JSONL, rows)
        _write_candidates_csv(ctx, rows)
        artifacts += [ART_CANDIDATES_JSONL, ART_CANDIDATES_CSV]
        row_count = len(rows)
        note = "Loaded from cache." if result.cache_hit else f"Executed BigQuery; {row_count} rows."
        if row_count == 0:
            warnings.append(_ZERO_CANDIDATE_WARNING)
    elif dry_run:
        row_count = 0
        note = "No data fetched because dry_run=true (dry-run estimate only)."
    else:
        row_count = 0
        note = "Query executed but returned no seeded theme candidates."
        warnings.append(_ZERO_CANDIDATE_WARNING)

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
            seed_terms=seed_terms,
            seed_search_columns=seed_search_columns,
            partition_filter=partition_filter,
            note=note,
            warnings=warnings,
        ),
    )


def _normalize_candidate_rows(rows: List[JsonDict]) -> List[JsonDict]:
    """Normalize raw seeded-discovery rows into deterministic candidate dicts."""
    out: List[JsonDict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "theme_code": str(row.get("theme_code", "")).strip(),
                "record_count": _int(row.get("record_count")),
                "distinct_day_count": _int(row.get("distinct_day_count")),
                "matched_seed_terms": _str_list(row.get("matched_seed_terms")),
                "sample_sources": _str_list(row.get("sample_sources")),
                "first_seen": _int_or_none(row.get("first_seen")),
                "last_seen": _int_or_none(row.get("last_seen")),
                # seeded-v2 fields (honest null when a cached v1 row lacks them).
                "source_count": _int_or_none(row.get("source_count")),
                "total_seed_records": _int_or_none(row.get("total_seed_records")),
            }
        )
    return out


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


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


def _write_candidates_csv(ctx, rows: List[JsonDict]) -> None:
    path = ctx.artifacts_dir / ART_CANDIDATES_CSV
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            flat["matched_seed_terms"] = "|".join(row.get("matched_seed_terms", []))
            flat["sample_sources"] = "|".join(row.get("sample_sources", []))
            writer.writerow({k: flat.get(k) for k in _CSV_FIELDS})


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
    seed_terms: List[str],
    seed_search_columns: List[str],
    partition_filter: JsonDict,
    note: str,
    warnings: List[str],
) -> JsonDict:
    return {
        "query_name": query_name,
        "topic": topic,
        "table": table,
        "source_table": table,
        "start_date": start_str,
        "end_date": end_str,
        "window": {"start": start_str, "end": end_str},
        "dry_run": dry_run,
        "matching_mode": MATCHING_MODE,
        "partition_filter": dict(partition_filter or {}),
        "seed_terms": list(seed_terms),
        "seed_search_columns": list(seed_search_columns),
        "estimated_bytes": estimate.total_bytes_processed if estimate else None,
        "estimated_gib": round(estimate.estimated_gib, 4) if estimate else None,
        "estimated_cost_usd": (round(estimate.estimated_cost_usd, 8) if estimate else None),
        "maximum_bytes_billed": maximum_bytes_billed,
        "decision": decision,
        "cache_hit": cache_hit,
        "row_count": row_count,
        "returned_theme_count": row_count,
        # Seeded discovery never auto-selects or modifies the bundle.
        "selected_theme_count": 0,
        "bundle_modified": False,
        "warnings": list(warnings),
        "output_artifacts": artifacts,
        "note": note,
    }

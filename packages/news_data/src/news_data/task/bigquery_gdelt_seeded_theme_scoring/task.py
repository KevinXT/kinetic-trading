"""Pipeline task orchestration for seeded GDELT theme scoring."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from common.cost.ledger import CostLedger
from common.cost.policy import load_cost_policy
from common.date_windows import resolve_date_window, to_yyyymmdd
from common.errors import CostGuardrailError, PipelineError

from news_data.bigquery.cache import bigquery_cache_path, build_cache_key
from news_data.bigquery.client import SafeBigQueryClient
from news_data.bigquery.gdelt_queries import (
    DEFAULT_SCORING_CANDIDATE_LIMIT,
    DEFAULT_SCORING_MIN_SUPPORT,
    DEFAULT_SEED_SAMPLE_COLUMN,
    DEFAULT_SEED_SEARCH_COLUMNS,
    DEFAULT_SEEDED_DISCOVERY_LIMIT,
    DEFAULT_SEEDED_SAMPLE_SIZE,
    DEFAULT_THEME_COLUMN,
    SCORING_QUERY_BUILDER_VERSION,
    build_gdelt_seeded_theme_scoring_query,
)
from news_data.bigquery.seed_matching import resolve_seed_terms

from .artifacts import (
    _write_concentration_csv,
    _write_decision_artifact,
    _write_estimate_artifact,
    _write_evidence,
    _write_review_csv,
    _write_scores_csv,
    _write_seed_diag,
    _write_stability_csv,
)
from .constants import (
    _DEFAULT_COST_POLICY_PATH,
    _DEFAULT_LEDGER_PATH,
    _DEFAULT_TABLE,
    ART_SCORES_JSONL,
    ART_SQL,
    ART_SUMMARY,
    PROVIDER,
)
from .report import _summary, _write_report
from .score import _score_rows
from .util import _non_negative_int, _positive_int

JsonDict = Dict[str, Any]

def bigquery_gdelt_seeded_theme_scoring_task(ctx, params: JsonDict) -> None:
    bq_cfg = (ctx.cfg.get("providers", {}) or {}).get("bigquery", {}) or {}
    project_id = bq_cfg.get("project_id")
    table = bq_cfg.get("table") or _DEFAULT_TABLE

    topic = str(params.get("topic", "")).strip()
    if not topic:
        raise PipelineError("bigquery_gdelt_seeded_theme_scoring requires a non-empty 'topic'.")
    query_name = str(params.get("query_name") or topic).strip()

    seed_terms: List[str] = [
        str(s).strip() for s in (params.get("seed_terms") or []) if str(s).strip()
    ]
    seed_specs = params.get("seed_specs") or None
    if not seed_terms and not seed_specs:
        raise PipelineError(
            "bigquery_gdelt_seeded_theme_scoring requires 'seed_terms' or 'seed_specs'."
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

    # `limit` is the PRESENTATION limit (top-N in review CSV / report tables).
    # `candidate_limit` is the SQL family safety cap: the FULL support-qualified
    # family is returned (bytes billed are unaffected by row count) so BH-FDR is
    # applied over every tested candidate, not an association-ranked top-N.
    limit = _positive_int(params.get("limit", DEFAULT_SEEDED_DISCOVERY_LIMIT), "limit")
    candidate_limit = _positive_int(
        params.get("candidate_limit", DEFAULT_SCORING_CANDIDATE_LIMIT), "candidate_limit"
    )
    sample_size = _positive_int(
        params.get("sample_size", DEFAULT_SEEDED_SAMPLE_SIZE), "sample_size"
    )
    min_support = _non_negative_int(
        params.get("min_support", DEFAULT_SCORING_MIN_SUPPORT), "min_support"
    )

    # Resolved seeds give a canonical -> kind map for honest diagnostics: the
    # experiment is organization-seeded only (V2Organizations), which the report
    # states explicitly.
    seeds = resolve_seed_terms(seed_specs=seed_specs, seed_terms=seed_terms or None)
    seed_kind = {s.canonical: s.kind for s in seeds}
    organization_seeded_only = [c.strip() for c in seed_search_columns] == ["V2Organizations"]

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
    n_weeks = ((end_date - start_date).days // 7) + 1

    sql = build_gdelt_seeded_theme_scoring_query(
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
        min_support=min_support,
        candidate_limit=candidate_limit,
    )
    (ctx.artifacts_dir / ART_SQL).write_text(sql + "\n", encoding="utf-8")

    cache_key = build_cache_key(
        provider=PROVIDER,
        table=table,
        topic=topic,
        query_terms=seed_terms,
        start_date=start_str,
        end_date=end_str,
        builder_version=(f"{SCORING_QUERY_BUILDER_VERSION}-cand{candidate_limit}-min{min_support}"),
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
        _write_decision_artifact(ctx, decision=decision, cache_hit=False, message=str(err))
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
                scored=[],
                seed_diag=[],
                denominators=(None, None),
                meta={},
                n_weeks=n_weeks,
                min_support=min_support,
                limit=limit,
                candidate_limit=candidate_limit,
                organization_seeded_only=organization_seeded_only,
                note=str(err),
                warnings=[f"blocked: {decision}"],
            ),
        )
        raise

    _write_estimate_artifact(ctx, result.estimate, maximum_bytes_billed)
    _write_decision_artifact(
        ctx, decision=result.decision, cache_hit=result.cache_hit, message=result.message
    )

    scored: List[JsonDict] = []
    seed_diag: List[JsonDict] = []
    denominators: Tuple[Optional[int], Optional[int]] = (None, None)
    meta: JsonDict = {}
    warnings: List[str] = []
    if result.has_rows:
        scored, seed_diag, denominators, meta = _score_rows(
            result.rows, n_weeks=n_weeks, seed_kind=seed_kind, candidate_limit=candidate_limit
        )
        ctx.state["gdelt_seeded_theme_scores"] = scored
        ctx.write_jsonl(ART_SCORES_JSONL, scored)
        _write_scores_csv(ctx, scored)
        _write_review_csv(ctx, scored, limit=limit)
        _write_seed_diag(ctx, seed_diag)
        _write_stability_csv(ctx, scored, n_weeks=n_weeks)
        _write_concentration_csv(ctx, scored)
        _write_evidence(ctx, scored, n_weeks=n_weeks)
        _write_report(
            ctx,
            scored=scored,
            seed_diag=seed_diag,
            denominators=denominators,
            meta=meta,
            n_weeks=n_weeks,
            topic=topic,
            start_str=start_str,
            end_str=end_str,
            organization_seeded_only=organization_seeded_only,
            limit=limit,
        )
        if not meta.get("family_complete", True):
            warnings.append(
                "STATISTICALLY INCOMPLETE: support-qualified family not proven "
                f"complete (status={meta.get('statistical_completion_status')}); "
                "Benjamini-Hochberg skipped, adjusted p-values are null, and no "
                "scientific verdict may be issued — raise candidate_limit above the "
                "independent qualified count and re-run."
            )
        note = (
            "Loaded from cache." if result.cache_hit else f"Executed BigQuery; {len(scored)} rows."
        )
        if not scored:
            warnings.append("No scored candidates returned; widen seeds/window before proceeding.")
    elif dry_run:
        note = "No data fetched because dry_run=true (dry-run estimate only)."
    else:
        note = "Query executed but returned no scored candidates."
        warnings.append("No scored candidates returned; widen seeds/window before proceeding.")

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
            scored=scored,
            seed_diag=seed_diag,
            denominators=denominators,
            meta=meta,
            n_weeks=n_weeks,
            min_support=min_support,
            limit=limit,
            candidate_limit=candidate_limit,
            organization_seeded_only=organization_seeded_only,
            note=note,
            warnings=warnings,
        ),
    )

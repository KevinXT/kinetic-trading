"""
SafeBigQueryClient: the only sanctioned path that executes BigQuery.

The flow is deliberately strict (see :meth:`SafeBigQueryClient.safe_query`):
validate SQL -> check local cache -> dry-run estimate -> enforce
``maximum_bytes_billed`` -> enforce single-query / daily / monthly caps ->
require typed confirmation -> execute (always with ``maximum_bytes_billed``).

``google-cloud-bigquery`` is imported lazily; importing this module never
requires the dependency. For tests, inject ``client`` and ``job_config_factory``
so no network call or credentials are needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from kinetic.ingestion.cost.estimate import (
    DEFAULT_USD_PER_TIB,
    bytes_to_gib,
    bytes_to_tib,
    estimate_bigquery_cost_usd,
)
from kinetic.ingestion.cost.ledger import CostLedger, billing_cycle_label, utc_now_iso
from kinetic.ingestion.cost.policy import CostPolicy
from kinetic.core.errors import CostGuardrailError, DataSourceError

from .cache import load_cached_rows, write_cached_rows
from .sql_guardrails import validate_bigquery_sql

JsonDict = Dict[str, Any]


class SafeQueryDecision:
    """Canonical decision strings recorded in the cost ledger."""

    USE_CACHE = "USE_CACHE"
    DRY_RUN_ONLY = "DRY_RUN_ONLY"
    ALLOW = "ALLOW"
    BLOCK_MISSING_EXECUTE_CONFIRMATION = "BLOCK_MISSING_EXECUTE_CONFIRMATION"
    BLOCK_OVER_MAX_BYTES = "BLOCK_OVER_MAX_BYTES"
    BLOCK_OVER_SINGLE_QUERY_CAP = "BLOCK_OVER_SINGLE_QUERY_CAP"
    BLOCK_OVER_DAILY_CAP = "BLOCK_OVER_DAILY_CAP"
    BLOCK_OVER_MONTHLY_CAP = "BLOCK_OVER_MONTHLY_CAP"
    BLOCK_EMERGENCY_STOP = "BLOCK_EMERGENCY_STOP"


@dataclass
class BigQueryEstimate:
    """Result of a BigQuery dry-run."""

    total_bytes_processed: int
    estimated_cost_usd: float
    estimated_gib: float
    estimated_tib: float
    maximum_bytes_billed: Optional[int] = None

    def to_dict(self) -> JsonDict:
        return {
            "total_bytes_processed": self.total_bytes_processed,
            "estimated_gib": round(self.estimated_gib, 6),
            "estimated_tib": round(self.estimated_tib, 9),
            "estimated_cost_usd": round(self.estimated_cost_usd, 8),
            "maximum_bytes_billed": self.maximum_bytes_billed,
        }


@dataclass
class SafeQueryResult:
    """Outcome of :meth:`SafeBigQueryClient.safe_query`."""

    decision: str
    rows: List[JsonDict]
    cache_hit: bool
    estimate: Optional[BigQueryEstimate]
    message: Optional[str] = None
    ledger_record: Optional[JsonDict] = None

    @property
    def executed(self) -> bool:
        return self.decision == SafeQueryDecision.ALLOW

    @property
    def has_rows(self) -> bool:
        return self.decision in (
            SafeQueryDecision.ALLOW,
            SafeQueryDecision.USE_CACHE,
        )


def _guardrail_error(
    message: str,
    *,
    decision: str,
    estimate: Optional["BigQueryEstimate"] = None,
    ledger_record: Optional[JsonDict] = None,
) -> CostGuardrailError:
    """Build a CostGuardrailError carrying decision context for callers."""
    err = CostGuardrailError(message)
    err.decision = decision  # type: ignore[attr-defined]
    err.estimate = estimate  # type: ignore[attr-defined]
    err.ledger_record = ledger_record  # type: ignore[attr-defined]
    return err


# Factory signature: (dry_run: bool, maximum_bytes_billed: Optional[int]) -> job_config
JobConfigFactory = Callable[..., Any]


def _default_job_config_factory(*, dry_run: bool, maximum_bytes_billed: Optional[int]) -> Any:
    """Build a real ``bigquery.QueryJobConfig`` (lazy import)."""
    try:
        from google.cloud import bigquery
    except ImportError as e:  # pragma: no cover - exercised only without the dep
        raise DataSourceError(
            "google-cloud-bigquery is not installed. Install it with "
            "'pip install google-cloud-bigquery' to run BigQuery queries."
        ) from e
    cfg = bigquery.QueryJobConfig(dry_run=dry_run, use_query_cache=False)
    if maximum_bytes_billed is not None:
        cfg.maximum_bytes_billed = int(maximum_bytes_billed)
    return cfg


class SafeBigQueryClient:
    """Cost-guarded wrapper around a BigQuery client."""

    def __init__(
        self,
        project_id: Optional[str] = None,
        *,
        client: Any = None,
        job_config_factory: Optional[JobConfigFactory] = None,
        usd_per_tib: float = DEFAULT_USD_PER_TIB,
    ) -> None:
        self.project_id = project_id
        self._client = client
        self._job_config_factory = job_config_factory or _default_job_config_factory
        self.usd_per_tib = usd_per_tib

    # ── low-level BigQuery access ───────────────────────────────────────────

    def _client_or_create(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.project_id or self.project_id == "YOUR_PROJECT_ID":
            raise DataSourceError(
                "BigQuery project_id is required. Set providers.bigquery.project_id "
                "in your config to your real GCP project id."
            )
        try:
            from google.cloud import bigquery
        except ImportError as e:
            raise DataSourceError(
                "google-cloud-bigquery is not installed. Install it with "
                "'pip install google-cloud-bigquery' to run BigQuery queries."
            ) from e
        try:
            self._client = bigquery.Client(project=self.project_id)
        except Exception as e:  # pragma: no cover - depends on local credentials
            raise DataSourceError(
                "Failed to initialize the BigQuery client. Check that credentials "
                f"and project_id ({self.project_id!r}) are configured: {e}"
            ) from e
        return self._client

    def dry_run(self, sql: str, maximum_bytes_billed: Optional[int] = None) -> BigQueryEstimate:
        """Estimate scanned bytes / cost without running the query."""
        client = self._client_or_create()
        job_config = self._job_config_factory(
            dry_run=True, maximum_bytes_billed=maximum_bytes_billed
        )
        job = client.query(sql, job_config=job_config)
        total_bytes = int(getattr(job, "total_bytes_processed", 0) or 0)
        return BigQueryEstimate(
            total_bytes_processed=total_bytes,
            estimated_cost_usd=estimate_bigquery_cost_usd(total_bytes, self.usd_per_tib),
            estimated_gib=bytes_to_gib(total_bytes),
            estimated_tib=bytes_to_tib(total_bytes),
            maximum_bytes_billed=(
                int(maximum_bytes_billed) if maximum_bytes_billed is not None else None
            ),
        )

    def run_query(self, sql: str, maximum_bytes_billed: int) -> List[JsonDict]:
        """Execute the query. ``maximum_bytes_billed`` is mandatory."""
        if maximum_bytes_billed is None:
            raise ValueError("run_query: maximum_bytes_billed must always be set (cost safety).")
        client = self._client_or_create()
        job_config = self._job_config_factory(
            dry_run=False, maximum_bytes_billed=maximum_bytes_billed
        )
        job = client.query(sql, job_config=job_config)
        result = job.result()
        return [dict(row) for row in result]

    # ── orchestration ───────────────────────────────────────────────────────

    def safe_query(
        self,
        *,
        sql: str,
        query_name: str,
        topic: Optional[str],
        cost_policy: CostPolicy,
        cost_controls: Optional[JsonDict] = None,
        project_id: Optional[str] = None,
        cache_key: Optional[str] = None,
        cache_path: Optional[str | Path] = None,
        ledger: Optional[CostLedger] = None,
        allowed_tables: Optional[Sequence[str]] = None,
        dry_run: bool = True,
        maximum_bytes_billed: Optional[int] = None,
        execute_query: str = "",
        use_cache: bool = True,
    ) -> SafeQueryResult:
        """
        Run the full cost-guarded flow. See module docstring for the ordering.

        Never executes a billable query unless: a dry-run estimate was taken,
        the estimate is within ``maximum_bytes_billed`` and all caps, and
        ``execute_query`` matches ``cost_policy.execute_confirmation_value``.
        """
        provider = "bigquery_gdelt"
        allowed = list(allowed_tables) if allowed_tables else None

        # 1. Validate SQL before anything else.
        validate_bigquery_sql(sql, allowed)

        if maximum_bytes_billed is None:
            maximum_bytes_billed = cost_policy.default_max_query_bytes
        maximum_bytes_billed = int(maximum_bytes_billed)

        def log(
            decision: str, *, estimate=None, cache_hit=False, status="success", message=None
        ) -> JsonDict:
            record = {
                "timestamp": utc_now_iso(),
                "billing_cycle": billing_cycle_label(
                    datetime.now(timezone.utc).date(),
                    start_day=cost_policy.billing_cycle_start_day,
                ),
                "provider": provider,
                "query_name": query_name,
                "topic": topic,
                "dry_run": bool(dry_run),
                "estimated_bytes": (estimate.total_bytes_processed if estimate else None),
                "estimated_cost_usd": (round(estimate.estimated_cost_usd, 8) if estimate else 0.0),
                "maximum_bytes_billed": maximum_bytes_billed,
                "decision": decision,
                "cache_hit": cache_hit,
                "status": status,
                "message": message,
            }
            if ledger is not None:
                return ledger.append(record)
            return record

        # 2. Cache check (before any cloud access).
        if use_cache and cost_policy.require_cache_check and cache_path is not None:
            cached = load_cached_rows(cache_path)
            if cached is not None:
                rec = log(
                    SafeQueryDecision.USE_CACHE, cache_hit=True, message="Loaded from local cache."
                )
                return SafeQueryResult(
                    decision=SafeQueryDecision.USE_CACHE,
                    rows=cached,
                    cache_hit=True,
                    estimate=None,
                    message="Loaded from local cache.",
                    ledger_record=rec,
                )

        # 3. Always dry-run to estimate (unless served from cache above).
        estimate = self.dry_run(sql, maximum_bytes_billed=maximum_bytes_billed)
        estimate.maximum_bytes_billed = maximum_bytes_billed

        # 4-7. Guardrail evaluation.
        violation = self._evaluate_guardrails(
            estimate=estimate,
            cost_policy=cost_policy,
            maximum_bytes_billed=maximum_bytes_billed,
            ledger=ledger,
        )

        # 8. Dry-run only: the estimate is informational, so the decision is
        # always DRY_RUN_ONLY (caps only *block* on the execution path below).
        # We still surface a would-be-blocked note so the estimate is actionable.
        if dry_run:
            message = "Dry-run only; no data fetched."
            if violation:
                message += f" Note: would be blocked on execution ({violation})."
            rec = log(SafeQueryDecision.DRY_RUN_ONLY, estimate=estimate, message=message)
            return SafeQueryResult(
                decision=SafeQueryDecision.DRY_RUN_ONLY,
                rows=[],
                cache_hit=False,
                estimate=estimate,
                message=message,
                ledger_record=rec,
            )

        # Execution path: a guardrail breach blocks before any spend.
        if violation:
            rec = log(
                violation,
                estimate=estimate,
                status="blocked",
                message=f"Blocked by cost guardrail: {violation}",
            )
            raise _guardrail_error(
                f"BigQuery query {query_name!r} blocked: {violation}. "
                f"Estimated {estimate.estimated_gib:.2f} GiB "
                f"(${estimate.estimated_cost_usd:.4f}).",
                decision=violation,
                estimate=estimate,
                ledger_record=rec,
            )

        # 9. Require typed confirmation for real execution.
        if (
            cost_policy.require_execute_confirmation
            and execute_query != cost_policy.execute_confirmation_value
        ):
            rec = log(
                SafeQueryDecision.BLOCK_MISSING_EXECUTE_CONFIRMATION,
                estimate=estimate,
                status="blocked",
                message="Missing typed execute confirmation.",
            )
            raise _guardrail_error(
                f"BigQuery query {query_name!r} requires typed confirmation to run. "
                f"Set cost_controls.execute_query to "
                f"{cost_policy.execute_confirmation_value!r} (got {execute_query!r}).",
                decision=SafeQueryDecision.BLOCK_MISSING_EXECUTE_CONFIRMATION,
                estimate=estimate,
                ledger_record=rec,
            )

        # 10. Allowed: execute with maximum_bytes_billed always set.
        rows = self.run_query(sql, maximum_bytes_billed=maximum_bytes_billed)
        if cache_path is not None:
            write_cached_rows(cache_path, rows)
        rec = log(
            SafeQueryDecision.ALLOW,
            estimate=estimate,
            message=f"Executed; {len(rows)} rows.",
        )
        return SafeQueryResult(
            decision=SafeQueryDecision.ALLOW,
            rows=rows,
            cache_hit=False,
            estimate=estimate,
            message=f"Executed; {len(rows)} rows.",
            ledger_record=rec,
        )

    @staticmethod
    def _evaluate_guardrails(
        *,
        estimate: BigQueryEstimate,
        cost_policy: CostPolicy,
        maximum_bytes_billed: int,
        ledger: Optional[CostLedger],
    ) -> Optional[str]:
        """Return a BLOCK_* decision string if a guardrail is violated, else None."""
        if estimate.total_bytes_processed > maximum_bytes_billed:
            return SafeQueryDecision.BLOCK_OVER_MAX_BYTES

        if estimate.estimated_cost_usd > cost_policy.single_query_cap_usd:
            return SafeQueryDecision.BLOCK_OVER_SINGLE_QUERY_CAP

        if ledger is not None:
            summary = ledger.summarize(cost_policy)
            projected_cycle = summary.cycle_estimated_usd + estimate.estimated_cost_usd
            projected_today = summary.today_estimated_usd + estimate.estimated_cost_usd

            if summary.emergency_stop_reached or projected_cycle >= cost_policy.emergency_stop_usd:
                return SafeQueryDecision.BLOCK_EMERGENCY_STOP
            if projected_today > cost_policy.daily_cap_usd:
                return SafeQueryDecision.BLOCK_OVER_DAILY_CAP
            if projected_cycle > cost_policy.monthly_cap_usd:
                return SafeQueryDecision.BLOCK_OVER_MONTHLY_CAP

        return None

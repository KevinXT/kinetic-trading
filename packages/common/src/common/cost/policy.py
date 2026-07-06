"""
Cost policy model + loader.

A :class:`CostPolicy` is the single source of truth for spend guardrails. It is
loaded from ``configs/cost_policy.yaml`` (or any compatible YAML) and validated
eagerly so misconfigured caps fail fast rather than during a live query.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from common.config_builder import load_yaml
from common.errors import ConfigError

JsonDict = Dict[str, Any]

# Safe defaults applied when an optional field is missing from YAML.
_DEFAULTS = {
    "billing_cycle_start_day": 1,
    "monthly_cap_usd": 10.0,
    "emergency_stop_usd": 8.0,
    "daily_cap_usd": 0.50,
    "single_query_cap_usd": 0.02,
    "default_max_query_gb": 1.0,
    "require_dry_run": True,
    "require_cache_check": True,
    "require_execute_confirmation": True,
    "execute_confirmation_value": "ENABLE",
    "bigquery_usd_per_tib": 6.25,
    "free_query_tib_per_month": 1.0,
}


@dataclass(frozen=True)
class CostPolicy:
    """Validated, immutable spend guardrails."""

    billing_cycle_start_day: int
    monthly_cap_usd: float
    emergency_stop_usd: float
    daily_cap_usd: float
    single_query_cap_usd: float
    default_max_query_gb: float
    require_dry_run: bool
    require_cache_check: bool
    require_execute_confirmation: bool
    execute_confirmation_value: str
    bigquery_usd_per_tib: float
    free_query_tib_per_month: float

    @property
    def default_max_query_bytes(self) -> int:
        """``default_max_query_gb`` expressed in bytes (GiB-based)."""
        return int(self.default_max_query_gb * (1024**3))


def _as_float(value: Any, *, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise ConfigError(f"cost_policy.{field} must be a number, got {value!r}.") from e


def _as_int(value: Any, *, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise ConfigError(f"cost_policy.{field} must be an integer, got {value!r}.") from e


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def cost_policy_from_dict(raw: JsonDict) -> CostPolicy:
    """
    Build a :class:`CostPolicy` from a raw mapping.

    Accepts either the top-level ``{"cost_policy": {...}}`` wrapper or the inner
    mapping directly. Missing optional fields fall back to safe defaults.

    Raises:
        ConfigError: A cap is negative, the emergency stop exceeds the monthly
            cap, ``default_max_query_gb`` is not positive, or a required
            confirmation value is empty.
    """
    if not isinstance(raw, dict):
        raise ConfigError(f"cost policy must be a mapping, got {type(raw).__name__}.")

    body = raw.get("cost_policy", raw)
    if not isinstance(body, dict):
        raise ConfigError("'cost_policy' section must be a mapping.")

    pricing = body.get("pricing", {}) or {}
    if not isinstance(pricing, dict):
        raise ConfigError("cost_policy.pricing must be a mapping when present.")

    def pick(key: str) -> Any:
        if key in body:
            return body[key]
        if key in pricing:
            return pricing[key]
        return _DEFAULTS[key]

    policy = CostPolicy(
        billing_cycle_start_day=_as_int(
            pick("billing_cycle_start_day"), field="billing_cycle_start_day"
        ),
        monthly_cap_usd=_as_float(pick("monthly_cap_usd"), field="monthly_cap_usd"),
        emergency_stop_usd=_as_float(pick("emergency_stop_usd"), field="emergency_stop_usd"),
        daily_cap_usd=_as_float(pick("daily_cap_usd"), field="daily_cap_usd"),
        single_query_cap_usd=_as_float(pick("single_query_cap_usd"), field="single_query_cap_usd"),
        default_max_query_gb=_as_float(pick("default_max_query_gb"), field="default_max_query_gb"),
        require_dry_run=_as_bool(pick("require_dry_run")),
        require_cache_check=_as_bool(pick("require_cache_check")),
        require_execute_confirmation=_as_bool(pick("require_execute_confirmation")),
        execute_confirmation_value=str(pick("execute_confirmation_value")),
        bigquery_usd_per_tib=_as_float(pick("bigquery_usd_per_tib"), field="bigquery_usd_per_tib"),
        free_query_tib_per_month=_as_float(
            pick("free_query_tib_per_month"), field="free_query_tib_per_month"
        ),
    )

    _validate(policy)
    return policy


def _validate(policy: CostPolicy) -> None:
    caps = {
        "monthly_cap_usd": policy.monthly_cap_usd,
        "emergency_stop_usd": policy.emergency_stop_usd,
        "daily_cap_usd": policy.daily_cap_usd,
        "single_query_cap_usd": policy.single_query_cap_usd,
    }
    for name, value in caps.items():
        if value < 0:
            raise ConfigError(f"cost_policy.{name} cannot be negative (got {value}).")

    if policy.emergency_stop_usd > policy.monthly_cap_usd:
        raise ConfigError(
            "cost_policy.emergency_stop_usd "
            f"({policy.emergency_stop_usd}) cannot exceed monthly_cap_usd "
            f"({policy.monthly_cap_usd})."
        )

    if policy.default_max_query_gb <= 0:
        raise ConfigError(
            "cost_policy.default_max_query_gb must be > 0 " f"(got {policy.default_max_query_gb})."
        )

    if not (1 <= policy.billing_cycle_start_day <= 28):
        raise ConfigError(
            "cost_policy.billing_cycle_start_day must be between 1 and 28 "
            f"(got {policy.billing_cycle_start_day})."
        )

    if (
        policy.require_execute_confirmation
        and not (policy.execute_confirmation_value or "").strip()
    ):
        raise ConfigError(
            "cost_policy.execute_confirmation_value must be non-empty when "
            "require_execute_confirmation is true."
        )


def load_cost_policy(path: str | Path) -> CostPolicy:
    """Load and validate a :class:`CostPolicy` from a YAML file."""
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"cost policy file not found: {p}")
    return cost_policy_from_dict(load_yaml(p))

"""Reusable leakage / availability audits for research observations.

These functions encode the invariants from the design memo so a dataset can be
checked mechanically. A dataset that violates them should fail loudly rather than
silently produce a leaky research product.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kinetic.data.catalog.features import FeatureCatalog
from kinetic.data.schemas.research import NewsMarketObservation, SessionNewsFeature


@dataclass(frozen=True)
class AuditIssue:
    observation_id: str
    code: str
    detail: str


def audit_observation(obs: NewsMarketObservation) -> list[AuditIssue]:
    """Audit one observation for availability-ordering leakage."""
    issues: list[AuditIssue] = []
    # Equality at the explicit feature cutoff is allowed.
    if obs.feature_available_at > obs.feature_cutoff:
        issues.append(
            AuditIssue(
                obs.observation_id,
                "input_after_cutoff",
                f"feature_available_at {obs.feature_available_at} > feature_cutoff "
                f"{obs.feature_cutoff}",
            )
        )
    # Targets must not leak into inputs: no key that looks like a forward target
    # may appear in the inputs namespace.
    for key in obs.inputs:
        if key.startswith(("target_", "forward_", "fwd_", "next_session")):
            issues.append(AuditIssue(obs.observation_id, "target_in_inputs", f"input key {key!r}"))
    # Contemporaneous fields must not be duplicated into inputs.
    contemporaneous_keys = set(obs.contemporaneous)
    for key in obs.inputs:
        if key in contemporaneous_keys:
            issues.append(
                AuditIssue(
                    obs.observation_id,
                    "contemporaneous_in_inputs",
                    f"input key {key!r} duplicates a contemporaneous field",
                )
            )
    return issues


def audit_observation_catalog(
    obs: NewsMarketObservation, catalog: FeatureCatalog
) -> list[AuditIssue]:
    """Require every dynamic field to live in its catalog-declared section."""
    issues: list[AuditIssue] = []
    sections = {
        "inputs": (obs.inputs, "input"),
        "contemporaneous": (obs.contemporaneous, "contemporaneous"),
        "targets": (obs.targets, "target"),
    }
    for section, (values, expected_category) in sections.items():
        for key in values:
            definition = catalog.by_name(key)
            if definition is None:
                issues.append(
                    AuditIssue(
                        obs.observation_id,
                        "feature_missing_from_catalog",
                        f"{section} field {key!r} is not cataloged",
                    )
                )
            elif definition.category != expected_category:
                issues.append(
                    AuditIssue(
                        obs.observation_id,
                        "feature_category_mismatch",
                        f"{section} field {key!r} is cataloged as {definition.category!r}",
                    )
                )
    return issues


def predictive_feature_row(obs: NewsMarketObservation, catalog: FeatureCatalog) -> dict[str, Any]:
    """Return a model-ready row containing inputs only, never outcomes/descriptors."""
    issues = audit_observation_catalog(obs, catalog)
    if issues:
        preview = "; ".join(issue.detail for issue in issues[:5])
        raise ValueError(f"catalog validation failed: {preview}")
    return {
        "observation_id": obs.observation_id,
        "session_date": obs.session_date.isoformat(),
        "topic": obs.topic,
        "instrument_id": obs.instrument_id,
        "symbol": obs.symbol,
        "feature_cutoff": obs.to_dict()["feature_cutoff"],
        **dict(obs.inputs),
    }


def audit_session_news(feature: SessionNewsFeature) -> list[AuditIssue]:
    """Audit a session news feature's window/availability ordering."""
    issues: list[AuditIssue] = []
    ident = f"{feature.session_date.isoformat()}:{feature.topic}"
    if feature.feature_window_start > feature.feature_window_end:
        issues.append(AuditIssue(ident, "window_inverted", "window_start after window_end"))
    if feature.feature_available_at < feature.feature_window_end:
        issues.append(
            AuditIssue(ident, "available_before_window_end", "available_at before window_end")
        )
    if feature.feature_available_at > feature.feature_cutoff:
        issues.append(
            AuditIssue(
                ident,
                "available_after_cutoff",
                "feature_available_at after feature_cutoff",
            )
        )
    return issues


def audit_dataset(observations: list[NewsMarketObservation]) -> list[AuditIssue]:
    """Audit a full dataset; returns all issues found (empty means clean)."""
    issues: list[AuditIssue] = []
    for obs in observations:
        issues.extend(audit_observation(obs))
    return issues


def assert_required_bar_symbols(available_symbols: set[str], required_symbols: list[str]) -> None:
    """Fail the build early if any required instrument/benchmark bar is absent.

    ``required_symbols`` typically lists the study instruments plus the benchmark.
    A missing benchmark or instrument would silently drop targets downstream, so
    this pre-build check turns that into a loud, actionable error.
    """
    available = {str(sym).strip().upper() for sym in available_symbols}
    required = [str(sym).strip().upper() for sym in required_symbols if str(sym).strip()]
    missing = sorted({sym for sym in required if sym not in available})
    if missing:
        raise ValueError(
            "required market bars are missing for symbol(s): "
            f"{missing}; available symbols: {sorted(available)}"
        )


def assert_no_leakage(observations: list[NewsMarketObservation]) -> None:
    """Raise if any observation fails the leakage audit."""
    issues = audit_dataset(observations)
    if issues:
        preview = "; ".join(f"{i.code}({i.observation_id}): {i.detail}" for i in issues[:5])
        raise ValueError(f"leakage audit failed with {len(issues)} issue(s): {preview}")


def assert_catalog_categories(
    observations: list[NewsMarketObservation], catalog: FeatureCatalog
) -> None:
    issues: list[AuditIssue] = []
    for obs in observations:
        issues.extend(audit_observation_catalog(obs, catalog))
    if issues:
        preview = "; ".join(f"{i.code}({i.observation_id}): {i.detail}" for i in issues[:5])
        raise ValueError(f"feature-catalog audit failed with {len(issues)} issue(s): {preview}")

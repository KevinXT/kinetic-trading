"""Guards for the refined + seeded semiconductor theme-discovery workflow.

Covers: the recorded false-positive rejection in the bundle, refined-pattern
safety (no bare foundry/wafer/lithography), seeded-config cost controls, and the
human-review worksheet helper. All offline and credential-free.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from common.config_builder import load_yaml
from news_data.bigquery.theme_review import (
    DEFAULT_KNOWN_REJECTED,
    REVIEW_FIELDS,
    build_review_rows,
    read_discovery_csv,
    write_review_worksheet,
)

RESEARCH_DIR = Path("configs/research")
BUNDLES_PATH = Path("configs/gdelt_theme_bundles.yaml")

REFINED_DRYRUN = "semiconductors_theme_discovery_refined_30d_dryrun.yaml"
REFINED_EXECUTE = "semiconductors_theme_discovery_refined_30d_execute.yaml"
SEEDED_DRYRUN = "semiconductors_seeded_theme_discovery_30d_dryrun.yaml"
SEEDED_EXECUTE = "semiconductors_seeded_theme_discovery_30d_execute.yaml"

UNSAFE_BARE = {"foundry", "wafer", "lithography"}
TARGETED = {
    "semiconductor",
    "semiconductors",
    "microelectronics",
    "microprocessor",
    "microprocessors",
    "integrated_circuit",
    "integrated_circuits",
    "silicon_chip",
    "computer_chip",
    "chipmaker",
    "chipmakers",
    "chip_manufacturing",
    "wafer_fabrication",
    "photolithography",
    "fabless",
}
REJECTED_CODE = "TAX_FNCACT_FOUNDRYMAN"


def _ingest(cfg: dict) -> dict:
    return (cfg.get("pipeline", {}) or {}).get("ingest", {}) or {}


def _semiconductors_bundle() -> dict:
    data = yaml.safe_load(BUNDLES_PATH.read_text(encoding="utf-8"))
    return data["theme_bundles"]["semiconductors"]


# ── Phase 2: recorded rejection ───────────────────────────────────────────────


def test_bundle_records_rejected_false_positive() -> None:
    bundle = _semiconductors_bundle()
    assert bundle["status"] == "placeholder_pending_discovery"
    assert bundle["themes"] == []
    assert bundle["selected_theme_codes"] == []
    assert bundle["source_discovery_run_id"] == "semiconductors_theme_discovery_90d_execute"
    excluded = {e["code"] for e in bundle["excluded_ambiguous_theme_codes"]}
    assert REJECTED_CODE in excluded


def test_rejected_theme_is_not_a_selected_theme() -> None:
    bundle = _semiconductors_bundle()
    assert REJECTED_CODE not in bundle["themes"]
    assert REJECTED_CODE not in bundle["selected_theme_codes"]


# ── Phase 3/4: refined pattern safety ─────────────────────────────────────────


def test_refined_configs_exist() -> None:
    assert (RESEARCH_DIR / REFINED_DRYRUN).is_file()
    assert (RESEARCH_DIR / REFINED_EXECUTE).is_file()


@pytest.mark.parametrize("name", [REFINED_DRYRUN, REFINED_EXECUTE])
def test_refined_configs_use_targeted_token_patterns(name: str) -> None:
    ingest = _ingest(load_yaml(RESEARCH_DIR / name))
    patterns = set(ingest.get("theme_search_patterns", []))
    assert patterns == TARGETED
    assert not (patterns & UNSAFE_BARE)  # no bare foundry/wafer/lithography
    assert ingest.get("match_mode") == "token"
    assert ingest.get("topic") == "semiconductors"


def test_refined_dryrun_and_execute_patterns_match() -> None:
    dry = _ingest(load_yaml(RESEARCH_DIR / REFINED_DRYRUN)).get("theme_search_patterns")
    ex = _ingest(load_yaml(RESEARCH_DIR / REFINED_EXECUTE)).get("theme_search_patterns")
    assert dry == ex


@pytest.mark.parametrize("name", [REFINED_DRYRUN, REFINED_EXECUTE])
def test_refined_configs_are_30_day_and_partition_pruned(name: str) -> None:
    ingest = _ingest(load_yaml(RESEARCH_DIR / name))
    assert ingest.get("window", {}).get("preset") == "last_30_days"
    pf = ingest.get("partition_filter", {}) or {}
    assert pf.get("enabled") is True and pf.get("column") == "_PARTITIONTIME"


def test_refined_cost_controls() -> None:
    dry = _ingest(load_yaml(RESEARCH_DIR / REFINED_DRYRUN)).get("cost_controls", {})
    ex = _ingest(load_yaml(RESEARCH_DIR / REFINED_EXECUTE)).get("cost_controls", {})
    assert dry.get("dry_run") is True and dry.get("execute_query", "") != "ENABLE"
    assert ex.get("dry_run") is False and ex.get("execute_query") == "ENABLE"
    for cc in (dry, ex):
        assert isinstance(cc.get("maximum_bytes_billed"), int) and cc["maximum_bytes_billed"] > 0
        assert cc.get("cost_policy_path")
        assert cc.get("ledger_path")
        assert cc.get("use_cache") is False


# ── Phase 6: seeded config cost controls ──────────────────────────────────────


def test_seeded_configs_exist() -> None:
    assert (RESEARCH_DIR / SEEDED_DRYRUN).is_file()
    assert (RESEARCH_DIR / SEEDED_EXECUTE).is_file()


@pytest.mark.parametrize("name", [SEEDED_DRYRUN, SEEDED_EXECUTE])
def test_seeded_configs_shape_and_partition(name: str) -> None:
    ingest = _ingest(load_yaml(RESEARCH_DIR / name))
    assert ingest.get("source") == "bigquery_gdelt_seeded_theme_discovery"
    assert ingest.get("topic") == "semiconductors"
    assert ingest.get("seed_terms")  # non-empty
    assert ingest.get("window", {}).get("preset") == "last_30_days"
    pf = ingest.get("partition_filter", {}) or {}
    assert pf.get("enabled") is True and pf.get("column") == "_PARTITIONTIME"


def test_seeded_cost_controls() -> None:
    dry = _ingest(load_yaml(RESEARCH_DIR / SEEDED_DRYRUN)).get("cost_controls", {})
    ex = _ingest(load_yaml(RESEARCH_DIR / SEEDED_EXECUTE)).get("cost_controls", {})
    assert dry.get("dry_run") is True and dry.get("execute_query", "") != "ENABLE"
    assert ex.get("dry_run") is False and ex.get("execute_query") == "ENABLE"
    for cc in (dry, ex):
        assert isinstance(cc.get("maximum_bytes_billed"), int) and cc["maximum_bytes_billed"] > 0
        assert cc.get("ledger_path")


# ── Phase 7: review worksheet helper ──────────────────────────────────────────


def test_review_worksheet_prefills_known_rejected() -> None:
    candidates = [
        {"theme": "TAX_FNCACT_FOUNDRYMAN", "count": 29},
        {"theme": "WB_2461_SEMICONDUCTORS", "count": 300},
    ]
    rows = build_review_rows(candidates, source_run_id="run-x")
    assert list(rows[0].keys()) == REVIEW_FIELDS
    assert rows[0]["theme_code"] == "TAX_FNCACT_FOUNDRYMAN"
    assert rows[0]["keep_or_reject"] == "reject"
    assert "foundryman" in rows[0]["rationale"].lower()
    # A non-rejected candidate is left blank for the reviewer.
    assert rows[1]["keep_or_reject"] == ""
    assert all(r["source_run_id"] == "run-x" for r in rows)
    assert "TAX_FNCACT_FOUNDRYMAN" in DEFAULT_KNOWN_REJECTED


def test_review_worksheet_accepts_seeded_shape() -> None:
    candidates = [{"theme_code": "ECON_SUBSIDIES", "record_count": 40}]
    rows = build_review_rows(candidates, source_run_id="seed-run")
    assert rows[0]["theme_code"] == "ECON_SUBSIDIES"
    assert str(rows[0]["count"]) == "40"


def test_review_worksheet_roundtrip(tmp_path: Path) -> None:
    candidates = [{"theme": "TAX_FNCACT_FOUNDRYMAN", "count": 29}]
    rows = build_review_rows(candidates, source_run_id="run-x")
    out = tmp_path / "worksheet.csv"
    write_review_worksheet(rows, out)
    back = read_discovery_csv(out)
    assert back[0]["theme_code"] == "TAX_FNCACT_FOUNDRYMAN"
    assert back[0]["keep_or_reject"] == "reject"

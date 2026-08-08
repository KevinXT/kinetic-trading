"""End-to-end synthetic real-corpus pilot integration tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from kinetic.core.pipeline.context import RunContext
from kinetic.ml.relevance.pilot_task import (
    run_semiconductor_relevance_real_corpus_pilot_task,
)

FIXED_CLOCK = datetime(2026, 7, 17, 15, 0, 0, tzinfo=timezone.utc)


def _load_params() -> dict:
    path = Path(
        "projects/semiconductor_case_study/configs/"
        "semiconductor_relevance_real_corpus_pilot_local.yaml"
    )
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    return dict(cfg["pipeline"]["steps"][0]["params"])


def test_task_registered() -> None:
    from kinetic.bootstrap import build_default_registry

    registry = build_default_registry()
    assert "ml.relevance.run_real_corpus_pilot" in registry.task_ids()


def test_blocked_without_calibration_approval(tmp_path: Path) -> None:
    params = _load_params()
    params = dict(params)
    params.pop("calibration_approval", None)
    ctx = RunContext(
        cfg={"name": "test"},
        run_name="test",
        run_id="test",
        run_dir=tmp_path / "blocked",
    )
    run_semiconductor_relevance_real_corpus_pilot_task(ctx, params, clock=lambda: FIXED_CLOCK)
    state = json.loads((ctx.artifacts_dir / "pilot_state.json").read_text())
    assert state["pilot_state"] == "FORMAL_PILOT_APPROVAL_REQUIRED"
    assert not (ctx.artifacts_dir / "formal_annotation_batch.csv").exists()
    readiness = json.loads((ctx.artifacts_dir / "pilot_readiness_decision.json").read_text())
    assert readiness["status"] in {"NOT_READY", "REVIEW_REQUIRED"}


def test_full_synthetic_pilot_deterministic(tmp_path: Path) -> None:
    params = _load_params()

    def run_once(run_dir: Path) -> RunContext:
        ctx = RunContext(
            cfg={"name": "test"},
            run_name="test",
            run_id="test",
            run_dir=run_dir,
        )
        run_semiconductor_relevance_real_corpus_pilot_task(ctx, params, clock=lambda: FIXED_CLOCK)
        return ctx

    ctx1 = run_once(tmp_path / "r1")
    ctx2 = run_once(tmp_path / "r2")

    scientific = [
        "pilot_sample_size_plan.json",
        "representative_sample.jsonl",
        "challenge_sample.jsonl",
        "sample_overlap_report.json",
        "pilot_sampling_frame.jsonl",
        "corpus_eligibility_decisions.jsonl",
        "calibration_agreement.json",
        "formal_annotation_agreement.json",
        "representative_baseline_metrics.json",
        "challenge_baseline_metrics.json",
        "duplicate_threshold_metrics.csv",
        "pilot_readiness_decision.json",
        "artifact_reconciliation.json",
    ]
    for name in scientific:
        p1 = ctx1.artifacts_dir / name
        p2 = ctx2.artifacts_dir / name
        assert p1.is_file(), name
        assert p1.read_bytes() == p2.read_bytes(), name

    # Blind annotation batch headers
    batch = (ctx1.artifacts_dir / "formal_annotation_batch.csv").read_text(encoding="utf-8")
    header = batch.splitlines()[0].lower()
    for forbidden in ("baseline", "theme", "return", "prediction", "expected_label", "weight"):
        assert forbidden not in header
    assert "challenge_reason_codes_hidden" in header

    readiness = json.loads((ctx1.artifacts_dir / "pilot_readiness_decision.json").read_text())
    # Without human approval gate, cannot be READY_FOR_FINAL_BENCHMARK_CONSTRUCTION
    assert readiness["status"] == "REVIEW_REQUIRED"

    report = (ctx1.artifacts_dir / "pilot_quantitative_report.md").read_text(encoding="utf-8")
    assert "trading edge" in report.lower() or "does **not** demonstrate" in report
    assert "profit" not in report.lower() or "no predictive" in report.lower()

    recon = json.loads((ctx1.artifacts_dir / "artifact_reconciliation.json").read_text())
    assert recon["ok"] is True

    # No production theme mutation artifacts
    assert not (ctx1.artifacts_dir / "theme_bundles.json").exists()


def test_reordered_input_determinism(tmp_path: Path) -> None:
    params = _load_params()
    articles = Path(params["articles_path"]).read_text(encoding="utf-8").splitlines()
    articles = [ln for ln in articles if ln.strip()]
    reordered = tmp_path / "articles_reordered.jsonl"
    reordered.write_text("\n".join(reversed(articles)) + "\n", encoding="utf-8")

    params_a = dict(params)
    params_b = dict(params)
    params_b["articles_path"] = str(reordered)

    def run(params_local: dict, run_dir: Path) -> bytes:
        ctx = RunContext(
            cfg={"name": "test"},
            run_name="test",
            run_id="test",
            run_dir=run_dir,
        )
        run_semiconductor_relevance_real_corpus_pilot_task(
            ctx, params_local, clock=lambda: FIXED_CLOCK
        )
        return (ctx.artifacts_dir / "representative_sample.jsonl").read_bytes()

    assert run(params_a, tmp_path / "a") == run(params_b, tmp_path / "b")


def test_human_approval_gate(tmp_path: Path) -> None:
    params = dict(_load_params())
    params["pilot_human_approval"] = "APPROVE_FINAL_BENCHMARK_CONSTRUCTION"
    ctx = RunContext(
        cfg={"name": "test"},
        run_name="test",
        run_id="test",
        run_dir=tmp_path / "approved",
    )
    run_semiconductor_relevance_real_corpus_pilot_task(ctx, params, clock=lambda: FIXED_CLOCK)
    readiness = json.loads((ctx.artifacts_dir / "pilot_readiness_decision.json").read_text())
    assert readiness["status"] == "READY_FOR_FINAL_BENCHMARK_CONSTRUCTION"


def test_missing_corpus_blocked(tmp_path: Path) -> None:
    params = dict(_load_params())
    params["articles_path"] = str(tmp_path / "does_not_exist.jsonl")
    ctx = RunContext(
        cfg={"name": "test"},
        run_name="test",
        run_id="test",
        run_dir=tmp_path / "missing",
    )
    run_semiconductor_relevance_real_corpus_pilot_task(ctx, params, clock=lambda: FIXED_CLOCK)
    state = json.loads((ctx.artifacts_dir / "pilot_state.json").read_text())
    assert state["pilot_state"] == "BLOCKED_MISSING_CORPUS"
    assert state.get("pilot_execution_status") == "BLOCKED_MISSING_RIGHTS_CLEARED_CORPUS"

"""End-to-end offline semiconductor relevance benchmark integration test."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from kinetic.core.pipeline.context import RunContext
from kinetic.ml.relevance.benchmark_task import (
    build_semiconductor_relevance_benchmark_task,
)

FIXED_CLOCK = datetime(2026, 7, 17, 15, 0, 0, tzinfo=timezone.utc)

REQUIRED_ARTIFACTS = [
    "article_text_records.jsonl",
    "article_import_rejections.jsonl",
    "entity_matches.jsonl",
    "exact_duplicate_clusters.jsonl",
    "near_duplicate_candidates.csv",
    "dedupe_summary.json",
    "benchmark_candidates.jsonl",
    "benchmark_sampling_manifest.json",
    "annotation_batch.csv",
    "annotation_batch_manifest.json",
    "raw_annotations.jsonl",
    "annotation_disagreements.csv",
    "adjudicated_annotations.jsonl",
    "annotation_agreement.json",
    "annotation_quality_report.md",
    "benchmark_split_assignments.csv",
    "split_manifest.json",
    "split_contamination_report.json",
    "baseline_predictions.csv",
    "benchmark_metrics.json",
    "benchmark_report.md",
    "research_limitations.md",
    "benchmark_run_summary.json",
]


def _load_config() -> dict:
    path = Path(
        "projects/semiconductor_case_study/configs/"
        "semiconductor_relevance_benchmark_offline.yaml"
    )
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    return dict(cfg["pipeline"]["steps"][0]["params"])


def test_offline_benchmark_pipeline_deterministic(tmp_path: Path) -> None:
    params = _load_config()
    # Ensure no evaluate_holdout peeking without gate — config enables it explicitly.
    assert params["splits"].get("evaluate_holdout") == "ENABLE"

    def run_once(run_dir: Path) -> dict:
        ctx = RunContext(
            cfg={"name": "test"},
            run_name="test",
            run_id="test",
            run_dir=run_dir,
        )
        build_semiconductor_relevance_benchmark_task(ctx, params, clock=lambda: FIXED_CLOCK)
        return ctx

    ctx1 = run_once(tmp_path / "r1")
    ctx2 = run_once(tmp_path / "r2")

    for name in REQUIRED_ARTIFACTS:
        assert (ctx1.artifacts_dir / name).is_file(), name
        assert (ctx1.artifacts_dir / name).read_bytes() == (ctx2.artifacts_dir / name).read_bytes()

    # Annotation batch must not contain leakage fields.
    batch = (ctx1.artifacts_dir / "annotation_batch.csv").read_text(encoding="utf-8")
    header = batch.splitlines()[0].lower()
    for forbidden in ("baseline", "theme", "return", "prediction", "expected_label"):
        assert forbidden not in header

    summary = json.loads((ctx1.artifacts_dir / "benchmark_run_summary.json").read_text())
    assert summary["articles_accepted"] >= 20
    assert summary["articles_rejected"] >= 3
    assert summary["holdout_metrics_enabled"] is True

    sampling = json.loads((ctx1.artifacts_dir / "benchmark_sampling_manifest.json").read_text())
    metrics = json.loads((ctx1.artifacts_dir / "benchmark_metrics.json").read_text())
    cont = json.loads((ctx1.artifacts_dir / "split_contamination_report.json").read_text())
    assert cont["exact_cluster_cross_split_count"] == 0
    assert cont["ok"] is True
    assert sampling["actual_sample_count"] == sampling["actual_sample_count"]
    assert "A_naive_substring" in metrics["baselines"]
    assert "B_token_safe_entity" in metrics["baselines"]
    assert "C_strict_title_entity" in metrics["baselines"]

    report = (ctx1.artifacts_dir / "benchmark_report.md").read_text(encoding="utf-8")
    assert "does not implement" in report.lower() or "benchmark infrastructure" in report.lower()
    limitations = (ctx1.artifacts_dir / "research_limitations.md").read_text(encoding="utf-8")
    assert "No live article-content provider" in limitations
    assert "sentiment" in limitations.lower()

    # Holdout gate off => no holdout metrics block.
    params_gated = json.loads(json.dumps(params))
    params_gated["splits"]["evaluate_holdout"] = None
    ctx3 = RunContext(
        cfg={"name": "test"},
        run_name="test",
        run_id="test",
        run_dir=tmp_path / "r3",
    )
    build_semiconductor_relevance_benchmark_task(ctx3, params_gated, clock=lambda: FIXED_CLOCK)
    metrics_gated = json.loads((ctx3.artifacts_dir / "benchmark_metrics.json").read_text())
    for baseline in metrics_gated["baselines"].values():
        assert "holdout" not in baseline


def test_task_registered() -> None:
    from kinetic.bootstrap import build_default_registry

    registry = build_default_registry()
    assert "ml.relevance.build_benchmark" in registry.task_ids()

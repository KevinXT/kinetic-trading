"""Annotation validation, agreement, and metric tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from research_data.relevance.annotation import (
    analyze_agreement,
    linearly_weighted_cohen_kappa,
    percent_agreement,
)
from research_data.relevance.baselines import BaselinePrediction
from research_data.relevance.metrics import (
    ConfusionCounts,
    confusion_from_predictions,
    metrics_from_counts,
    wilson_interval,
)
from research_data.relevance.models import (
    AdjudicatedAnnotationV1,
    SemiconductorRelevanceAnnotationV1,
    binary_relevant,
)


def test_label_range_and_binary_conversion() -> None:
    assert binary_relevant(2) is True
    assert binary_relevant(1) is False
    with pytest.raises(ValueError):
        SemiconductorRelevanceAnnotationV1(
            article_id="a",
            duplicate_cluster_id="c",
            annotator_id="x",
            guideline_version="g",
            relevance_label=4,
            central_entity_ids=(),
            evidence_text=None,
            evidence_start=None,
            evidence_end=None,
            uncertain=False,
            uncertainty_reason=None,
            annotator_notes=None,
            annotated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            annotation_version="annotation-v1",
        )


def test_evidence_span_and_missing_not_zero() -> None:
    with pytest.raises(ValueError, match="evidence"):
        SemiconductorRelevanceAnnotationV1(
            article_id="a",
            duplicate_cluster_id="c",
            annotator_id="x",
            guideline_version="g",
            relevance_label=1,
            central_entity_ids=(),
            evidence_text="ab",
            evidence_start=0,
            evidence_end=1,
            uncertain=False,
            uncertainty_reason=None,
            annotator_notes=None,
            annotated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            annotation_version="annotation-v1",
        )


def test_adjudication_requires_provenance_for_large_disagreement() -> None:
    with pytest.raises(ValueError, match="large ordinal"):
        AdjudicatedAnnotationV1(
            article_id="a",
            duplicate_cluster_id="c",
            relevance_label=2,
            central_entity_ids=(),
            adjudicator_id="adj",
            adjudication_reason="short",
            guideline_version="g",
            annotation_version="annotation-v1",
            raw_annotator_ids=("a1", "a2"),
            raw_labels=(0, 3),
            adjudicated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


def test_agreement_hand_computed() -> None:
    pairs = [
        ("a", 0, 0, "x", "y"),
        ("b", 1, 2, "x", "y"),
        ("c", 3, 3, "x", "y"),
        ("d", 2, 2, "x", "y"),
    ]
    assert percent_agreement(pairs) == 0.75
    kappa = linearly_weighted_cohen_kappa(pairs)
    assert kappa["n_joint"] == 4
    assert kappa["warning"]  # small sample
    # Empty -> null kappa
    empty = linearly_weighted_cohen_kappa([])
    assert empty["kappa"] is None
    assert empty["reason"] == "no_joint_labels"


def test_metrics_known_confusion_matrices() -> None:
    perfect = metrics_from_counts(ConfusionCounts(tp=5, fp=0, tn=5, fn=0, abstentions=0))
    assert perfect["precision"] == 1.0
    assert perfect["recall"] == 1.0
    assert perfect["f1"] == 1.0

    all_fp = metrics_from_counts(ConfusionCounts(tp=0, fp=4, tn=0, fn=0, abstentions=0))
    assert all_fp["precision"] == 0.0
    assert all_fp["recall"] is None
    assert all_fp["recall_reason"] == "undefined_zero_denominator"

    all_fn = metrics_from_counts(ConfusionCounts(tp=0, fp=0, tn=0, fn=3, abstentions=0))
    assert all_fn["precision"] is None
    assert all_fn["recall"] == 0.0

    no_pred_pos = metrics_from_counts(ConfusionCounts(tp=0, fp=0, tn=2, fn=2, abstentions=0))
    assert no_pred_pos["precision"] is None

    abst = metrics_from_counts(ConfusionCounts(tp=1, fp=0, tn=1, fn=0, abstentions=2))
    assert abst["coverage"] == 0.5
    assert abst["abstention_rate"] == 0.5

    low, high = wilson_interval(5, 10)  # type: ignore[misc]
    assert 0.0 <= low < 0.5 < high <= 1.0


def test_confusion_from_predictions_respects_abstention() -> None:
    labels = {
        "a": AdjudicatedAnnotationV1(
            article_id="a",
            duplicate_cluster_id="c",
            relevance_label=3,
            central_entity_ids=(),
            adjudicator_id="adj",
            adjudication_reason="fixture_reason_ok",
            guideline_version="g",
            annotation_version="annotation-v1",
            raw_annotator_ids=("x",),
            raw_labels=(3,),
            adjudicated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    }
    preds = [
        BaselinePrediction(
            article_id="a",
            baseline_id="B",
            baseline_version="v",
            predicted_binary_relevant=None,
            abstained=True,
            matched_entity_ids=(),
            matched_aliases=(),
            match_fields=(),
            rule_explanation="abstain",
        )
    ]
    counts = confusion_from_predictions(preds, labels)
    assert counts.abstentions == 1
    assert counts.covered == 0


def test_analyze_agreement_fixture_file() -> None:
    from research_data.relevance.annotation import load_raw_annotations

    anns = load_raw_annotations("tests/fixtures/research/relevance_benchmark/raw_annotations.jsonl")
    # Raw labels immutable load
    assert anns
    report = analyze_agreement(anns)
    assert report["n_jointly_labeled_articles"] >= 1
    assert "confusion_matrix" in report

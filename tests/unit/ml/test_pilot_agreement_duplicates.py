"""Agreement mathematics and duplicate-pair calibration tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kinetic.ml.relevance.duplicate_calibration import (
    confusion_at_threshold,
    threshold_grid_metrics,
)
from kinetic.ml.relevance.pilot_agreement import (
    adjacent_agreement,
    analyze_pilot_agreement,
    bootstrap_weighted_kappa_ci,
    determinate_joint_pairs,
    exact_agreement,
    large_disagreement_rate,
)
from kinetic.ml.relevance.pilot_models import (
    DuplicatePairReviewV1,
    PilotRelevanceAnnotationV1,
    is_same_underlying_story,
)
from kinetic.ml.relevance.pilot_sampling import pair_score_bin, stable_pair_id

FIXED = datetime(2026, 7, 17, 16, 0, 0, tzinfo=timezone.utc)


def _ann(cluster: str, annotator: str, label: int) -> PilotRelevanceAnnotationV1:
    return PilotRelevanceAnnotationV1(
        article_id=f"art-{cluster}",
        duplicate_cluster_id=cluster,
        sample_roles=("calibration",),
        annotator_id=annotator,
        guideline_version="g1",
        relevance_label=label,
        cannot_determine=False,
        cannot_determine_reason=None,
        central_entity_ids=(),
        secondary_entity_ids=(),
        evidence_text=None,
        evidence_start=None,
        evidence_end=None,
        content_sufficient=True,
        uncertain=False,
        uncertainty_reason=None,
        decision_reason_code="TEST",
        annotator_notes=None,
        annotation_started_at=None,
        annotation_completed_at=FIXED,
    )


def test_agreement_hand_examples() -> None:
    anns = [
        _ann("c1", "a", 2),
        _ann("c1", "b", 2),
        _ann("c2", "a", 1),
        _ann("c2", "b", 2),
        _ann("c3", "a", 0),
        _ann("c3", "b", 3),
        _ann("c4", "a", 3),
        _ann("c4", "b", 3),
    ]
    pairs = determinate_joint_pairs(anns)
    assert len(pairs) == 4
    assert exact_agreement(pairs) == 0.5
    assert adjacent_agreement(pairs) == 0.75
    assert large_disagreement_rate(pairs) == 0.25
    report = analyze_pilot_agreement(anns, bootstrap_replicates=20)
    assert report["n_jointly_labeled_clusters"] == 4
    assert report["raw_exact_agreement"] == 0.5


def test_cannot_determine_not_label_zero() -> None:
    with pytest.raises(ValueError):
        PilotRelevanceAnnotationV1(
            article_id="a",
            duplicate_cluster_id="c",
            sample_roles=("formal",),
            annotator_id="a1",
            guideline_version="g",
            relevance_label=0,
            cannot_determine=True,
            cannot_determine_reason="BODY_UNAVAILABLE",
            central_entity_ids=(),
            secondary_entity_ids=(),
            evidence_text=None,
            evidence_start=None,
            evidence_end=None,
            content_sufficient=False,
            uncertain=False,
            uncertainty_reason=None,
            decision_reason_code="X",
            annotator_notes=None,
            annotation_started_at=None,
            annotation_completed_at=FIXED,
        )


def test_bootstrap_determinism_and_pairing() -> None:
    pairs = [("c1", 0, 0, "a", "b"), ("c2", 1, 2, "a", "b"), ("c3", 3, 3, "a", "b")] * 4
    a = bootstrap_weighted_kappa_ci(pairs, replicates=30, seed="s")
    b = bootstrap_weighted_kappa_ci(pairs, replicates=30, seed="s")
    assert a == b
    assert a["method"] == "cluster_bootstrap_paired_labels"


def test_same_underlying_story_mapping() -> None:
    assert is_same_underlying_story("SYNDICATED_SAME_STORY") is True
    assert is_same_underlying_story("RELATED_SAME_EVENT_DISTINCT_ARTICLE") is False
    assert is_same_underlying_story("CANNOT_DETERMINE") is None


def test_pair_id_order_independence() -> None:
    assert stable_pair_id("b", "a") == stable_pair_id("a", "b")
    assert pair_score_bin(0.69) == "[0.65,0.70)"
    assert pair_score_bin(0.70) == "[0.70,0.75)"


def test_duplicate_metrics_and_no_auto_threshold() -> None:
    reviews = [
        DuplicatePairReviewV1(
            pair_id="p1",
            article_id_a="a1",
            article_id_b="a2",
            exact_cluster_id_a="c1",
            exact_cluster_id_b="c2",
            title_similarity=0.9,
            body_similarity=0.85,
            publication_time_distance_hours=1.0,
            language_pair="en|en",
            domain_pair="d1|d2",
            score_bin="[0.80,0.90)",
            candidate_at_current_threshold=True,
            sampling_probability=0.5,
            sampling_weight=2.0,
            reviewer_id="r1",
            pair_label="REWRITTEN_SAME_STORY",
            uncertain=False,
            reason="same",
            reviewed_at=FIXED,
        ),
        DuplicatePairReviewV1(
            pair_id="p2",
            article_id_a="a3",
            article_id_b="a4",
            exact_cluster_id_a="c3",
            exact_cluster_id_b="c4",
            title_similarity=0.4,
            body_similarity=0.55,
            publication_time_distance_hours=2.0,
            language_pair="en|en",
            domain_pair="d3|d4",
            score_bin="[0.50,0.60)",
            candidate_at_current_threshold=False,
            sampling_probability=0.25,
            sampling_weight=4.0,
            reviewer_id="r1",
            pair_label="UNRELATED",
            uncertain=False,
            reason="diff",
            reviewed_at=FIXED,
        ),
        DuplicatePairReviewV1(
            pair_id="p3",
            article_id_a="a5",
            article_id_b="a6",
            exact_cluster_id_a="c5",
            exact_cluster_id_b="c6",
            title_similarity=0.2,
            body_similarity=0.68,
            publication_time_distance_hours=3.0,
            language_pair="en|en",
            domain_pair="d5|d6",
            score_bin="[0.65,0.70)",
            candidate_at_current_threshold=False,
            sampling_probability=0.25,
            sampling_weight=4.0,
            reviewer_id="r1",
            pair_label="REWRITTEN_SAME_STORY",
            uncertain=False,
            reason="missed",
            reviewed_at=FIXED,
        ),
    ]
    at70 = confusion_at_threshold(reviews, threshold=0.70, weighted=True)
    assert at70["candidate_precision"] == 1.0
    assert at70["fn"] > 0  # below-threshold same-story
    frame = [
        {
            "title_similarity": r.title_similarity,
            "body_similarity": r.body_similarity,
        }
        for r in reviews
    ]
    grid = threshold_grid_metrics(reviews, frame, grid=[0.60, 0.70, 0.80])
    counts = [row["corpus_candidate_pair_count"] for row in grid]
    assert counts == sorted(counts, reverse=True)
    assert all(row["recommendation_status"] == "HUMAN_REVIEW_REQUIRED" for row in grid)

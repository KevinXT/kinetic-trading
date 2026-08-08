"""Regression tests for duplicate-pair stratified inclusion probabilities."""

from __future__ import annotations

from collections import Counter

from kinetic.ml.relevance.pilot_sampling import (
    PairFrameRow,
    classify_pair_candidate_status,
    pair_dependence_diagnostics,
    sample_duplicate_pair_reviews,
)


def _row(
    pair_id: str,
    *,
    score: float,
    title: float | None,
    body: float | None,
    title_thr: float = 0.70,
    body_thr: float = 0.70,
    article_a: str | None = None,
    article_b: str | None = None,
) -> PairFrameRow:
    candidate, rule = classify_pair_candidate_status(
        title_similarity=title,
        body_similarity=body,
        title_threshold=title_thr,
        body_threshold=body_thr,
    )
    a = article_a or f"a_{pair_id}"
    b = article_b or f"b_{pair_id}"
    from kinetic.ml.relevance.pilot_sampling import pair_score_bin

    return PairFrameRow(
        pair_id=pair_id,
        article_id_a=a,
        article_id_b=b,
        exact_cluster_id_a=f"c_{a}",
        exact_cluster_id_b=f"c_{b}",
        title_similarity=title,
        body_similarity=body,
        max_similarity=score,
        publication_time_distance_hours=1.0,
        language_pair="en|en",
        domain_pair="d1|d2",
        score_bin=pair_score_bin(score),
        candidate_at_current_threshold=candidate,
        candidate_rule_category=rule,
    )


def test_mixed_candidate_non_candidate_same_bin_separate_strata() -> None:
    # Same score bin, different candidate status → separate strata.
    rows = [
        _row("p1", score=0.72, title=0.72, body=0.10),  # candidate title
        _row("p2", score=0.72, title=0.10, body=0.10),  # below
        _row("p3", score=0.72, title=0.10, body=0.75),  # candidate body
        _row("p4", score=0.72, title=0.80, body=0.80),  # both
        _row("p5", score=0.72, title=0.05, body=0.05),  # below
    ]
    assert {r.score_bin for r in rows} == {"[0.70,0.75)"}
    strata = {r.stratum_key() for r in rows}
    assert any(s.startswith("candidate|") for s in strata)
    assert any(s.startswith("non_candidate|") for s in strata)
    assert len(strata) >= 3  # title, body/both, below at minimum


def test_stored_pi_equals_selected_fraction_and_weight_inverse() -> None:
    rows = [_row(f"c{i}", score=0.82, title=0.82, body=0.20) for i in range(10)] + [
        _row(f"n{i}", score=0.22, title=0.10, body=0.10) for i in range(10)
    ]
    selected, manifest = sample_duplicate_pair_reviews(
        rows,
        candidate_target=4,
        below_threshold_target=4,
        sampling_seed="seed-pi",
    )
    by_stratum: dict[str, list[dict]] = {}
    for s in selected:
        by_stratum.setdefault(s["pair_review_stratum"], []).append(s)
    for h, group in by_stratum.items():
        N_h = manifest["stratum_population_sizes"][h]
        n_h = manifest["stratum_sample_sizes"][h]
        assert len(group) == n_h
        pi = n_h / N_h
        for row in group:
            assert abs(row["sampling_probability"] - pi) < 1e-12
            assert abs(row["sampling_weight"] - (1.0 / pi)) < 1e-12
            assert abs(row["sampling_weight"] - (N_h / n_h)) < 1e-12


def test_reordered_inputs_same_selection() -> None:
    rows = [_row(f"c{i}", score=0.82, title=0.82, body=0.20) for i in range(8)] + [
        _row(f"n{i}", score=0.22, title=0.10, body=0.10) for i in range(8)
    ]
    a, ma = sample_duplicate_pair_reviews(
        rows, candidate_target=3, below_threshold_target=3, sampling_seed="seed-ord"
    )
    b, mb = sample_duplicate_pair_reviews(
        list(reversed(rows)),
        candidate_target=3,
        below_threshold_target=3,
        sampling_seed="seed-ord",
    )
    assert [r["pair_id"] for r in a] == [r["pair_id"] for r in b]
    assert ma["inclusion_probability_by_stratum"] == mb["inclusion_probability_by_stratum"]


def test_candidate_counts_monotonic_as_threshold_lowered() -> None:
    scores = [0.55, 0.62, 0.68, 0.71, 0.78, 0.85]
    counts = []
    for thr in (0.80, 0.70, 0.60, 0.50):
        n = 0
        for i, s in enumerate(scores):
            cand, _ = classify_pair_candidate_status(
                title_similarity=s,
                body_similarity=None,
                title_threshold=thr,
                body_threshold=thr,
            )
            n += int(cand)
        counts.append(n)
    assert counts == sorted(counts)  # non-decreasing as threshold lowers


def test_below_threshold_audit_pairs_and_no_exact_cluster_internal() -> None:
    rows = [
        _row("below1", score=0.20, title=0.10, body=0.10),
        _row("cand1", score=0.90, title=0.90, body=0.90),
    ]
    selected, manifest = sample_duplicate_pair_reviews(
        rows, candidate_target=1, below_threshold_target=1, sampling_seed="seed-b"
    )
    assert any(not s["candidate_at_current_threshold"] for s in selected)
    assert any(s["candidate_rule_category"] == "BELOW_THRESHOLD_AUDIT" for s in selected)
    # Pair IDs are order-independent (stable_pair_id sorts article IDs).
    assert all(s["article_id_a"] <= s["article_id_b"] for s in selected)
    assert manifest["sampling_version"].startswith("pair-review-stratified")


def test_dependence_diagnostics_reported() -> None:
    rows = [
        _row("p1", score=0.9, title=0.9, body=0.9, article_a="A", article_b="B"),
        _row("p2", score=0.9, title=0.9, body=0.9, article_a="A", article_b="C"),
        _row("p3", score=0.2, title=0.1, body=0.1, article_a="D", article_b="E"),
    ]
    selected, manifest = sample_duplicate_pair_reviews(
        rows, candidate_target=2, below_threshold_target=1, sampling_seed="seed-d"
    )
    dep = manifest["dependence"]
    assert dep["reviewed_pair_count"] == len(selected)
    assert dep["unique_article_count"] >= 2
    assert dep["component_bootstrap_implemented"] is False
    assert "dependent" in dep["dependence_warning"].casefold()
    # Direct helper agrees.
    assert pair_dependence_diagnostics(selected)["connected_component_count"] >= 1


def test_pair_ids_order_independent() -> None:
    from kinetic.ml.relevance.pilot_sampling import stable_pair_id

    assert stable_pair_id("b", "a") == stable_pair_id("a", "b")


def test_stratum_keys_mutually_exclusive_coverage() -> None:
    rows = [
        _row("t", score=0.72, title=0.72, body=0.10),
        _row("b", score=0.72, title=0.10, body=0.75),
        _row("both", score=0.85, title=0.90, body=0.90),
        _row("below", score=0.20, title=0.10, body=0.10),
    ]
    keys = [r.stratum_key() for r in rows]
    assert len(keys) == len(set(keys))
    # Every pair belongs to exactly one stratum key.
    counts = Counter(keys)
    assert all(v == 1 for v in counts.values())

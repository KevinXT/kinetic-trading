#!/usr/bin/env python3
"""Generate synthetic relevance pilot fixtures for offline tests."""

from __future__ import annotations

import json
from pathlib import Path

from kinetic.data.catalog.entities import load_entity_references
from kinetic.data.schemas.news import ArticleTextRecordV1
from kinetic.ml.relevance.pilot_sampling import (
    build_pair_comparison_universe,
    sample_duplicate_pair_reviews,
)
from kinetic.processing.news.dedupe.exact import cluster_exact_duplicates
from kinetic.processing.news.dedupe.near import NearDuplicateConfig
from kinetic.processing.news.entity_linking import match_entities_many
from kinetic.processing.news.normalize import (
    NORMALIZER_VERSION,
    domain_from_url,
    normalize_text_for_hash,
    normalize_url,
    sha256_hex,
    stable_article_id,
)

OUT = Path("tests/fixtures/research/relevance_pilot")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    raw_rows = [
        {
            "title": "NVIDIA unveils next-generation AI GPU roadmap",
            "body": (
                "NVIDIA Corporation detailed data-center GPU plans for training large models."
            ),
            "domain": "example-tech.test",
            "date": "2025-06-02T14:00:00Z",
        },
        {
            "title": "Chip stocks rise as AMD gains after product refresh",
            "body": (
                "Advanced Micro Devices shares moved after a server CPU refresh announcement."
            ),
            "domain": "example-markets.test",
            "date": "2025-06-03T15:30:00Z",
        },
        {
            "title": "Business intelligence software market expands",
            "body": (
                "Vendors competed for analytics contracts. "
                "No semiconductor company is the focus."
            ),
            "domain": "example-soft.test",
            "date": "2025-06-04T10:00:00Z",
        },
        {
            "title": "Nasdaq falls as inflation data surprises",
            "body": (
                "Broader markets sold off. Taiwan Semiconductor Manufacturing was little "
                "changed in afternoon trade."
            ),
            "domain": "example-macro.test",
            "date": "2025-06-05T16:00:00Z",
        },
        {
            "title": "Global semiconductor equipment orders stabilize",
            "body": "Wafer fabrication tool demand steadied as foundry utilization improved.",
            "domain": "example-ind.test",
            "date": "2025-06-06T11:00:00Z",
        },
        {
            "title": "Micron Technology expands memory capacity plans",
            "body": "Micron Technology Inc. said it will increase DRAM output for AI servers.",
            "domain": "example-mem.test",
            "date": "2025-06-07T12:00:00Z",
        },
        {
            "title": "Samsung electronics supply update",
            "body": None,
            "description": "Brief wire headline only.",
            "domain": "example-wire.test",
            "date": "2025-06-08T09:00:00Z",
            "content_status": "metadata_only",
        },
        {
            "title": "TSMC and NVIDIA deepen capacity partnership",
            "body": (
                "Taiwan Semiconductor Manufacturing and NVIDIA coordinated advanced "
                "packaging capacity."
            ),
            "domain": "example-supply.test",
            "date": "2025-06-09T13:00:00Z",
        },
        {
            "title": "US export controls reshape chip tool shipments",
            "body": "Policy limits affected semiconductor equipment destined for certain fabs.",
            "domain": "example-policy.test",
            "date": "2025-06-10T14:00:00Z",
        },
        {
            "title": "New Arizona fab construction progresses",
            "body": (
                "Semiconductor fab construction milestones were reported for a major US site."
            ),
            "domain": "example-fab.test",
            "date": "2025-06-11T15:00:00Z",
        },
        {
            "title": "Broadcom reports quarterly earnings beat",
            "body": "Broadcom Inc. posted stronger semiconductor solutions revenue.",
            "domain": "example-earn.test",
            "date": "2025-06-12T21:00:00Z",
        },
        {
            "title": "City council approves downtown park renovation",
            "body": (
                "Local officials voted on landscaping contracts unrelated to technology firms."
            ),
            "domain": "example-local.test",
            "date": "2025-06-13T18:00:00Z",
        },
        {
            "title": "ASML ships high-NA lithography system",
            "body": (
                "ASML Holding delivered an extreme ultraviolet lithography tool to a "
                "leading foundry."
            ),
            "domain": "example-litho.test",
            "date": "2025-06-14T08:00:00Z",
        },
        {
            "title": "NVIDIA unveils next-generation AI GPU roadmap",
            "body": (
                "NVIDIA Corporation detailed data-center GPU plans for training large models."
            ),
            "domain": "example-syndicate.test",
            "date": "2025-06-02T14:05:00Z",
        },
        {
            "title": "NVIDIA outlines AI GPU roadmap for data centers",
            "body": (
                "NVIDIA Corporation detailed data center GPU plans for training large models "
                "with minor wording changes for syndication."
            ),
            "domain": "example-rewrite.test",
            "date": "2025-06-02T16:00:00Z",
        },
        {
            "title": "Galaxy phone launch draws crowds",
            "body": (
                "Samsung Electronics unveiled a consumer smartphone. Chip content is incidental."
            ),
            "domain": "example-consumer.test",
            "date": "2025-06-15T12:00:00Z",
        },
        {
            "title": "Rare gases supply tightens for chip plants",
            "body": ("Industrial gas shortages may affect semiconductor manufacturing indirectly."),
            "domain": "example-gas.test",
            "date": "2025-06-16T10:00:00Z",
        },
        {
            "title": "Intel expands foundry customer pipeline",
            "body": "Intel Corporation said new foundry customers committed to process nodes.",
            "domain": "example-intel.test",
            "date": "2025-06-17T11:00:00Z",
        },
        {
            "title": "SOX semiconductor index edges higher",
            "body": (
                "The Philadelphia semiconductor index rose. Constituents including NVIDIA "
                "and AMD were mixed."
            ),
            "domain": "example-index.test",
            "date": "2025-06-18T15:00:00Z",
        },
        {
            "title": "Researchers study AMD risk scores in cardiology",
            "body": (
                "Age-related macular degeneration (AMD) clinical scores were updated. "
                "Not Advanced Micro Devices."
            ),
            "domain": "example-med.test",
            "date": "2025-06-19T09:00:00Z",
        },
        {
            "title": "Qualcomm wins new smartphone modem design",
            "body": "Qualcomm Incorporated secured a multi-year modem design win.",
            "domain": "example-qcom.test",
            "date": "2025-06-20T10:00:00Z",
        },
        {
            "title": "Cloud providers raise capex guidance",
            "body": (
                "Hyperscalers cited accelerators. NVIDIA was mentioned once among many suppliers."
            ),
            "domain": "example-cloud.test",
            "date": "2025-06-21T11:00:00Z",
        },
        {
            "title": "Restricted wire item about chips",
            "body": "This synthetic record simulates unclear rights.",
            "domain": "example-restricted.test",
            "date": "2025-06-22T12:00:00Z",
            "rights": "rights_unclear",
        },
        {
            "title": "TSMC raises capital budget for advanced nodes",
            "body": (
                "Taiwan Semiconductor Manufacturing increased spending on process technology."
            ),
            "domain": "example-tsm2.test",
            "date": "2025-06-23T13:00:00Z",
        },
        {
            "title": "Chip equipment commentary lacks date",
            "body": ("Semiconductor tool makers commented on demand without a usable timestamp."),
            "domain": "example-undated.test",
            "date": None,
        },
    ]

    articles = []
    provenance = []
    for i, raw in enumerate(raw_rows):
        url = f"https://{raw['domain']}/articles/{i:04d}"
        norm = normalize_url(url)
        title = raw["title"]
        body = raw.get("body")
        desc = raw.get("description")
        published = raw["date"]
        content_status = raw.get("content_status") or ("available" if body else "metadata_only")
        title_h = sha256_hex(normalize_text_for_hash(title))
        body_h = sha256_hex(normalize_text_for_hash(body)) if body else None
        aid = stable_article_id(
            provider="synthetic_pilot",
            provider_record_id=f"p{i:04d}",
            normalized_url=norm,
            title_sha256=title_h,
        )
        articles.append(
            {
                "article_id": aid,
                "provider": "synthetic_pilot",
                "provider_record_id": f"p{i:04d}",
                "url": url,
                "normalized_url": norm,
                "canonical_url": None,
                "domain": domain_from_url(url),
                "language": "en",
                "source_country": "US",
                "title": title,
                "description": desc,
                "body": body,
                "published_at": published,
                "ingested_at": "2026-07-17T15:00:00Z",
                "retrieved_at": "2026-07-17T15:00:00Z" if body else None,
                "content_status": content_status,
                "content_source": "synthetic_fixture",
                "title_sha256": title_h,
                "body_sha256": body_h,
                "normalization_version": NORMALIZER_VERSION,
                "schema_version": "article-text-record-v1",
                "published_at_source": "fixture_datetime" if published else None,
                "published_timezone": "UTC" if published else None,
            }
        )
        rights = raw.get("rights", "user_supplied_authorized")
        research_ok = rights not in {"rights_unclear", "research_use_restricted"}
        storage_ok = rights not in {"rights_unclear"}
        redistrib = rights in {"open_license", "public_domain"}
        body_commit = rights != "rights_unclear"
        if rights == "rights_unclear":
            body_commit = False
            research_ok = False
            storage_ok = False
        provenance.append(
            {
                "article_id": aid,
                "content_origin": "synthetic_fixture",
                "provider_name": "kinetic_synthetic_pilot",
                "source_url": url,
                "acquisition_method": "local_fixture_generation",
                "acquired_at": "2026-07-17T15:00:00Z",
                "rights_status": rights,
                "license_name": "synthetic-test-only",
                "license_reference": "tests/fixtures/research/relevance_pilot/PROVENANCE.md",
                "storage_allowed": storage_ok,
                "research_use_allowed": research_ok,
                "redistribution_allowed": redistrib,
                "body_commit_allowed": body_commit,
                "retention_policy": "test_fixture_retain",
                "provenance_notes": "Synthetic; not a real news article.",
                "provenance_version": "article-content-provenance-v1",
            }
        )

    (OUT / "articles.jsonl").write_text(
        "\n".join(json.dumps(a, sort_keys=True) for a in articles) + "\n", encoding="utf-8"
    )
    (OUT / "provenance.jsonl").write_text(
        "\n".join(json.dumps(p, sort_keys=True) for p in provenance) + "\n", encoding="utf-8"
    )

    records = [ArticleTextRecordV1.from_dict(a) for a in articles]
    entities = load_entity_references(None)
    match_entities_many(records, entities)
    clusters = cluster_exact_duplicates(records)
    prov_by = {p["article_id"]: p for p in provenance}

    cals = sorted(clusters, key=lambda c: c.cluster_id)[:8]
    calib_anns = []
    label_plan = [3, 3, 0, 2, 2, 3, 1, 3]
    label_plan_b = [3, 2, 0, 2, 1, 3, 1, 3]
    for idx, cluster in enumerate(cals):
        for annotator, lab in (
            ("annotator_a", label_plan[idx]),
            ("annotator_b", label_plan_b[idx]),
        ):
            calib_anns.append(
                {
                    "article_id": cluster.canonical_article_id,
                    "duplicate_cluster_id": cluster.cluster_id,
                    "sample_roles": ["calibration"],
                    "annotator_id": annotator,
                    "guideline_version": "pilot-guidelines-v1-calibration",
                    "relevance_label": lab,
                    "cannot_determine": False,
                    "cannot_determine_reason": None,
                    "central_entity_ids": [],
                    "secondary_entity_ids": [],
                    "evidence_text": None,
                    "evidence_start": None,
                    "evidence_end": None,
                    "content_sufficient": True,
                    "uncertain": False,
                    "uncertainty_reason": None,
                    "decision_reason_code": "SYNTHETIC_FIXTURE",
                    "annotator_notes": None,
                    "annotation_started_at": None,
                    "annotation_completed_at": "2026-07-17T16:00:00Z",
                    "annotation_version": "pilot-relevance-annotation-v1",
                }
            )
    (OUT / "calibration_raw_annotations.jsonl").write_text(
        "\n".join(json.dumps(a, sort_keys=True) for a in calib_anns) + "\n", encoding="utf-8"
    )

    formal_anns = []
    adj = []
    eligible_idx = 0
    labels = [0, 1, 2, 3, 3, 2, 1, 0, 2, 3, 3, 0, 2, 1, 3, 2, 2, 3, 1, 0, 3, 2]
    for cluster in sorted(clusters, key=lambda c: c.cluster_id):
        art = next(r for r in records if r.article_id == cluster.canonical_article_id)
        if art.published_at is None:
            continue
        if prov_by[art.article_id]["rights_status"] == "rights_unclear":
            continue
        lab = labels[eligible_idx % len(labels)]
        lab_b = lab if eligible_idx % 5 else max(0, lab - 1)
        for annotator, label in (("annotator_a", lab), ("annotator_b", lab_b)):
            if annotator == "annotator_b" and eligible_idx >= 12:
                continue
            formal_anns.append(
                {
                    "article_id": cluster.canonical_article_id,
                    "duplicate_cluster_id": cluster.cluster_id,
                    "sample_roles": ["formal", "representative"],
                    "annotator_id": annotator,
                    "guideline_version": "pilot-guidelines-v1-formal",
                    "relevance_label": label,
                    "cannot_determine": False,
                    "cannot_determine_reason": None,
                    "central_entity_ids": [],
                    "secondary_entity_ids": [],
                    "evidence_text": None,
                    "evidence_start": None,
                    "evidence_end": None,
                    "content_sufficient": True,
                    "uncertain": False,
                    "uncertainty_reason": None,
                    "decision_reason_code": "SYNTHETIC_FIXTURE",
                    "annotator_notes": None,
                    "annotation_started_at": None,
                    "annotation_completed_at": "2026-07-17T17:00:00Z",
                    "annotation_version": "pilot-relevance-annotation-v1",
                }
            )
        adj.append(
            {
                "article_id": cluster.canonical_article_id,
                "duplicate_cluster_id": cluster.cluster_id,
                "relevance_label": lab,
                "central_entity_ids": [],
                "adjudicator_id": "adjudicator_1",
                "adjudication_reason": ("synthetic fixture adjudication for offline pilot tests"),
                "guideline_version": "pilot-guidelines-v1-formal",
                "annotation_version": "annotation-v1",
                "raw_annotator_ids": (
                    ["annotator_a", "annotator_b"] if eligible_idx < 12 else ["annotator_a"]
                ),
                "raw_labels": [lab, lab_b] if eligible_idx < 12 else [lab],
                "adjudicated_at": "2026-07-17T18:00:00Z",
                "schema_version": "adjudicated-annotation-v1",
            }
        )
        eligible_idx += 1

    (OUT / "formal_raw_annotations.jsonl").write_text(
        "\n".join(json.dumps(a, sort_keys=True) for a in formal_anns) + "\n", encoding="utf-8"
    )
    (OUT / "formal_adjudicated_annotations.jsonl").write_text(
        "\n".join(json.dumps(a, sort_keys=True) for a in adj) + "\n", encoding="utf-8"
    )
    (OUT / "calibration_decision.json").write_text(
        json.dumps(
            {
                "decision": "READY_FOR_FORMAL_PILOT",
                "reason": (
                    "Synthetic fixture: guidelines accepted for offline software "
                    "validation only."
                ),
                "guideline_version_after": "pilot-guidelines-v1-formal",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    pair_frame, _blocking = build_pair_comparison_universe(
        records,
        clusters,
        config=NearDuplicateConfig(max_publication_distance_hours=720),
        current_title_threshold=0.8,
        current_body_threshold=0.7,
    )
    batch, _manifest = sample_duplicate_pair_reviews(
        pair_frame,
        candidate_target=10,
        below_threshold_target=10,
        sampling_seed="pilot-fixture",
    )
    reviews = []
    for row in batch:
        sim = row.get("max_similarity") or 0
        if sim >= 0.7:
            label = "REWRITTEN_SAME_STORY"
        elif sim >= 0.5:
            label = "RELATED_TOPIC_DISTINCT_EVENT"
        else:
            label = "UNRELATED"
        reviews.append(
            {
                "pair_id": row["pair_id"],
                "article_id_a": row["article_id_a"],
                "article_id_b": row["article_id_b"],
                "exact_cluster_id_a": row["exact_cluster_id_a"],
                "exact_cluster_id_b": row["exact_cluster_id_b"],
                "title_similarity": row["title_similarity"],
                "body_similarity": row["body_similarity"],
                "publication_time_distance_hours": row["publication_time_distance_hours"],
                "language_pair": row["language_pair"],
                "domain_pair": row["domain_pair"],
                "score_bin": row["score_bin"],
                "candidate_at_current_threshold": row["candidate_at_current_threshold"],
                "sampling_probability": row["sampling_probability"],
                "sampling_weight": row["sampling_weight"],
                "reviewer_id": "reviewer_1",
                "pair_label": label,
                "uncertain": False,
                "reason": "synthetic_fixture_pair_label",
                "reviewed_at": "2026-07-17T19:00:00Z",
                "review_version": "duplicate-pair-review-v1",
            }
        )
    (OUT / "duplicate_pair_reviews.jsonl").write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in reviews) + "\n", encoding="utf-8"
    )
    (OUT / "PROVENANCE.md").write_text(
        "# Relevance pilot fixtures\n\n"
        "All article bodies, provenance rows, and labels in this directory are **synthetic** "
        "test fixtures for offline unit/integration tests. They are not copyrighted news "
        "articles and are not production human labels. They must never be reported as real "
        "pilot findings.\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {OUT}: articles={len(articles)} clusters={len(clusters)} "
        f"eligible_labeled={eligible_idx} reviews={len(reviews)}"
    )


if __name__ == "__main__":
    main()

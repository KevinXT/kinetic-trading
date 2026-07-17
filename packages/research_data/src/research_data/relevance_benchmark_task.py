"""Pipeline task: build_semiconductor_relevance_benchmark.

Offline, deterministic foundation for article-level semiconductor relevance
evaluation. No network, credentials, GPU, models, or trading.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from news_data.article.corpus import LocalArticleCorpusProvider
from news_data.article.models import ArticleTextRecordV1
from news_data.dedupe.exact import cluster_exact_duplicates
from news_data.dedupe.near import NearDuplicateConfig, generate_near_duplicate_candidates
from news_data.entity.matching import match_entities_many
from news_data.entity.reference import load_entity_references

from research_data.relevance.annotation import (
    analyze_agreement,
    disagreement_rows,
    load_adjudicated_annotations,
    load_raw_annotations,
    render_annotation_quality_report,
)
from research_data.relevance.baselines import run_all_baselines
from research_data.relevance.metrics import evaluate_baselines
from research_data.relevance.report import LIMITATIONS, render_benchmark_report
from research_data.relevance.sampling import SamplingConfig, sample_benchmark_candidates
from research_data.relevance.splits import (
    SplitConfig,
    assign_splits,
    contamination_report,
)


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fieldnames})


def _annotation_batch_rows(
    candidates: Sequence[Any],
    articles: Sequence[ArticleTextRecordV1],
) -> list[dict[str, Any]]:
    by_id = {a.article_id: a for a in articles}
    rows: list[dict[str, Any]] = []
    for cand in candidates:
        art = by_id[cand.article_id]
        rows.append(
            {
                "article_id": art.article_id,
                "duplicate_cluster_id": cand.duplicate_cluster_id,
                "title": art.title,
                "description": art.description or "",
                "body": art.body or "",
                "content_status": art.content_status,
                "domain": art.domain,
                "language": art.language or "",
                "published_at": cand.published_at or "",
                "url": art.url,
                "double_annotate": "yes" if cand.double_annotate else "no",
                "guideline_version": "semiconductor-relevance-guidelines-v1",
                # Intentionally omitted: baselines, theme scores, expected labels, returns.
            }
        )
    rows.sort(key=lambda r: r["article_id"])
    return rows


def build_semiconductor_relevance_benchmark_task(
    ctx: Any,
    params: dict[str, Any],
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:
    now = clock()
    if now.tzinfo is None:
        raise ValueError("clock must return a timezone-aware datetime")

    corpus_path = params.get("articles_path") or params.get("corpus_path")
    if not corpus_path:
        raise ValueError(
            "build_semiconductor_relevance_benchmark requires 'articles_path' "
            "(local JSONL corpus)"
        )

    provider = LocalArticleCorpusProvider(
        path=str(corpus_path),
        default_content_source=str(params.get("default_content_source", "synthetic_fixture")),
        ingested_at=now,
        provenance={"task": "build_semiconductor_relevance_benchmark"},
    )
    imported = provider.import_articles()
    records = imported.records
    ctx.state["article_text_records"] = [r.to_dict() for r in records]
    ctx.write_jsonl("article_text_records.jsonl", (r.to_dict() for r in records))
    ctx.write_jsonl(
        "article_import_rejections.jsonl",
        (rej.to_dict() for rej in imported.rejections),
    )

    entities = load_entity_references(params.get("entity_reference_path"))
    ctx.write_jsonl("entity_references.jsonl", (e.to_dict() for e in entities))

    matches = match_entities_many(records, entities)
    ctx.state["entity_matches"] = [m.to_dict() for m in matches]
    ctx.write_jsonl("entity_matches.jsonl", (m.to_dict() for m in matches))

    clusters = cluster_exact_duplicates(records)
    ctx.write_jsonl("exact_duplicate_clusters.jsonl", (c.to_dict() for c in clusters))

    near_cfg = NearDuplicateConfig(
        title_similarity_threshold=float(params.get("near_title_threshold", 0.8)),
        body_similarity_threshold=float(params.get("near_body_threshold", 0.7)),
        max_publication_distance_hours=(
            float(params["near_max_publication_distance_hours"])
            if params.get("near_max_publication_distance_hours") is not None
            else 72.0
        ),
        shingle_size=int(params.get("near_shingle_size", 3)),
    )
    near = generate_near_duplicate_candidates(records, clusters, config=near_cfg)
    near_rows = [c.to_dict() for c in near]
    _write_csv(
        ctx.artifacts_dir / "near_duplicate_candidates.csv",
        near_rows,
        [
            "article_id_a",
            "article_id_b",
            "title_similarity",
            "body_shingle_similarity",
            "publication_time_distance_seconds",
            "domain_a",
            "domain_b",
            "candidate_reason",
            "similarity_config_version",
            "human_review_status",
            "cluster_id_a",
            "cluster_id_b",
        ],
    )
    multi = sum(1 for c in clusters if len(c.member_article_ids) > 1)
    ctx.write_json(
        "dedupe_summary.json",
        {
            "exact_dedupe_version": clusters[0].exact_dedupe_version if clusters else None,
            "near_dedupe_version": near_cfg.version,
            "input_articles": len(records),
            "exact_cluster_count": len(clusters),
            "exact_multi_member_clusters": multi,
            "near_duplicate_candidate_pairs": len(near),
            "near_title_threshold": near_cfg.title_similarity_threshold,
            "near_body_threshold": near_cfg.body_similarity_threshold,
            "notes": [
                "Near-duplicate threshold is a candidate-generation engineering choice.",
                "Near duplicates are not auto-merged.",
            ],
        },
    )

    sampling_cfg = SamplingConfig(
        sample_size=int(params.get("sample_size", 50)),
        sampling_seed=str(params.get("sampling_seed", "semiconductor-relevance-benchmark-v1")),
        max_per_entity=int(params.get("max_per_entity", 8)),
        double_annotation_fraction=float(params.get("double_annotation_fraction", 0.2)),
    )
    candidates, sampling_manifest = sample_benchmark_candidates(
        records,
        clusters,
        matches,
        near,
        entities,
        config=sampling_cfg,
    )
    ctx.write_jsonl("benchmark_candidates.jsonl", (c.to_dict() for c in candidates))
    ctx.write_json("benchmark_sampling_manifest.json", sampling_manifest)

    batch_rows = _annotation_batch_rows(candidates, records)
    _write_csv(
        ctx.artifacts_dir / "annotation_batch.csv",
        batch_rows,
        [
            "article_id",
            "duplicate_cluster_id",
            "title",
            "description",
            "body",
            "content_status",
            "domain",
            "language",
            "published_at",
            "url",
            "double_annotate",
            "guideline_version",
        ],
    )
    ctx.write_json(
        "annotation_batch_manifest.json",
        {
            "batch_size": len(batch_rows),
            "double_annotation_count": sum(1 for r in batch_rows if r["double_annotate"] == "yes"),
            "guideline_version": "semiconductor-relevance-guidelines-v1",
            "blind": True,
            "excluded_fields": [
                "baseline_predictions",
                "theme_ranking_scores",
                "model_predictions",
                "expected_labels",
                "market_returns",
            ],
            "note": (
                "double_annotation_fraction is an operational convenience, "
                "not a universal statistical threshold."
            ),
        },
    )

    split_params = params.get("splits") or {}
    if not split_params:
        raise ValueError("build_semiconductor_relevance_benchmark requires 'splits' config")
    split_cfg = SplitConfig.from_mapping(split_params)
    assignments, split_manifest = assign_splits(candidates, records, clusters, config=split_cfg)
    _write_csv(
        ctx.artifacts_dir / "benchmark_split_assignments.csv",
        assignments,
        [
            "article_id",
            "duplicate_cluster_id",
            "split",
            "cluster_published_date",
            "is_sample_representative",
            "split_version",
        ],
    )
    ctx.write_json("split_manifest.json", split_manifest)

    raw_annotations = []
    adjudicated = []
    agreement = None
    if params.get("raw_annotations_path"):
        raw_annotations = load_raw_annotations(str(params["raw_annotations_path"]))
        ctx.write_jsonl("raw_annotations.jsonl", (a.to_dict() for a in raw_annotations))
        disagreements = disagreement_rows(raw_annotations)
        _write_csv(
            ctx.artifacts_dir / "annotation_disagreements.csv",
            disagreements,
            [
                "article_id",
                "annotator_a",
                "label_a",
                "annotator_b",
                "label_b",
                "absolute_distance",
            ],
        )
        agreement = analyze_agreement(raw_annotations)
        ctx.write_json("annotation_agreement.json", agreement)
        (ctx.artifacts_dir / "annotation_quality_report.md").write_text(
            render_annotation_quality_report(agreement), encoding="utf-8"
        )

    if params.get("adjudicated_annotations_path"):
        adjudicated = load_adjudicated_annotations(str(params["adjudicated_annotations_path"]))
        ctx.write_jsonl("adjudicated_annotations.jsonl", (a.to_dict() for a in adjudicated))

    contamination = contamination_report(
        assignments,
        clusters,
        near,
        config=split_cfg,
        labels=adjudicated or None,
        articles=records,
    )
    ctx.write_json("split_contamination_report.json", contamination)

    # Baselines on sampled representative articles (and any labeled ids).
    sample_ids = {c.article_id for c in candidates}
    sample_articles = [a for a in records if a.article_id in sample_ids]
    sample_matches = [m for m in matches if m.article_id in sample_ids]
    predictions = run_all_baselines(sample_articles, entities, sample_matches)
    pred_rows = []
    for p in predictions:
        row = p.to_dict()
        row["matched_entity_ids"] = "|".join(row["matched_entity_ids"])
        row["matched_aliases"] = "|".join(row["matched_aliases"])
        row["match_fields"] = "|".join(row["match_fields"])
        pred_rows.append(row)
    _write_csv(
        ctx.artifacts_dir / "baseline_predictions.csv",
        pred_rows,
        [
            "article_id",
            "baseline_id",
            "baseline_version",
            "predicted_binary_relevant",
            "abstained",
            "matched_entity_ids",
            "matched_aliases",
            "match_fields",
            "rule_explanation",
        ],
    )

    metrics = None
    if adjudicated:
        cluster_counts: dict[str, int] = {}
        for row in assignments:
            if row.get("is_sample_representative"):
                cluster_counts[row["split"]] = cluster_counts.get(row["split"], 0) + 1
        metrics = evaluate_baselines(
            predictions,
            adjudicated,
            assignments,
            holdout_enabled=split_cfg.holdout_enabled,
            cluster_count_by_split=cluster_counts,
        )
        ctx.write_json("benchmark_metrics.json", metrics)

    report = render_benchmark_report(
        title=str(params.get("report_title", "Semiconductor relevance benchmark")),
        sampling_manifest=sampling_manifest,
        split_manifest=split_manifest,
        contamination=contamination,
        metrics=metrics,
        agreement=agreement,
        generated_at=now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    (ctx.artifacts_dir / "benchmark_report.md").write_text(report, encoding="utf-8")
    (ctx.artifacts_dir / "research_limitations.md").write_text(LIMITATIONS, encoding="utf-8")

    ctx.write_json(
        "benchmark_run_summary.json",
        {
            "articles_accepted": len(records),
            "articles_rejected": len(imported.rejections),
            "entity_count": len(entities),
            "entity_matches": len(matches),
            "exact_clusters": len(clusters),
            "near_candidates": len(near),
            "sample_size": len(candidates),
            "raw_annotations": len(raw_annotations),
            "adjudicated_annotations": len(adjudicated),
            "holdout_metrics_enabled": split_cfg.holdout_enabled,
            "generated_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    )

"""Pipeline task: run_semiconductor_relevance_real_corpus_pilot.

Rights-aware real-corpus annotation pilot with typed state machine.
No network, credentials, GPU, models, sentiment, or trading.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

import yaml
from news_data.article.corpus import LocalArticleCorpusProvider
from news_data.article.models import ArticleTextRecordV1
from news_data.dedupe.exact import cluster_exact_duplicates
from news_data.dedupe.near import NearDuplicateConfig, generate_near_duplicate_candidates
from news_data.entity.matching import match_entities_many
from news_data.entity.reference import load_entity_references

from research_data.relevance.baselines import run_all_baselines
from research_data.relevance.content_controls import (
    RealContentControlsV1,
    annotation_batch_may_include_body,
    assert_path_safe_for_real_content,
    redact_body_for_artifact,
)
from research_data.relevance.duplicate_calibration import (
    DEFAULT_THRESHOLD_GRID,
    load_duplicate_pair_reviews,
    render_duplicate_threshold_report,
    threshold_grid_metrics,
)
from research_data.relevance.eligibility import (
    PilotCorpusEligibilityV1,
    evaluate_corpus_eligibility,
    render_rights_validation_report,
)
from research_data.relevance.pilot_agreement import (
    analyze_pilot_agreement,
    load_pilot_annotations,
)
from research_data.relevance.pilot_metrics import (
    design_weighted_prevalence,
    evaluate_pilot_baselines,
)
from research_data.relevance.pilot_models import PILOT_STATES, SampledUnitV1
from research_data.relevance.pilot_planning import (
    plan_from_config,
    render_design_assumptions_md,
)
from research_data.relevance.pilot_report import (
    PILOT_LIMITATIONS,
    build_readiness_decision,
    render_quantitative_report,
)
from research_data.relevance.pilot_sampling import (
    RepresentativeSampleConfig,
    assign_double_annotation,
    build_pair_comparison_universe,
    build_sampling_frame,
    merge_annotation_units,
    sample_calibration_units,
    sample_challenge_purposive,
    sample_duplicate_pair_reviews,
    sample_representative_probability,
)
from research_data.relevance.provenance import load_provenance_jsonl
from research_data.relevance.reconciliation import reconcile_pilot_counts
from research_data.relevance.sampling import _industry_phrase_hit


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fieldnames})


def _emit_state(
    ctx: Any,
    state: str,
    *,
    missing: Optional[Sequence[str]] = None,
    extras: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    if state not in PILOT_STATES:
        raise ValueError(f"unknown pilot state {state!r}")
    payload = {
        "pilot_state": state,
        "missing_requirements": list(missing or []),
        "generated_at": extras.get("generated_at") if extras else None,
    }
    if extras:
        payload.update(dict(extras))
    ctx.write_json("pilot_state.json", payload)
    return payload


def _annotation_batch_rows(
    units: Sequence[SampledUnitV1],
    articles: Sequence[ArticleTextRecordV1],
    *,
    guideline_version: str,
    controls: RealContentControlsV1,
    output_path: Path,
    provenance_body_commit: Mapping[str, bool],
    hide_challenge_reasons: bool = True,
) -> list[dict[str, Any]]:
    by_id = {a.article_id: a for a in articles}
    rows: list[dict[str, Any]] = []
    for unit in units:
        art = by_id[unit.article_id]
        include_body = annotation_batch_may_include_body(
            content_source=art.content_source,
            controls=controls,
            output_path=output_path,
            body_commit_allowed=provenance_body_commit.get(art.article_id, False)
            or art.content_source == controls.synthetic_content_source,
        )
        row = {
            "article_id": art.article_id,
            "duplicate_cluster_id": unit.cluster_id,
            "title": art.title,
            "description": art.description or "",
            "body": (art.body or "") if include_body else "",
            "content_status": art.content_status,
            "domain": art.domain,
            "language": art.language or "",
            "published_at": (
                art.published_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                if art.published_at
                else ""
            ),
            "url": art.url,
            "double_annotate": "yes" if unit.double_annotate else "no",
            "guideline_version": guideline_version,
            # Blind controls: no baselines, roles (when possible), weights, challenge reasons.
            "sample_roles_hidden": "yes",
            "challenge_reason_codes_hidden": (
                "" if hide_challenge_reasons else "|".join(unit.challenge_reason_codes)
            ),
        }
        rows.append(row)
    rows.sort(key=lambda r: r["article_id"])
    return rows


def run_semiconductor_relevance_real_corpus_pilot_task(
    ctx: Any,
    params: dict[str, Any],
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:
    now = clock()
    if now.tzinfo is None:
        raise ValueError("clock must return a timezone-aware datetime")
    generated_at = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    controls = RealContentControlsV1.from_mapping(params.get("real_content_controls"))
    ctx.write_json("real_content_controls.json", controls.to_dict())

    corpus_path = params.get("articles_path") or params.get("corpus_path")
    if not corpus_path or not Path(str(corpus_path)).is_file():
        _emit_state(
            ctx,
            "BLOCKED_MISSING_CORPUS",
            missing=["articles_path"],
            extras={
                "generated_at": generated_at,
                "pilot_execution_status": "BLOCKED_MISSING_RIGHTS_CLEARED_CORPUS",
            },
        )
        ctx.write_json(
            "pilot_readiness_decision.json",
            build_readiness_decision(
                evidence={"corpus": "missing"},
                human_approval=params.get("pilot_human_approval"),
            ),
        )
        (ctx.artifacts_dir / "pilot_limitations.md").write_text(PILOT_LIMITATIONS, encoding="utf-8")
        return

    provenance_path = params.get("provenance_path")
    if not provenance_path or not Path(str(provenance_path)).is_file():
        _emit_state(
            ctx,
            "BLOCKED_RIGHTS_VALIDATION",
            missing=["provenance_path"],
            extras={
                "generated_at": generated_at,
                "pilot_execution_status": "BLOCKED_MISSING_RIGHTS_CLEARED_CORPUS",
            },
        )
        ctx.write_json(
            "real_corpus_preflight_summary.json",
            {
                "status": "BLOCKED_RIGHTS_VALIDATION",
                "reason": "missing provenance records",
            },
        )
        return

    _emit_state(ctx, "CORPUS_PREFLIGHT", extras={"generated_at": generated_at})

    provider = LocalArticleCorpusProvider(
        path=str(corpus_path),
        default_content_source=str(params.get("default_content_source", "local_corpus")),
        ingested_at=now,
        provenance={"task": "run_semiconductor_relevance_real_corpus_pilot"},
    )
    imported = provider.import_articles()
    records = imported.records

    # Redact bodies from git-tracked artifact risk: run artifacts may omit bodies.
    safe_records = []
    for r in records:
        body = redact_body_for_artifact(
            r.body,
            content_source=r.content_source,
            controls=controls,
            body_commit_allowed=(r.content_source == controls.synthetic_content_source),
        )
        d = r.to_dict()
        if (
            body is None
            and r.body is not None
            and r.content_source != controls.synthetic_content_source
        ):
            d["body"] = None
            d["body_redacted_in_artifact"] = True
        safe_records.append(d)
    assert_path_safe_for_real_content(
        ctx.artifacts_dir / "article_text_records.jsonl",
        controls=controls,
        contains_real_body=any(
            r.content_source != controls.synthetic_content_source and r.body for r in records
        )
        and controls.allow_full_body_in_run_artifacts,
    )
    ctx.write_jsonl("article_text_records.jsonl", safe_records)
    ctx.write_jsonl(
        "article_import_rejections.jsonl",
        (rej.to_dict() for rej in imported.rejections),
    )

    provenance_rows = load_provenance_jsonl(str(provenance_path))
    provenance_by_id = {p.article_id: p for p in provenance_rows}
    ctx.write_jsonl("article_content_provenance.jsonl", (p.to_dict() for p in provenance_rows))

    # Rights completeness: every article needs provenance for eligibility.
    missing_prov = [a.article_id for a in records if a.article_id not in provenance_by_id]
    if missing_prov and bool(params.get("require_provenance_for_all", True)):
        # Continue with eligibility marking missing provenance — do not invent rights.
        pass

    entities = load_entity_references(params.get("entity_reference_path"))
    matches = match_entities_many(records, entities)
    ctx.write_jsonl("entity_matches.jsonl", (m.to_dict() for m in matches))

    clusters = cluster_exact_duplicates(records)
    ctx.write_jsonl("exact_duplicate_clusters.jsonl", (c.to_dict() for c in clusters))

    evidence_ids = {m.article_id for m in matches}
    for a in records:
        if _industry_phrase_hit(a):
            evidence_ids.add(a.article_id)

    rules = PilotCorpusEligibilityV1.from_mapping(params.get("eligibility"))
    ctx.write_json("eligibility_rules.json", rules.to_dict())
    decisions, flow_counts, _eligible_canonical = evaluate_corpus_eligibility(
        records,
        provenance_by_id,
        clusters,
        rules=rules,
        schema_rejected=len(imported.rejections),
        entity_or_industry_ids=evidence_ids,
    )
    ctx.write_jsonl("corpus_eligibility_decisions.jsonl", (d.to_dict() for d in decisions))
    ctx.write_json("corpus_exclusion_summary.json", flow_counts.to_dict())
    (ctx.artifacts_dir / "rights_validation_report.md").write_text(
        render_rights_validation_report(decisions, flow_counts), encoding="utf-8"
    )

    if flow_counts.sampling_frame_units == 0:
        _emit_state(
            ctx,
            "BLOCKED_RIGHTS_VALIDATION",
            missing=["eligible_sampling_frame"],
            extras={
                "generated_at": generated_at,
                "pilot_execution_status": "BLOCKED_MISSING_RIGHTS_CLEARED_CORPUS",
            },
        )
        ctx.write_json(
            "real_corpus_preflight_summary.json",
            {
                "status": "BLOCKED_MISSING_RIGHTS_CLEARED_CORPUS",
                "flow": flow_counts.to_dict(),
            },
        )
        ctx.write_json(
            "pilot_readiness_decision.json",
            build_readiness_decision(
                evidence={"rights_complete": False, "eligible_frame": False},
                human_approval=params.get("pilot_human_approval"),
            ),
        )
        (ctx.artifacts_dir / "pilot_limitations.md").write_text(PILOT_LIMITATIONS, encoding="utf-8")
        return

    near_cfg = NearDuplicateConfig(
        title_similarity_threshold=float(params.get("near_title_threshold", 0.8)),
        body_similarity_threshold=float(params.get("near_body_threshold", 0.7)),
        max_publication_distance_hours=float(
            params.get("near_max_publication_distance_hours", 168)
        ),
        shingle_size=int(params.get("near_shingle_size", 3)),
    )
    near = generate_near_duplicate_candidates(records, clusters, config=near_cfg)
    near_ids = {p.article_id_a for p in near} | {p.article_id_b for p in near}

    frame = build_sampling_frame(
        records, clusters, matches, decisions, provenance_by_id, near_ids=near_ids
    )
    ctx.write_jsonl("pilot_sampling_frame.jsonl", (u.to_dict() for u in frame))

    plan = plan_from_config(params, eligible_cluster_population_size=len(frame))
    ctx.write_json("pilot_sample_size_plan.json", plan.to_dict())
    _write_csv(
        ctx.artifacts_dir / "pilot_sample_size_scenarios.csv",
        list(plan.scenario_rows) + list(plan.required_representative_by_prevalence),
        sorted(
            {
                k
                for row in list(plan.scenario_rows)
                + list(plan.required_representative_by_prevalence)
                for k in row
            }
        ),
    )
    (ctx.artifacts_dir / "pilot_design_assumptions.md").write_text(
        render_design_assumptions_md(plan), encoding="utf-8"
    )

    sampling_seed = str(params.get("sampling_seed", "semiconductor-relevance-pilot-v1"))
    # For synthetic offline tests / small corpora: allow override of operational target.
    rep_target = int(
        params.get("representative_sample_size_override") or plan.operational_target_representative
    )
    if plan.underfilled and params.get("representative_sample_size_override") is None:
        # Do not quietly reduce — use available N but mark underfilled in state/artifacts.
        rep_target = len(frame)

    rep_units, rep_manifest = sample_representative_probability(
        frame,
        config=RepresentativeSampleConfig(target_n=rep_target, sampling_seed=sampling_seed),
    )
    if plan.underfilled:
        rep_manifest["underfilled"] = True
        rep_manifest["planned_operational_target"] = plan.operational_target_representative
    ctx.write_jsonl("representative_sample.jsonl", (u.to_dict() for u in rep_units))
    ctx.write_json("representative_sample_manifest.json", rep_manifest)

    chal_target = int(
        (params.get("pilot_design") or {})
        .get("challenge_sample", {})
        .get("target_count", plan.challenge_target_count)
    )
    if params.get("challenge_sample_size_override") is not None:
        chal_target = int(params["challenge_sample_size_override"])
    chal_units, chal_manifest = sample_challenge_purposive(
        frame, target_n=chal_target, sampling_seed=sampling_seed
    )
    ctx.write_jsonl("challenge_sample.jsonl", (u.to_dict() for u in chal_units))
    ctx.write_json("challenge_sample_manifest.json", chal_manifest)

    merged_units, overlap_report = merge_annotation_units(rep_units, chal_units)
    ctx.write_json("sample_overlap_report.json", overlap_report)

    calib_target = int(
        (params.get("pilot_design") or {})
        .get("annotation_calibration", {})
        .get("target_count", plan.planned_calibration_round_size)
    )
    if params.get("calibration_sample_size_override") is not None:
        calib_target = int(params["calibration_sample_size_override"])
    calib_units = sample_calibration_units(
        frame, target_n=calib_target, sampling_seed=sampling_seed
    )

    # Pair universe (Sample C)
    pair_frame, blocking = build_pair_comparison_universe(
        records,
        clusters,
        config=near_cfg,
        current_title_threshold=near_cfg.title_similarity_threshold,
        current_body_threshold=near_cfg.body_similarity_threshold,
        max_pairs=params.get("max_pair_universe"),
    )
    ctx.write_jsonl("duplicate_pair_sampling_frame.jsonl", (p.to_dict() for p in pair_frame))
    pair_batch, pair_manifest = sample_duplicate_pair_reviews(
        pair_frame,
        candidate_target=int(
            params.get("duplicate_candidate_target_override")
            or plan.duplicate_candidate_target_count
        ),
        below_threshold_target=int(
            params.get("duplicate_below_threshold_override")
            or plan.duplicate_below_threshold_audit_target_count
        ),
        sampling_seed=sampling_seed,
    )
    _write_csv(
        ctx.artifacts_dir / "duplicate_pair_review_batch.csv",
        pair_batch,
        [
            "pair_id",
            "article_id_a",
            "article_id_b",
            "exact_cluster_id_a",
            "exact_cluster_id_b",
            "title_similarity",
            "body_similarity",
            "max_similarity",
            "score_bin",
            "candidate_at_current_threshold",
            "sampling_probability",
            "sampling_weight",
            "language_pair",
            "domain_pair",
            "publication_time_distance_hours",
        ],
    )
    ctx.write_json("duplicate_pair_review_manifest.json", {**pair_manifest, "blocking": blocking})

    _emit_state(
        ctx,
        "SAMPLE_PLAN_READY",
        extras={
            "generated_at": generated_at,
            "underfilled": plan.underfilled,
            "frame_units": len(frame),
        },
    )
    ctx.write_json(
        "real_corpus_preflight_summary.json",
        {
            "status": "SAMPLE_PLAN_READY",
            "articles_accepted": len(records),
            "articles_rejected": len(imported.rejections),
            "flow": flow_counts.to_dict(),
            "frame_units": len(frame),
            "underfilled": plan.underfilled,
            "is_synthetic_fixture": all(
                r.content_source == controls.synthetic_content_source for r in records
            ),
        },
    )

    # Calibration batch
    guideline_calib = str(
        params.get("calibration_guideline_version", "pilot-guidelines-v1-calibration")
    )
    body_commit = {
        a.article_id: (
            provenance_by_id[a.article_id].body_commit_allowed
            if a.article_id in provenance_by_id
            else a.content_source == controls.synthetic_content_source
        )
        for a in records
    }
    calib_path = ctx.artifacts_dir / "calibration_annotation_batch.csv"
    calib_rows = _annotation_batch_rows(
        calib_units,
        records,
        guideline_version=guideline_calib,
        controls=controls,
        output_path=calib_path,
        provenance_body_commit=body_commit,
    )
    _write_csv(
        calib_path,
        calib_rows,
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
            "sample_roles_hidden",
            "challenge_reason_codes_hidden",
        ],
    )
    ctx.write_json(
        "calibration_annotation_batch_manifest.json",
        {
            "batch_size": len(calib_rows),
            "double_annotation_count": len(calib_rows),
            "guideline_version": guideline_calib,
            "excluded_from_formal_benchmark": True,
            "blind": True,
        },
    )
    _emit_state(
        ctx,
        "CALIBRATION_BATCH_READY",
        missing=(
            ["calibration_raw_annotations_path"]
            if not params.get("calibration_raw_annotations_path")
            else []
        ),
        extras={"generated_at": generated_at},
    )

    calibration_agreement = None
    calibration_decision = {
        "decision": "BLOCKED",
        "reason": "calibration labels not supplied",
    }
    if params.get("calibration_raw_annotations_path"):
        calib_anns = load_pilot_annotations(str(params["calibration_raw_annotations_path"]))
        ctx.write_jsonl("calibration_raw_annotations.jsonl", (a.to_dict() for a in calib_anns))
        calibration_agreement = analyze_pilot_agreement(
            calib_anns,
            bootstrap_replicates=int(params.get("bootstrap_replicates", 200)),
            bootstrap_seed=str(params.get("bootstrap_seed", "pilot-kappa-bootstrap-v1")),
        )
        ctx.write_json("calibration_agreement.json", calibration_agreement)
        if params.get("calibration_decision_path"):
            calibration_decision = json.loads(
                Path(str(params["calibration_decision_path"])).read_text(encoding="utf-8")
            )
        else:
            calibration_decision = {
                "decision": "CALIBRATION_REVIEW_REQUIRED",
                "reason": "labels present; human calibration decision required",
            }
        ctx.write_json("calibration_decision.json", calibration_decision)
        if not params.get("calibration_adjudications_path"):
            _emit_state(
                ctx,
                "CALIBRATION_REVIEW_REQUIRED",
                missing=["calibration_decision ENABLE or adjudications"],
                extras={"generated_at": generated_at},
            )
    else:
        _emit_state(
            ctx,
            "CALIBRATION_LABELS_INCOMPLETE",
            missing=["calibration_raw_annotations_path"],
            extras={"generated_at": generated_at},
        )
        # Write empty decision artifact
        ctx.write_json("calibration_decision.json", calibration_decision)

    # Guideline revision log placeholder
    rev_log = params.get("calibration_guideline_revision_log_path")
    if rev_log and Path(str(rev_log)).is_file():
        (ctx.artifacts_dir / "calibration_guideline_revision_log.md").write_text(
            Path(str(rev_log)).read_text(encoding="utf-8"), encoding="utf-8"
        )
    else:
        (ctx.artifacts_dir / "calibration_guideline_revision_log.md").write_text(
            "# Calibration guideline revision log\n\nNo revisions recorded for this run.\n",
            encoding="utf-8",
        )

    approval = params.get("calibration_approval")
    if approval != "ENABLE_FORMAL_PILOT":
        _emit_state(
            ctx,
            "FORMAL_PILOT_APPROVAL_REQUIRED",
            missing=['calibration_approval: "ENABLE_FORMAL_PILOT"'],
            extras={"generated_at": generated_at},
        )
        readiness = build_readiness_decision(
            evidence={
                "rights_complete": flow_counts.rights_ineligible == 0
                or flow_counts.corpus_eligible > 0,
                "sample_plan_complete": True,
                "representative_sample_complete": len(rep_units) > 0,
                "challenge_sample_complete": len(chal_units) > 0,
                "calibration_complete": bool(params.get("calibration_raw_annotations_path")),
                "formal_labels": "missing",
                "duplicate_calibration": "batch_ready",
            },
            human_approval=params.get("pilot_human_approval"),
        )
        ctx.write_json("pilot_readiness_decision.json", readiness)
        (ctx.artifacts_dir / "pilot_quantitative_report.md").write_text(
            render_quantitative_report(
                state="FORMAL_PILOT_APPROVAL_REQUIRED",
                plan=plan.to_dict(),
                eligibility_counts=flow_counts.to_dict(),
                representative_manifest=rep_manifest,
                challenge_manifest=chal_manifest,
                agreement=calibration_agreement,
                baseline_rep=None,
                baseline_chal=None,
                duplicate_blocking=blocking,
                readiness=readiness,
                execution_status="FORMAL_PILOT_APPROVAL_REQUIRED",
            ),
            encoding="utf-8",
        )
        (ctx.artifacts_dir / "pilot_limitations.md").write_text(PILOT_LIMITATIONS, encoding="utf-8")
        _write_resolved_config(ctx, params, generated_at)
        return

    # Formal batch
    formal_double = int(
        (params.get("pilot_design") or {})
        .get("formal_double_annotation", {})
        .get("target_count", plan.planned_double_annotation_count)
    )
    if params.get("formal_double_annotation_override") is not None:
        formal_double = int(params["formal_double_annotation_override"])
    formal_units = assign_double_annotation(
        merged_units, target_count=formal_double, sampling_seed=sampling_seed
    )
    # Mark formal role
    formal_units = [
        SampledUnitV1(
            cluster_id=u.cluster_id,
            article_id=u.article_id,
            sample_roles=tuple(dict.fromkeys(list(u.sample_roles) + ["formal"])),
            sampling_stratum=u.sampling_stratum,
            inclusion_probability=u.inclusion_probability,
            design_weight=u.design_weight,
            challenge_reason_codes=u.challenge_reason_codes,
            double_annotate=u.double_annotate,
            purposive=u.purposive,
            sampling_rank_key=u.sampling_rank_key,
        )
        for u in formal_units
    ]
    guideline_formal = str(params.get("formal_guideline_version", "pilot-guidelines-v1-formal"))
    formal_path = ctx.artifacts_dir / "formal_annotation_batch.csv"
    formal_rows = _annotation_batch_rows(
        formal_units,
        records,
        guideline_version=guideline_formal,
        controls=controls,
        output_path=formal_path,
        provenance_body_commit=body_commit,
    )
    _write_csv(
        formal_path,
        formal_rows,
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
            "sample_roles_hidden",
            "challenge_reason_codes_hidden",
        ],
    )
    ctx.write_json(
        "formal_annotation_batch_manifest.json",
        {
            "batch_size": len(formal_rows),
            "double_annotation_count": sum(1 for r in formal_rows if r["double_annotate"] == "yes"),
            "guideline_version": guideline_formal,
            "blind": True,
        },
    )
    _emit_state(ctx, "FORMAL_BATCH_READY", extras={"generated_at": generated_at})

    formal_agreement = None
    adjudicated = []
    if not params.get("formal_raw_annotations_path"):
        _emit_state(
            ctx,
            "FORMAL_LABELS_INCOMPLETE",
            missing=["formal_raw_annotations_path"],
            extras={"generated_at": generated_at},
        )
    else:
        formal_anns = load_pilot_annotations(str(params["formal_raw_annotations_path"]))
        ctx.write_jsonl("formal_raw_annotations.jsonl", (a.to_dict() for a in formal_anns))
        formal_agreement = analyze_pilot_agreement(
            formal_anns,
            bootstrap_replicates=int(params.get("bootstrap_replicates", 200)),
            bootstrap_seed=str(params.get("bootstrap_seed", "pilot-kappa-bootstrap-v1")),
        )
        ctx.write_json("formal_annotation_agreement.json", formal_agreement)
        (ctx.artifacts_dir / "formal_annotation_quality_report.md").write_text(
            "# Formal annotation quality\n\n"
            f"Joint clusters: {formal_agreement.get('n_jointly_labeled_clusters')}\n"
            f"Exact agreement: {formal_agreement.get('raw_exact_agreement')}\n"
            "Kappa: "
            f"{(formal_agreement.get('linearly_weighted_cohen_kappa') or {}).get('kappa')}\n",
            encoding="utf-8",
        )
        if not params.get("formal_adjudicated_annotations_path"):
            _emit_state(
                ctx,
                "ADJUDICATION_REQUIRED",
                missing=["formal_adjudicated_annotations_path"],
                extras={"generated_at": generated_at},
            )
        else:
            from research_data.relevance.annotation import load_adjudicated_annotations

            adjudicated = load_adjudicated_annotations(
                str(params["formal_adjudicated_annotations_path"])
            )
            ctx.write_jsonl(
                "formal_adjudicated_annotations.jsonl",
                (a.to_dict() for a in adjudicated),
            )

    # Duplicate reviews if supplied
    dup_metrics = None
    if params.get("duplicate_pair_reviews_path"):
        reviews = load_duplicate_pair_reviews(str(params["duplicate_pair_reviews_path"]))
        ctx.write_jsonl("duplicate_pair_reviews.jsonl", (r.to_dict() for r in reviews))
        grid = [
            float(x) for x in (params.get("duplicate_threshold_grid") or DEFAULT_THRESHOLD_GRID)
        ]
        dup_metrics = threshold_grid_metrics(reviews, [p.to_dict() for p in pair_frame], grid=grid)
        _write_csv(
            ctx.artifacts_dir / "duplicate_threshold_metrics.csv",
            dup_metrics,
            [
                "threshold",
                "candidate_precision",
                "sensitivity_within_blocked_universe",
                "corpus_candidate_pair_count",
                "tp",
                "fp",
                "fn",
                "tn",
                "recommendation_status",
            ],
        )
        ctx.write_json(
            "duplicate_threshold_uncertainty.json",
            {
                "method": "exploratory",
                "dependence": "pairs sharing article IDs are dependent",
                "wilson_on_weighted": False,
                "recommendation_status": "HUMAN_REVIEW_REQUIRED",
            },
        )
        (ctx.artifacts_dir / "duplicate_threshold_report.md").write_text(
            render_duplicate_threshold_report(dup_metrics, blocking_universe=blocking),
            encoding="utf-8",
        )

    # Baselines
    sample_ids = {u.article_id for u in formal_units} | {u.article_id for u in calib_units}
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
        ctx.artifacts_dir / "pilot_baseline_predictions.csv",
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

    baseline_rep = baseline_chal = None
    if adjudicated:
        baseline_rep = evaluate_pilot_baselines(
            predictions, adjudicated, formal_units, role="representative"
        )
        baseline_chal = evaluate_pilot_baselines(
            predictions, adjudicated, formal_units, role="challenge"
        )
        ctx.write_json("representative_baseline_metrics.json", baseline_rep)
        ctx.write_json("challenge_baseline_metrics.json", baseline_chal)
        ctx.write_json(
            "pilot_prevalence_estimate.json",
            design_weighted_prevalence(adjudicated, formal_units),
        )
        _emit_state(ctx, "PILOT_ANALYSIS_READY", extras={"generated_at": generated_at})
    elif params.get("formal_raw_annotations_path"):
        pass
    else:
        pass

    # Reconciliation
    recon_payload = {
        "raw_input": flow_counts.local_input,
        "schema_accepted": flow_counts.schema_valid,
        "schema_rejected": flow_counts.schema_rejected,
        "rights_eligible": flow_counts.rights_eligible,
        "rights_ineligible": flow_counts.rights_ineligible,
        "corpus_eligible": flow_counts.corpus_eligible,
        "corpus_excluded_non_rights": flow_counts.corpus_excluded_non_rights,
        "sampling_frame_units": flow_counts.sampling_frame_units,
        "eligible_exact_clusters": len(frame),
        "representative_selected": len(rep_units),
        "representative_by_stratum": dict(rep_manifest.get("stratum_sample_sizes") or {}),
    }
    recon = reconcile_pilot_counts(recon_payload)
    ctx.write_json("artifact_reconciliation.json", {**recon, "payload": recon_payload})
    if not recon["ok"]:
        _emit_state(
            ctx,
            "BENCHMARK_BUILD_REVIEW_REQUIRED",
            missing=["reconciliation"] + recon["errors"],
            extras={"generated_at": generated_at},
        )

    readiness = build_readiness_decision(
        evidence={
            "rights_complete": flow_counts.corpus_eligible > 0,
            "sample_plan_complete": True,
            "representative_sample_complete": len(rep_units) > 0,
            "challenge_sample_complete": len(chal_units) > 0,
            "calibration_complete": bool(params.get("calibration_raw_annotations_path")),
            "formal_labels": bool(params.get("formal_raw_annotations_path")),
            "adjudication": bool(adjudicated),
            "duplicate_calibration": bool(params.get("duplicate_pair_reviews_path")),
            "agreement_estimates": bool(formal_agreement or calibration_agreement),
            "reconciliation_ok": recon["ok"],
        },
        human_approval=params.get("pilot_human_approval"),
        human_reason=params.get("pilot_human_reason"),
    )
    if readiness["status"] != "READY_FOR_FINAL_BENCHMARK_CONSTRUCTION":
        if adjudicated and recon["ok"]:
            _emit_state(
                ctx,
                "BENCHMARK_BUILD_REVIEW_REQUIRED",
                missing=['pilot_human_approval: "APPROVE_FINAL_BENCHMARK_CONSTRUCTION"'],
                extras={"generated_at": generated_at},
            )

    ctx.write_json("pilot_readiness_decision.json", readiness)
    (ctx.artifacts_dir / "pilot_quantitative_report.md").write_text(
        render_quantitative_report(
            state=json.loads((ctx.artifacts_dir / "pilot_state.json").read_text())["pilot_state"],
            plan=plan.to_dict(),
            eligibility_counts=flow_counts.to_dict(),
            representative_manifest=rep_manifest,
            challenge_manifest=chal_manifest,
            agreement=formal_agreement or calibration_agreement,
            baseline_rep=baseline_rep,
            baseline_chal=baseline_chal,
            duplicate_blocking=blocking,
            readiness=readiness,
            execution_status=readiness["status"],
        ),
        encoding="utf-8",
    )
    (ctx.artifacts_dir / "pilot_limitations.md").write_text(PILOT_LIMITATIONS, encoding="utf-8")
    _write_resolved_config(ctx, params, generated_at)
    ctx.write_json(
        "run_metadata.json",
        {
            "task": "run_semiconductor_relevance_real_corpus_pilot",
            "generated_at": generated_at,
            "no_network": True,
            "no_models": True,
            "no_gpu": True,
            "no_production_theme_changes": True,
        },
    )


def _write_resolved_config(ctx: Any, params: Mapping[str, Any], generated_at: str) -> None:
    resolved = {"generated_at": generated_at, "params": dict(params)}
    # Avoid dumping huge inline blobs; paths only.
    (ctx.artifacts_dir / "config_resolved.yaml").write_text(
        yaml.safe_dump(resolved, sort_keys=True), encoding="utf-8"
    )

#!/usr/bin/env python3
"""Offline exploratory analysis + scoring of seeded theme-discovery candidates.

Reads an existing seeded-discovery artifact (``seeded_theme_candidates.jsonl`` or
``.csv``) and produces additive, human-review research outputs. It **never**
touches the production theme bundle, the source experiment, or BigQuery.

Two input shapes are supported:

- **discovery** (``record_count``, no background) — the current execute run. Only
  support, temporal presence, seed breadth, and classification are computable;
  every association metric (lift, log-lift, odds ratio, composite) is written as
  an honest ``null`` because there is no background corpus to compare against.
- **scoring** (``seed_record_count`` + ``background_record_count`` +
  ``total_seed_records`` + ``total_background_records``) — the output of the
  single-scan seed-vs-background scoring query. Full association metrics are
  computed via :mod:`kinetic.ingestion.warehouse.bigquery.theme_scoring`.

Outputs (written to ``--out-dir``):
    theme_candidate_scores.csv / .jsonl
    theme_candidate_review.csv
    theme_candidate_scoring_summary.json
    research_quality_report.md
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from kinetic.processing.news.themes.classification import GENERIC_NOISE, classify_theme
from kinetic.processing.news.themes.scoring import (
    ScoreComponents,
    association_component,
    composite_score,
    lift,
    odds_ratio,
    prevalence,
    risk_difference,
    smoothed_log_lift,
    support_component,
    temporal_stability_component,
)

# Canonical output column order for the scored-candidate artifact.
SCORE_FIELDS = [
    "theme",
    "candidate_rank",
    "seed_record_count",
    "background_record_count",
    "total_seed_records",
    "total_background_records",
    "seed_prevalence",
    "background_prevalence",
    "lift",
    "smoothed_log_lift",
    "risk_difference",
    "odds_ratio",
    "minimum_period_support",
    "periods_present",
    "temporal_stability_score",
    "source_count",
    "top_source_share",
    "seed_count",
    "top_seed_share",
    "top_day_share",
    "classification",
    "classification_confidence",
    "generic_noise_flag",
    "manual_review_required",
    "sample_record_ids",
    "sample_sources",
    "matched_seed_examples",
    "score_components",
    "composite_score",
]

_MIN_SUPPORT = 25
_SATURATING_SUPPORT = 5000


def _load(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    rows: List[Dict[str, Any]] = []
    for row in csv.DictReader(text.splitlines()):
        parsed = dict(row)
        for list_key in ("matched_seed_terms", "sample_sources", "sample_record_ids"):
            if isinstance(parsed.get(list_key), str):
                parsed[list_key] = [p for p in parsed[list_key].split("|") if p]
        rows.append(parsed)
    return rows


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _round(value: Optional[float], ndigits: int = 6) -> Optional[float]:
    return None if value is None else round(value, ndigits)


def _window_days(rows: List[Dict[str, Any]]) -> Optional[int]:
    firsts = [_int_or_none(r.get("first_seen")) for r in rows]
    lasts = [_int_or_none(r.get("last_seen")) for r in rows]
    firsts = [f for f in firsts if f]
    lasts = [f for f in lasts if f]
    if not firsts or not lasts:
        return None
    lo, hi = min(firsts), max(lasts)
    try:
        import datetime as dt

        d0 = dt.date(lo // 10000, (lo // 100) % 100, lo % 100)
        d1 = dt.date(hi // 10000, (hi // 100) % 100, hi % 100)
        return (d1 - d0).days + 1
    except (ValueError, TypeError):
        return None


def _seed_count(row: Dict[str, Any]) -> Optional[int]:
    seeds = row.get("matched_seed_terms")
    if isinstance(seeds, list):
        return len(seeds)
    return None


def build_score_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    window_days = _window_days(rows)
    out: List[Dict[str, Any]] = []
    for rank, row in enumerate(rows, start=1):
        theme = str(row.get("theme_code") or row.get("theme") or "").strip()

        seed_count_records = _int_or_none(row.get("seed_record_count"))
        if seed_count_records is None:
            seed_count_records = _int_or_none(row.get("record_count"))
        background = _int_or_none(row.get("background_record_count"))
        total_seed = _int_or_none(row.get("total_seed_records"))
        total_background = _int_or_none(row.get("total_background_records"))

        seed_prev = prevalence(seed_count_records, total_seed)
        bg_prev = prevalence(background, total_background)
        lift_v = lift(seed_count_records, total_seed, background, total_background)
        log_lift_v = smoothed_log_lift(seed_count_records, total_seed, background, total_background)
        risk_v = risk_difference(seed_count_records, total_seed, background, total_background)
        odds_v = odds_ratio(seed_count_records, total_seed, background, total_background)

        periods_present = _int_or_none(row.get("distinct_day_count"))
        stability = temporal_stability_component(periods_present, window_days)

        cls = classify_theme(theme)
        seed_breadth = _seed_count(row)

        components = ScoreComponents(
            support=support_component(seed_count_records, _MIN_SUPPORT, _SATURATING_SUPPORT),
            association=association_component(log_lift_v),
            temporal_stability=stability,
            source_diversity=None,  # honest null: v1 sample sources are not counts
            seed_diversity=(None if seed_breadth is None else min(1.0, seed_breadth / 9.0)),
            generic_penalty=0.8 if cls.category == GENERIC_NOISE else 0.0,
            duplication_penalty=None,
        )

        out.append(
            {
                "theme": theme,
                "candidate_rank": rank,
                "seed_record_count": seed_count_records,
                "background_record_count": background,
                "total_seed_records": total_seed,
                "total_background_records": total_background,
                "seed_prevalence": _round(seed_prev, 9),
                "background_prevalence": _round(bg_prev, 9),
                "lift": _round(lift_v),
                "smoothed_log_lift": _round(log_lift_v),
                "risk_difference": _round(risk_v, 9),
                "odds_ratio": _round(odds_v),
                "minimum_period_support": None,  # needs per-week breakdown (future scan)
                "periods_present": periods_present,
                "temporal_stability_score": _round(stability),
                "source_count": _int_or_none(row.get("source_count")),
                "top_source_share": None,  # needs per-source counts (future scan)
                "seed_count": seed_breadth,
                "top_seed_share": None,  # needs per-seed record counts (future scan)
                "top_day_share": None,  # needs per-day record counts (future scan)
                "classification": cls.category,
                "classification_confidence": round(cls.confidence, 4),
                "generic_noise_flag": cls.generic_noise_flag,
                "manual_review_required": cls.manual_review_required,
                "sample_record_ids": row.get("sample_record_ids") or None,
                "sample_sources": row.get("sample_sources") or None,
                "matched_seed_examples": row.get("matched_seed_terms") or None,
                "score_components": components.to_dict(),
                "composite_score": _round(composite_score(components)),
            }
        )
    return out


def _summary(score_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(score_rows)
    has_bg = sum(1 for r in score_rows if r["background_record_count"] is not None)
    generic = sum(1 for r in score_rows if r["generic_noise_flag"])
    review = sum(1 for r in score_rows if r["manual_review_required"])
    from collections import Counter

    fam = Counter(r["classification"] for r in score_rows)
    counts = [r["seed_record_count"] for r in score_rows if r["seed_record_count"] is not None]
    total = math.fsum(counts) if counts else 0.0
    top10 = math.fsum(sorted(counts, reverse=True)[:10]) / total if total else None
    survived = sum(1 for c in counts if c >= _MIN_SUPPORT)
    return {
        "candidate_count": n,
        "candidates_with_background": has_bg,
        "association_metrics_computable": has_bg > 0,
        "generic_noise_flagged": generic,
        "manual_review_required": review,
        "classification_breakdown": dict(fam.most_common()),
        "top10_share_of_seed_records": _round(top10),
        "candidates_surviving_min_support": survived,
        "min_support_threshold": _MIN_SUPPORT,
        "hit_row_limit_truncation_suspected": n >= 200,
        "notes": (
            "Association metrics are null unless the input carries background "
            "counts. Run the single-scan seed-vs-background scoring query to "
            "populate lift / composite_score."
        ),
    }


def _report_md(summary: Dict[str, Any], score_rows: List[Dict[str, Any]], src: str) -> str:
    lines: List[str] = []
    lines.append("# Seeded Theme-Discovery — Research Quality Report\n")
    lines.append(f"Source artifact: `{src}`\n")
    lines.append("## Headline\n")
    lines.append(
        "Raw co-occurrence frequency ranked the candidates; this report re-reads "
        "them with association-aware scoring and rule-based classification. "
        "**Association metrics require a background corpus** and are null when the "
        "input lacks background counts.\n"
    )
    lines.append("## Summary\n")
    for key, value in summary.items():
        lines.append(f"- **{key}**: {value}")
    lines.append("\n## Classification breakdown\n")
    for cat, cnt in summary["classification_breakdown"].items():
        lines.append(f"- {cat}: {cnt}")
    non_generic = [
        r
        for r in score_rows
        if not r["generic_noise_flag"] and r["classification"] != "market_context"
    ]
    lines.append("\n## Non-generic candidates worth a record read (top 25 by rank)\n")
    lines.append("| rank | theme | classification | conf | seed_records |")
    lines.append("| --- | --- | --- | --- | --- |")
    for r in non_generic[:25]:
        lines.append(
            f"| {r['candidate_rank']} | {r['theme']} | {r['classification']} | "
            f"{r['classification_confidence']} | {r['seed_record_count']} |"
        )
    lines.append(
        "\n> Classification is a research aid, not ground truth. No candidate is "
        "approved for the production bundle by this tool.\n"
    )
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SCORE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            for key in ("sample_sources", "matched_seed_examples", "sample_record_ids"):
                if isinstance(flat.get(key), list):
                    flat[key] = "|".join(str(v) for v in flat[key])
            if isinstance(flat.get("score_components"), dict):
                flat["score_components"] = json.dumps(flat["score_components"])
            writer.writerow({k: flat.get(k) for k in SCORE_FIELDS})


def _write_review_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    fields = [
        "theme",
        "candidate_rank",
        "classification",
        "classification_confidence",
        "generic_noise_flag",
        "manual_review_required",
        "seed_record_count",
        "lift",
        "composite_score",
        "keep_or_reject",
        "reviewer_notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = {k: row.get(k) for k in fields}
            out["keep_or_reject"] = ""
            out["reviewer_notes"] = ""
            writer.writerow(out)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="seeded candidate .jsonl or .csv")
    parser.add_argument("--out-dir", required=True, help="output directory (created if absent)")
    args = parser.parse_args(argv)

    in_path = Path(args.input)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _load(in_path)
    score_rows = build_score_rows(rows)
    summary = _summary(score_rows)

    (out_dir / "theme_candidate_scores.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in score_rows), encoding="utf-8"
    )
    _write_csv(out_dir / "theme_candidate_scores.csv", score_rows)
    _write_review_csv(out_dir / "theme_candidate_review.csv", score_rows)
    (out_dir / "theme_candidate_scoring_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "research_quality_report.md").write_text(
        _report_md(summary, score_rows, str(in_path)), encoding="utf-8"
    )

    print(f"Analyzed {len(score_rows)} candidates from {in_path}")
    print(f"  association metrics computable: {summary['association_metrics_computable']}")
    print(f"  generic_noise_flagged: {summary['generic_noise_flagged']}")
    print(f"  manual_review_required: {summary['manual_review_required']}")
    print(f"  classification_breakdown: {summary['classification_breakdown']}")
    print(f"  wrote outputs to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

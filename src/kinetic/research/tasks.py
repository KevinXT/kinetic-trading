"""Pipeline task: build the news×market research dataset.

Registered as ``build_news_market_dataset``. It reads committed offline inputs (or
upstream ``ctx.state``), builds every normalized layer, and writes deterministic
artifacts. A fixed clock makes output byte-stable for reproducibility tests. It
performs no provider calls.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from kinetic.data.catalog.features import DEFAULT_CATALOG_PATH, FeatureCatalog, load_feature_catalog
from kinetic.data.catalog.mappings import DEFAULT_MAPPINGS_PATH, load_topic_instrument_mappings
from kinetic.data.schemas.research import NewsMarketObservation
from kinetic.processing.cross_asset.validation import assert_required_bar_symbols
from kinetic.processing.market.calendar import MarketCalendar
from kinetic.research.datasets.builder import build_dataset
from kinetic.research.event_studies.event_study import EventStudyConfig
from kinetic.research.reports.study_report import (
    CONTRAST_CSV_FIELDS,
    contrast_rows,
    render_study_report,
    study_coverage,
)

LIMITATIONS = """# Research dataset limitations

This dataset is a research artifact. Read these limitations before drawing any
conclusion from it.

- GDELT coverage is a news-attention proxy, not investor attention. It never
  observes whether investors read the stories.
- The DOC API record limit can censor article counts (`response_truncated`).
- Query definitions shape measured topic volume; different queries would produce
  different counts.
- Topic tagging uses keyword rules, which introduce classification choices.
- The first version uses the reported publication timestamp as a proxy for
  historical information availability. It does not model GDELT indexing delay,
  publisher timestamp revisions, API delivery latency, or the time required for
  a live ingestion pipeline to observe the article. Modern collection ingestion
  time is never treated as original historical availability.
- Title-based deduplication does not measure semantic novelty, and duplicate
  groups are scoped to a collection run.
- `instrument_id` is the durable research identity; ticker symbols are date-valid
  identifiers and may change. Null validity windows do not remove survivorship risk.
- Source country is an imperfect metadata field; it is not the location of
  investors or of the underlying economic event.
- Topic-to-instrument mappings are researcher-defined assumptions, versioned but
  not objective truth.
- The current symbol selection introduces survivorship and selection bias; a
  present-day ticker list is not a survivorship-bias-free historical universe.
- Daily bars cannot reproduce intraday price discovery.
- Benchmark-adjusted return is a simple benchmark difference, not factor-model alpha; no
  risk-free rate or factor returns are available.
- Daily volume does not reveal order-book liquidity; the Amihud measure is a
  daily price/volume proxy, not market impact or a bid-ask spread.
- Correlation does not establish that news caused a price movement.
- Multiple testing can generate false discoveries; effect sizes and confidence
  intervals matter more than any single p-value. BH correction is limited to the
  predeclared H1 confirmatory endpoint family. H2 subgroup summaries are
  descriptive until a contrast is predeclared; H3-H5 are exploratory.
- Small samples produce unstable estimates. Groups below `minimum_sample` have
  no formal CI or p-value and are excluded from FDR.
- Session-date moving-block resampling keeps same-date rows together and partly
  addresses overlapping horizons, but it does not fully solve cross-sectional
  dependence, common shocks, or serial correlation.
- `USEquitySessionCalendarV1` is a curated 2018-2035 ruleset, not an authoritative
  exchange schedule; unscheduled closures and historical exceptions are omitted.
- Strategy profitability is not tested. Transaction costs, slippage, and
  execution are outside this milestone.
"""


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError(f"{path} must contain a JSON array")
        return [d for d in data if isinstance(d, dict)]
    # JSONL fallback.
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def _write_models_jsonl(ctx, name: str, models: Sequence[Any]) -> None:
    ctx.write_jsonl(name, (m.to_dict() for m in models))


def _observations_csv(ctx, name: str, observations: list[NewsMarketObservation], split_of) -> None:
    input_keys: set[str] = set()
    target_keys: set[str] = set()
    for obs in observations:
        input_keys.update(obs.inputs.keys())
        target_keys.update(obs.targets.keys())
    ordered_inputs = sorted(input_keys)
    ordered_targets = sorted(target_keys)
    identity = [
        "dataset_version",
        "observation_id",
        "session_date",
        "topic",
        "symbol",
        "instrument_id",
        "news_measurement_method",
        "alignment_policy",
        "feature_cutoff",
        "cutoff_buffer_seconds",
        "availability_assumption",
        "alignment_precision",
        "mapping_version",
        "split",
        "news_coverage_status",
        "market_coverage_status",
    ]
    header = (
        identity + [f"input.{k}" for k in ordered_inputs] + [f"target.{k}" for k in ordered_targets]
    )
    path = ctx.artifacts_dir / name
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for obs in observations:
            row = [
                obs.dataset_version,
                obs.observation_id,
                obs.session_date.isoformat(),
                obs.topic,
                obs.symbol,
                obs.instrument_id,
                obs.news_measurement_method,
                obs.alignment_policy,
                obs.to_dict()["feature_cutoff"],
                obs.cutoff_buffer_seconds,
                obs.availability_assumption,
                obs.alignment_precision,
                obs.mapping_version,
                split_of.get(obs.session_date.isoformat(), "unassigned"),
                obs.quality.get("news_coverage_status"),
                obs.quality.get("market_coverage_status"),
            ]
            row += [obs.inputs.get(k) for k in ordered_inputs]
            row += [obs.targets.get(k) for k in ordered_targets]
            writer.writerow(row)


def _event_summary_csv(ctx, name: str, summary: dict[str, Any]) -> None:
    path = ctx.artifacts_dir / name
    fields = [
        "group_kind",
        "group_value",
        "event_count",
        "response",
        "n",
        "mean",
        "median",
        "std",
        "minimum",
        "maximum",
        "ci_low",
        "ci_high",
        "positive_fraction",
        "p_value_uncorrected",
        "adjusted_p_value",
        "inferential_status",
        "inference_n_sessions",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for group in summary["groups"]:
            for response, metrics in sorted(group["metrics"].items()):
                writer.writerow(
                    {
                        "group_kind": group["group_kind"],
                        "group_value": group["group_value"],
                        "event_count": group["event_count"],
                        "response": response,
                        "n": metrics["n"],
                        "mean": metrics["mean"],
                        "median": metrics["median"],
                        "std": metrics["std"],
                        "minimum": metrics["minimum"],
                        "maximum": metrics["maximum"],
                        "ci_low": metrics["ci_low"],
                        "ci_high": metrics["ci_high"],
                        "positive_fraction": metrics["positive_fraction"],
                        "p_value_uncorrected": metrics["p_value_uncorrected"],
                        "adjusted_p_value": metrics["adjusted_p_value"],
                        "inferential_status": metrics["inferential_status"],
                        "inference_n_sessions": metrics["inference_n_sessions"],
                    }
                )


def build_news_market_dataset_task(
    ctx: Any,
    params: dict[str, Any],
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    catalog_loader: Callable[[str], FeatureCatalog] = load_feature_catalog,
) -> None:
    now = clock()
    if now.tzinfo is None:
        raise ValueError("clock must return a timezone-aware datetime")

    calendar = MarketCalendar()
    catalog_path = str(params.get("feature_catalog_path", DEFAULT_CATALOG_PATH))
    catalog = catalog_loader(catalog_path)
    mappings_path = str(params.get("mappings_path", DEFAULT_MAPPINGS_PATH))
    mapping_version, mappings = load_topic_instrument_mappings(mappings_path)

    # Inputs: prefer explicit committed fixture paths; fall back to ctx.state.
    articles: list[dict[str, Any]] = []
    if params.get("articles_path"):
        articles = _load_json_list(Path(str(params["articles_path"])))
    elif "tagged_articles" in ctx.state:
        articles = list(ctx.state["tagged_articles"])
    elif "normalized_articles" in ctx.state:
        articles = list(ctx.state["normalized_articles"])

    count_rows: list[dict[str, Any]] = []
    if params.get("counts_path"):
        count_rows = _load_json_list(Path(str(params["counts_path"])))
    elif "topic_daily_features" in ctx.state:
        count_rows = list(ctx.state["topic_daily_features"])

    bars: list[Any] = []
    if params.get("bars_path"):
        bars = _load_json_list(Path(str(params["bars_path"])))
    elif "price_bars" in ctx.state:
        bars = list(ctx.state["price_bars"])

    if not bars:
        raise ValueError(
            "build_news_market_dataset requires market bars via 'bars_path' or "
            "ctx.state['price_bars']"
        )
    if not articles and not count_rows:
        raise ValueError(
            "build_news_market_dataset requires news via 'articles_path'/'counts_path' or "
            "ctx.state['tagged_articles'|'normalized_articles'|'topic_daily_features']"
        )

    # Pre-build validation: fail loudly if required instrument/benchmark bars are
    # absent, rather than silently dropping targets later.
    require_symbols = params.get("require_symbols")
    if require_symbols:
        bar_symbols = {
            str(getattr(b, "symbol", None) or (b.get("symbol") if isinstance(b, dict) else ""))
            .strip()
            .upper()
            for b in bars
        }
        assert_required_bar_symbols(bar_symbols, list(require_symbols))

    study_window = params.get("study_window") or {}
    study_window_start = study_window.get("start") if isinstance(study_window, dict) else None
    study_window_end = study_window.get("end") if isinstance(study_window, dict) else None

    event_config = EventStudyConfig(
        threshold=float(params.get("event_threshold", 2.0)),
        minimum_sample=int(params.get("event_minimum_sample", 20)),
        forward_horizon=int(params.get("forward_horizon", 5)),
        seed=int(params.get("bootstrap_seed", 12345)),
        bootstrap_block_length=int(params.get("bootstrap_block_length", 5)),
        bootstrap_iterations=int(params.get("bootstrap_iterations", 2000)),
        study_window_start=str(study_window_start) if study_window_start else None,
        study_window_end=str(study_window_end) if study_window_end else None,
    )

    result = build_dataset(
        dataset_version=str(params.get("dataset_version", "news-market-v2")),
        feature_version=str(params.get("feature_version", "news-market-features-v2")),
        topic_taxonomy_version=str(params.get("topic_taxonomy_version", "kinetic-topics-v1")),
        calendar=calendar,
        catalog=catalog,
        mapping_version=mapping_version,
        mappings=mappings,
        bars=bars,
        articles=articles or None,
        count_rows=count_rows or None,
        alignment_policy_name=str(params.get("alignment_policy", "session_information_window_v2")),
        cutoff_buffer_seconds=int(params.get("cutoff_buffer_seconds", 300)),
        allow_alignment_downgrade=bool(params.get("allow_alignment_downgrade", False)),
        observed_at=now,
        generated_at=now,
        forward_horizon=int(params.get("forward_horizon", 5)),
        max_records=(int(params["max_records"]) if params.get("max_records") is not None else None),
        event_config=event_config,
        config_snapshot={"articles": bool(articles), "counts": bool(count_rows)},
    )

    ctx.state["news_topic_daily_features"] = [m.to_dict() for m in result.news_topic_daily_features]
    ctx.state["session_news_features"] = [m.to_dict() for m in result.session_news_features]
    ctx.state["market_session_features"] = [m.to_dict() for m in result.market_session_features]
    ctx.state["news_market_observations"] = [m.to_dict() for m in result.observations]
    ctx.state["event_study_results"] = result.event_study_summary

    _write_models_jsonl(ctx, "news_topic_daily_features.jsonl", result.news_topic_daily_features)
    _write_models_jsonl(ctx, "session_news_features.jsonl", result.session_news_features)
    _write_models_jsonl(ctx, "market_session_features.jsonl", result.market_session_features)
    _write_models_jsonl(ctx, "news_market_observations.jsonl", result.observations)
    _observations_csv(ctx, "news_market_observations.csv", result.observations, result.split_of)

    ctx.write_json("feature_catalog.json", catalog.to_dict())
    ctx.write_json("dataset_manifest.json", result.manifest.to_dict())
    ctx.write_json(
        "join_summary.json",
        {
            "observation_count": len(result.observations),
            "news_topic_daily_features": len(result.news_topic_daily_features),
            "session_news_features": len(result.session_news_features),
            "market_session_features": len(result.market_session_features),
            "topics": sorted({o.topic for o in result.observations}),
            "symbols": sorted({o.symbol for o in result.observations}),
            "news_measurement_methods": list(result.manifest.news_measurement_methods),
            "exclusion_counts": result.manifest.exclusion_counts,
            "splits": result.splits,
            "ordering": "session_date -> topic -> symbol -> measurement_method",
        },
    )
    ctx.write_jsonl("event_study_events.jsonl", (dict(e) for e in result.event_study_events))
    ctx.write_json("event_study_summary.json", result.event_study_summary)
    _event_summary_csv(ctx, "event_study_summary.csv", result.event_study_summary)
    (ctx.artifacts_dir / "research_limitations.md").write_text(LIMITATIONS, encoding="utf-8")

    # Event-vs-control contrast artifacts (the confirmatory H1 test).
    contrasts = result.event_study_summary.get("event_control_contrasts", [])
    rows = contrast_rows(contrasts)
    ctx.write_json(
        "event_control_contrasts.json",
        {
            "primary_h1_endpoint_family": result.event_study_summary.get(
                "primary_h1_endpoint_family"
            ),
            "fdr_families": result.event_study_summary.get("fdr_families"),
            "contrasts": contrasts,
        },
    )
    _contrasts_csv(ctx, "event_control_contrasts.csv", rows)

    # Optional human-readable study report.
    study_report_name = params.get("study_report_name")
    if study_report_name:
        bigquery_cost = None
        cost_summary_path = params.get("bigquery_summary_path")
        if cost_summary_path and Path(str(cost_summary_path)).is_file():
            bigquery_cost = json.loads(Path(str(cost_summary_path)).read_text(encoding="utf-8"))
        observed_benchmarks = sorted(
            {
                str(o.lineage.get("benchmark_symbol"))
                for o in result.observations
                if o.lineage.get("benchmark_symbol")
            }
        )
        coverage = study_coverage(
            result,
            requested_news_window=_window_tuple(params.get("news_window")),
            requested_market_window=_window_tuple(params.get("market_window")),
            study_window=_window_tuple(study_window),
            benchmark_symbols=observed_benchmarks or list(result.manifest.benchmark_symbols),
            bigquery_cost=bigquery_cost,
        )
        report_text = render_study_report(
            title=str(params.get("study_report_title", "News-attention market study")),
            topic=str(params.get("study_topic", "")) or ",".join(result.manifest.included_topics),
            coverage=coverage,
            summary=result.event_study_summary,
            config_params={"dataset_version": result.manifest.dataset_version},
            generated_at=result.manifest.to_dict()["generated_at"],
        )
        (ctx.artifacts_dir / str(study_report_name)).write_text(report_text, encoding="utf-8")
        ctx.write_json("study_coverage.json", coverage)


def _window_tuple(window: Any) -> tuple[str, str] | None:
    if isinstance(window, dict) and window.get("start") and window.get("end"):
        return (str(window["start"]), str(window["end"]))
    return None


def _contrasts_csv(ctx, name: str, rows: list[dict[str, Any]]) -> None:
    path = ctx.artifacts_dir / name
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CONTRAST_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

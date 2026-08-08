"""Build normalized news features from the two GDELT measurement paths.

- DOC / article-list path: rich article-level metadata supports breadth,
  concentration, syndication, and publication-span features.
- BigQuery count path: only ``article_count`` per date is available. Every
  richer field is emitted as ``None`` with an explicit capability flag set to
  ``False`` — a measured zero is never fabricated for an unmeasurable field.

Two layers are produced:

- ``NewsTopicDailyFeature`` — one calendar feature date × topic (raw daily layer).
- ``SessionNewsFeature`` — news assigned to a target session under an alignment
  policy, including lag-only rolling attention features computed from strictly
  prior sessions.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from kinetic.data.schemas.research import (
    AVAILABILITY_COLLECTION_TIME_ONLY,
    AVAILABILITY_DAILY_PERIOD_END_PROXY,
    AVAILABILITY_PUBLICATION_PROXY,
    COVERAGE_OK,
    COVERAGE_TRUNCATED,
    COVERAGE_ZERO,
    METHOD_BIGQUERY,
    METHOD_DOC,
    NewsTopicDailyFeature,
    SessionNewsFeature,
)
from kinetic.processing import stats
from kinetic.processing.cross_asset.alignment import POLICY_SESSION_WINDOW, AlignmentPolicy
from kinetic.processing.market.calendar import MarketCalendar

# Capability matrix per measurement method. False => the feature is not
# measurable on this path and must be null, not zero.
_DOC_CAPABILITIES = {
    "title_deduplicated_article_count": True,
    "attention_count": True,
    "syndication": True,
    "breadth": True,
    "source_concentration": True,
    "publication_timing": True,
    "topic_composition": True,
}
_BIGQUERY_CAPABILITIES = {
    "title_deduplicated_article_count": False,
    "attention_count": True,
    "syndication": False,
    "breadth": False,
    "source_concentration": False,
    "publication_timing": False,
    "topic_composition": False,
}


def query_specification_hash(queries: Iterable[str]) -> str:
    """Stable short hash of the sorted, de-duplicated query strings."""
    unique = sorted({q.strip() for q in queries if isinstance(q, str) and q.strip()})
    payload = json.dumps(unique, ensure_ascii=False, sort_keys=True)
    return "qspec-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _norm_title(article: dict[str, Any]) -> str:
    return str(article.get("title", "")).strip().lower()


def _article_topics(article: dict[str, Any]) -> list[str]:
    topics = article.get("topics")
    if isinstance(topics, list) and topics:
        return [str(t) for t in topics]
    primary = article.get("primary_topic")
    if primary:
        return [str(primary)]
    return ["untagged"]


def _aggregate_group(articles: Sequence[dict[str, Any]], topic: str) -> dict[str, Any]:
    """Compute raw news metrics for one (bucket, topic) group of DOC articles.

    Deduplication is by normalized title and scoped to this group, so
    ``observed`` counts article rows while ``title_deduplicated`` counts unique
    normalized titles within this session/topic group. It is not a persistent or
    semantic article identity.
    """
    observed = len(articles)
    title_groups: dict[str, int] = defaultdict(int)
    domain_counts: dict[str, int] = defaultdict(int)
    country_counts: dict[str, int] = defaultdict(int)
    timestamps: list[datetime] = []
    primary_count = 0
    multi_topic_count = 0

    for article in articles:
        title = _norm_title(article) or f"__untitled__{id(article)}"
        title_groups[title] += 1
        domain = str(article.get("domain", "")).strip().lower()
        if domain:
            domain_counts[domain] += 1
        country = str(article.get("source_country", "")).strip()
        if country:
            country_counts[country] += 1
        ts = _parse_ts(article.get("published_at"))
        if ts is not None:
            timestamps.append(ts)
        if article.get("primary_topic") == topic:
            primary_count += 1
        if len(_article_topics(article)) > 1:
            multi_topic_count += 1

    title_deduplicated = len(title_groups)
    duplicate_groups = {t: n for t, n in title_groups.items() if n > 1}
    largest_group = max(title_groups.values(), default=0)

    return {
        "observed": observed,
        "title_deduplicated": title_deduplicated,
        "observed_copy_count": observed - title_deduplicated,
        "domain_counts": dict(domain_counts),
        "country_counts": dict(country_counts),
        "unique_domains": len(domain_counts),
        "unique_source_countries": len(country_counts),
        "primary_topic_count": primary_count,
        "multi_topic_article_count": multi_topic_count,
        "duplicate_group_count": len(duplicate_groups),
        "largest_duplicate_group": largest_group,
        "earliest": min(timestamps) if timestamps else None,
        "latest": max(timestamps) if timestamps else None,
    }


def _base_news_inputs(agg: dict[str, Any], capabilities: dict[str, bool]) -> dict[str, Any]:
    """Turn a raw aggregate into the per-session news input feature dict.

    Unsupported metrics (capabilities False) are emitted as ``None``.
    """
    title_deduplicated = agg["title_deduplicated"]
    breadth = capabilities["breadth"]
    syndication = capabilities["syndication"]
    concentration = capabilities["source_concentration"]
    timing = capabilities["publication_timing"]
    composition = capabilities["topic_composition"]

    unique_domains = agg["unique_domains"] if breadth else None
    unique_countries = agg["unique_source_countries"] if breadth else None
    observed = agg["observed"]
    observed_copies = agg["observed_copy_count"] if syndication else None

    span_hours: float | None = None
    if timing and agg["earliest"] and agg["latest"]:
        span_hours = (agg["latest"] - agg["earliest"]).total_seconds() / 3600.0

    domain_counts = list(agg["domain_counts"].values())
    country_counts = list(agg["country_counts"].values())

    duplicate_ratio: float | None
    if not syndication:
        duplicate_ratio = None
    elif observed > 0 and observed_copies is not None:
        duplicate_ratio = observed_copies / observed
    else:
        duplicate_ratio = 0.0

    inputs: dict[str, Any] = {
        "title_deduplicated_article_count": title_deduplicated,
        "attention_count": title_deduplicated,
        "observed_article_count": observed,
        "log1p_attention_count": math.log1p(title_deduplicated),
        "observed_copy_count": observed_copies,
        "duplicate_ratio": duplicate_ratio,
        "unique_domains": unique_domains,
        "unique_source_countries": unique_countries,
        "domains_per_title_deduplicated_article": (
            (unique_domains / title_deduplicated) if (breadth and title_deduplicated > 0) else None
        ),
        "countries_per_title_deduplicated_article": (
            (unique_countries / title_deduplicated)
            if (breadth and title_deduplicated > 0)
            else None
        ),
        "primary_topic_share": (
            (agg["primary_topic_count"] / observed) if (composition and observed > 0) else None
        ),
        "multi_topic_share": (
            (agg["multi_topic_article_count"] / observed)
            if (composition and observed > 0)
            else None
        ),
        "publication_span_hours": span_hours,
        "observed_copy_domain_hhi": stats.herfindahl(domain_counts) if concentration else None,
        "observed_copy_domain_entropy": stats.entropy(domain_counts) if concentration else None,
        "observed_copy_effective_domain_count": (
            stats.effective_count(domain_counts) if concentration else None
        ),
        "observed_copy_source_country_hhi": (
            stats.herfindahl(country_counts) if concentration else None
        ),
        "largest_duplicate_group": agg["largest_duplicate_group"] if syndication else None,
        "duplicate_group_count": agg["duplicate_group_count"] if syndication else None,
    }
    return inputs


def build_news_features(
    *,
    method: str,
    policy: AlignmentPolicy,
    calendar: MarketCalendar,
    provider: str,
    feature_version: str,
    topic_taxonomy_version: str,
    observed_at: datetime,
    articles: Sequence[dict[str, Any]] | None = None,
    count_rows: Sequence[dict[str, Any]] | None = None,
    restrict_topics: frozenset[str] | None = None,
    max_records: int | None = None,
    dedupe_strategy: str = "title",
    alignment_downgraded: bool = False,
    alignment_warning: str | None = None,
) -> tuple[list[NewsTopicDailyFeature], list[SessionNewsFeature]]:
    """Build daily and session-aligned news features for one measurement method."""
    if method == METHOD_DOC:
        return _build_from_articles(
            policy=policy,
            calendar=calendar,
            provider=provider,
            feature_version=feature_version,
            topic_taxonomy_version=topic_taxonomy_version,
            observed_at=observed_at,
            articles=articles or [],
            restrict_topics=restrict_topics,
            max_records=max_records,
            dedupe_strategy=dedupe_strategy,
        )
    if method == METHOD_BIGQUERY:
        if policy.name == POLICY_SESSION_WINDOW:
            raise ValueError(
                "bigquery_gdelt_counts has daily counts without publication timestamps and "
                "cannot use session_information_window_v2; configure "
                "conservative_calendar_day_v2 or explicitly allow a builder downgrade"
            )
        return _build_from_counts(
            policy=policy,
            calendar=calendar,
            provider=provider,
            feature_version=feature_version,
            topic_taxonomy_version=topic_taxonomy_version,
            observed_at=observed_at,
            count_rows=count_rows or [],
            restrict_topics=restrict_topics,
            alignment_downgraded=alignment_downgraded,
            alignment_warning=alignment_warning,
        )
    raise ValueError(f"unknown news measurement method: {method!r}")


def _rolling_lag_features(
    ordered_counts: list[float | None],
    index: int,
) -> dict[str, Any]:
    """Lag-only attention features for the observation at ``index``.

    The prior window strictly excludes the current observation, so a z-score can
    never include the shock it is meant to classify.
    """
    prior_raw = ordered_counts[:index]
    prior = [value for value in prior_raw if value is not None]
    current = ordered_counts[index]
    prior7 = prior[-7:]
    prior30 = prior[-30:]
    if current is None:
        return {
            "attention_zscore_7": None,
            "attention_zscore_30": None,
            "attention_ratio_7": None,
            "attention_ratio_30": None,
            "attention_percentile_prior": None,
            "article_count_change_1": None,
            "attention_history_len": len(prior),
        }
    return {
        "attention_zscore_7": stats.lag_zscore(current, prior7) if len(prior7) == 7 else None,
        "attention_zscore_30": (stats.lag_zscore(current, prior30) if len(prior30) == 30 else None),
        "attention_ratio_7": stats.lag_ratio(current, prior7) if len(prior7) == 7 else None,
        "attention_ratio_30": stats.lag_ratio(current, prior30) if len(prior30) == 30 else None,
        "attention_percentile_prior": stats.trailing_percentile(current, prior),
        "article_count_change_1": (
            current - prior_raw[-1] if prior_raw and prior_raw[-1] is not None else None
        ),
        "attention_history_len": len(prior),
    }


def _build_from_articles(
    *,
    policy: AlignmentPolicy,
    calendar: MarketCalendar,
    provider: str,
    feature_version: str,
    topic_taxonomy_version: str,
    observed_at: datetime,
    articles: Sequence[dict[str, Any]],
    restrict_topics: frozenset[str] | None,
    max_records: int | None,
    dedupe_strategy: str,
) -> tuple[list[NewsTopicDailyFeature], list[SessionNewsFeature]]:
    capabilities = dict(_DOC_CAPABILITIES)
    all_queries = {str(a.get("query", "")) for a in articles}
    qhash = query_specification_hash(all_queries)
    truncated = max_records is not None and len(articles) >= max_records

    daily_buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    session_buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    session_source_dates: dict[tuple[str, str], set[str]] = defaultdict(set)

    for article in articles:
        published = _parse_ts(article.get("published_at"))
        if published is None:
            continue
        et_date = published.astimezone(calendar.tz).date().isoformat()
        try:
            session_date = policy.assign_session(published)
        except ValueError:
            continue
        for topic in _article_topics(article):
            if restrict_topics is not None and topic not in restrict_topics:
                continue
            daily_buckets[(et_date, topic)].append(article)
            session_key = (session_date.isoformat(), topic)
            session_buckets[session_key].append(article)
            session_source_dates[session_key].add(et_date)

    # --- Daily layer ---------------------------------------------------------
    daily: list[NewsTopicDailyFeature] = []
    for (feature_date, topic), group in sorted(daily_buckets.items()):
        agg = _aggregate_group(group, topic)
        coverage = COVERAGE_TRUNCATED if truncated else COVERAGE_OK
        daily.append(
            NewsTopicDailyFeature(
                feature_date=_date_from_iso(feature_date),
                topic=topic,
                provider=provider,
                news_measurement_method=METHOD_DOC,
                feature_version=feature_version,
                topic_taxonomy_version=topic_taxonomy_version,
                query_specification_hash=qhash,
                feature_timezone=str(calendar.tz),
                title_deduplicated_article_count=agg["title_deduplicated"],
                observed_article_count=agg["observed"],
                observed_copy_count=agg["observed_copy_count"],
                unique_domains=agg["unique_domains"],
                unique_source_countries=agg["unique_source_countries"],
                primary_topic_count=agg["primary_topic_count"],
                multi_topic_article_count=agg["multi_topic_article_count"],
                query_count=len({str(a.get("query", "")) for a in group if a.get("query")}),
                collection_run_count=1,
                earliest_published_at=agg["earliest"],
                latest_published_at=agg["latest"],
                observed_copy_domain_counts=agg["domain_counts"],
                observed_copy_country_counts=agg["country_counts"],
                duplicate_group_count=agg["duplicate_group_count"],
                largest_duplicate_group=agg["largest_duplicate_group"],
                feature_available_at=observed_at,
                source_observed_at=None,
                ingested_at=observed_at,
                availability_assumption=AVAILABILITY_COLLECTION_TIME_ONLY,
                coverage_status=coverage,
                response_truncated=truncated,
                feature_capabilities=capabilities,
            )
        )

    # --- Session layer (with lag-only rolling attention) ---------------------
    sessions = _finalize_sessions(
        session_buckets=session_buckets,
        session_source_dates=session_source_dates,
        policy=policy,
        calendar=calendar,
        provider=provider,
        method=METHOD_DOC,
        feature_version=feature_version,
        capabilities=capabilities,
        truncated=truncated,
        dedupe_strategy=dedupe_strategy,
        aggregate=lambda group, topic: _base_news_inputs(
            _aggregate_group(group, topic), capabilities
        ),
        count_of=lambda group, topic: float(_aggregate_group(group, topic)["title_deduplicated"]),
        baseline_eligible=not truncated,
        alignment_downgraded=False,
        alignment_warning=None,
    )
    return daily, sessions


def _build_from_counts(
    *,
    policy: AlignmentPolicy,
    calendar: MarketCalendar,
    provider: str,
    feature_version: str,
    topic_taxonomy_version: str,
    observed_at: datetime,
    count_rows: Sequence[dict[str, Any]],
    restrict_topics: frozenset[str] | None,
    alignment_downgraded: bool,
    alignment_warning: str | None,
) -> tuple[list[NewsTopicDailyFeature], list[SessionNewsFeature]]:
    capabilities = dict(_BIGQUERY_CAPABILITIES)
    all_terms: set[str] = set()
    for row in count_rows:
        for term in row.get("query_terms") or []:
            all_terms.add(str(term))
    qhash = query_specification_hash(all_terms)

    # BigQuery counts have no intraday timestamps, so they are aligned
    # conservatively: a daily count for D is knowable by end of D and maps to the
    # next session. Precision is recorded as conservative regardless of the
    # configured policy.
    daily: list[NewsTopicDailyFeature] = []
    session_inputs: dict[tuple[str, str], dict[str, Any]] = {}
    session_source_dates: dict[tuple[str, str], set[str]] = defaultdict(set)
    session_counts: dict[tuple[str, str], float] = {}

    for row in count_rows:
        topic = str(row.get("topic", "")).strip()
        if not topic or (restrict_topics is not None and topic not in restrict_topics):
            continue
        date_str = str(row.get("date", "")).strip()
        if not date_str:
            continue
        feature_date = _date_from_iso(date_str)
        count = int(row.get("article_count") or 0)
        coverage = COVERAGE_ZERO if count == 0 else COVERAGE_OK
        daily.append(
            NewsTopicDailyFeature(
                feature_date=feature_date,
                topic=topic,
                provider=provider,
                news_measurement_method=METHOD_BIGQUERY,
                feature_version=feature_version,
                topic_taxonomy_version=topic_taxonomy_version,
                query_specification_hash=qhash,
                feature_timezone=str(calendar.tz),
                title_deduplicated_article_count=None,
                observed_article_count=count,
                observed_copy_count=None,
                unique_domains=None,
                unique_source_countries=None,
                primary_topic_count=None,
                multi_topic_article_count=None,
                query_count=len(row.get("query_terms") or []),
                collection_run_count=1,
                earliest_published_at=None,
                latest_published_at=None,
                observed_copy_domain_counts={},
                observed_copy_country_counts={},
                duplicate_group_count=None,
                largest_duplicate_group=None,
                feature_available_at=observed_at,
                source_observed_at=None,
                ingested_at=observed_at,
                availability_assumption=AVAILABILITY_COLLECTION_TIME_ONLY,
                coverage_status=coverage,
                response_truncated=False,
                feature_capabilities=capabilities,
            )
        )
        session_date = calendar.next_session(feature_date)
        key = (session_date.isoformat(), topic)
        session_source_dates[key].add(feature_date.isoformat())
        session_counts[key] = session_counts.get(key, 0.0) + count
        # Merge: multiple feature dates can map to one session; counts add.
        merged_count = int(session_counts[key])
        session_inputs[key] = {
            "title_deduplicated_article_count": None,
            "attention_count": merged_count,
            "observed_article_count": merged_count,
            "log1p_attention_count": math.log1p(merged_count),
            "observed_copy_count": None,
            "duplicate_ratio": None,
            "unique_domains": None,
            "unique_source_countries": None,
            "domains_per_title_deduplicated_article": None,
            "countries_per_title_deduplicated_article": None,
            "primary_topic_share": None,
            "multi_topic_share": None,
            "publication_span_hours": None,
            "observed_copy_domain_hhi": None,
            "observed_copy_domain_entropy": None,
            "observed_copy_effective_domain_count": None,
            "observed_copy_source_country_hhi": None,
            "largest_duplicate_group": None,
            "duplicate_group_count": None,
        }

    sessions = _finalize_session_dicts(
        session_inputs=session_inputs,
        session_source_dates=session_source_dates,
        session_counts=session_counts,
        policy=policy,
        calendar=calendar,
        provider=provider,
        method=METHOD_BIGQUERY,
        feature_version=feature_version,
        capabilities=capabilities,
        truncated=False,
        dedupe_strategy="none",
        baseline_eligible=True,
        alignment_downgraded=alignment_downgraded,
        alignment_warning=alignment_warning,
    )
    return daily, sessions


def _finalize_sessions(
    *,
    session_buckets: dict[tuple[str, str], list[dict[str, Any]]],
    session_source_dates: dict[tuple[str, str], set[str]],
    policy: AlignmentPolicy,
    calendar: MarketCalendar,
    provider: str,
    method: str,
    feature_version: str,
    capabilities: dict[str, bool],
    truncated: bool,
    dedupe_strategy: str,
    aggregate: Any,
    count_of: Any,
    baseline_eligible: bool,
    alignment_downgraded: bool,
    alignment_warning: str | None,
) -> list[SessionNewsFeature]:
    session_inputs: dict[tuple[str, str], dict[str, Any]] = {}
    session_counts: dict[tuple[str, str], float] = {}
    for key, group in session_buckets.items():
        topic = key[1]
        session_inputs[key] = aggregate(group, topic)
        session_counts[key] = count_of(group, topic)
    return _finalize_session_dicts(
        session_inputs=session_inputs,
        session_source_dates=session_source_dates,
        session_counts=session_counts,
        policy=policy,
        calendar=calendar,
        provider=provider,
        method=method,
        feature_version=feature_version,
        capabilities=capabilities,
        truncated=truncated,
        dedupe_strategy=dedupe_strategy,
        baseline_eligible=baseline_eligible,
        alignment_downgraded=alignment_downgraded,
        alignment_warning=alignment_warning,
    )


def _finalize_session_dicts(
    *,
    session_inputs: dict[tuple[str, str], dict[str, Any]],
    session_source_dates: dict[tuple[str, str], set[str]],
    session_counts: dict[tuple[str, str], float],
    policy: AlignmentPolicy,
    calendar: MarketCalendar,
    provider: str,
    method: str,
    feature_version: str,
    capabilities: dict[str, bool],
    truncated: bool,
    dedupe_strategy: str,
    baseline_eligible: bool,
    alignment_downgraded: bool,
    alignment_warning: str | None,
) -> list[SessionNewsFeature]:
    # Order counts per topic for lag-only rolling features.
    by_topic: dict[str, list[tuple[str, tuple[str, str]]]] = defaultdict(list)
    for key in session_inputs:
        session_date, topic = key
        by_topic[topic].append((session_date, key))
    for topic in by_topic:
        by_topic[topic].sort()

    rolling: dict[tuple[str, str], dict[str, Any]] = {}
    for topic, entries in by_topic.items():
        ordered_counts: list[float | None] = [
            session_counts[key] if baseline_eligible else None for _, key in entries
        ]
        for idx, (_, key) in enumerate(entries):
            rolling[key] = _rolling_lag_features(ordered_counts, idx)

    results: list[SessionNewsFeature] = []
    for (session_date_str, topic), inputs in sorted(session_inputs.items()):
        session_date = _date_from_iso(session_date_str)
        alignment = policy.window_for_session(session_date)
        merged_inputs = dict(inputs)
        merged_inputs.update(rolling.get((session_date_str, topic), {}))
        source_dates = tuple(sorted(session_source_dates.get((session_date_str, topic), set())))
        attention_count = int(session_counts[(session_date_str, topic)])
        coverage = (
            COVERAGE_TRUNCATED
            if truncated
            else (COVERAGE_ZERO if attention_count == 0 else COVERAGE_OK)
        )
        results.append(
            SessionNewsFeature(
                session_date=session_date,
                topic=topic,
                provider=provider,
                news_measurement_method=method,
                feature_version=feature_version,
                alignment_policy=policy.name,
                feature_window_start=alignment.window_start,
                feature_window_end=alignment.window_end,
                feature_available_at=alignment.available_at,
                feature_cutoff=alignment.feature_cutoff,
                target_session_open=alignment.target_session_open,
                cutoff_buffer_seconds=alignment.cutoff_buffer_seconds,
                availability_assumption=(
                    AVAILABILITY_PUBLICATION_PROXY
                    if method == METHOD_DOC
                    else AVAILABILITY_DAILY_PERIOD_END_PROXY
                ),
                alignment_precision=alignment.alignment_precision,
                alignment_downgraded=alignment_downgraded,
                alignment_warning=alignment_warning,
                market_timezone=str(calendar.tz),
                source_feature_dates=source_dates,
                source_date_count=len(source_dates),
                calendar_days_aggregated=alignment.calendar_days_aggregated,
                inputs=merged_inputs,
                coverage_status=coverage,
                feature_capabilities=capabilities,
                quality_flags={
                    "dedupe_scope": "session_topic",
                    "dedupe_strategy": dedupe_strategy,
                    "alignment_precision": alignment.alignment_precision,
                    "alignment_downgraded": alignment_downgraded,
                    "alignment_warning": alignment_warning,
                    "response_truncated": truncated,
                    "attention_baseline_eligible": baseline_eligible,
                    "attention_history_len": merged_inputs.get("attention_history_len", 0),
                },
            )
        )
    return results


def _date_from_iso(value: str):
    from datetime import date

    return date.fromisoformat(value[:10])

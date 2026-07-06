"""Tests for the BigQuery reporting / BI layer (mocked BigQuery; no network)."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from common.cost.ledger import CostLedger
from common.cost.policy import cost_policy_from_dict
from common.errors import ConfigError
from news_data.bigquery.client import SafeBigQueryClient
from news_data.bigquery.sql_guardrails import validate_bigquery_sql
from news_data.reporting import build_views as bv
from news_data.reporting import views as views_mod
from news_data.reporting.views import (
    REPORTING_VIEWS,
    build_create_view_statement,
    discover_sql_files,
    get_reporting_views,
    get_view,
    qualified_view_id,
    render_view_sql,
)

_GIB = 1024**3
SOURCE_TABLE = "gdelt-bq.gdeltv2.gkg_partitioned"


# ── fake BigQuery (mirrors test_safe_bigquery_client) ───────────────────────


class _FakeJob:
    def __init__(self, total_bytes: int, rows=None) -> None:
        self.total_bytes_processed = total_bytes
        self._rows = rows or []

    def result(self):
        return list(self._rows)


class _FakeBQClient:
    def __init__(self, total_bytes: int) -> None:
        self.total_bytes = total_bytes
        self.calls = []

    def query(self, sql, job_config=None):
        self.calls.append((sql, job_config))
        return _FakeJob(self.total_bytes)


def _factory(*, dry_run, maximum_bytes_billed):
    return SimpleNamespace(dry_run=dry_run, maximum_bytes_billed=maximum_bytes_billed)


def _safe_client(total_bytes=1 * _GIB):
    fake = _FakeBQClient(total_bytes)
    client = SafeBigQueryClient(project_id="proj", client=fake, job_config_factory=_factory)
    return client, fake


def _policy(**overrides):
    body = {
        "monthly_cap_usd": 10.0,
        "emergency_stop_usd": 8.0,
        "daily_cap_usd": 0.50,
        "single_query_cap_usd": 0.10,
        "default_max_query_gb": 12,
    }
    body.update(overrides)
    return cost_policy_from_dict({"cost_policy": body})


def _settings(**overrides):
    base = dict(
        project_id="proj",
        dataset="kinetic_reporting",
        source_table=SOURCE_TABLE,
        lookback_days=30,
        top_n=25,
        maximum_bytes_billed=2 * _GIB,
    )
    base.update(overrides)
    return bv.ReportingSettings(**base)


# ── SQL file discovery ──────────────────────────────────────────────────────


def test_all_registered_sql_files_exist() -> None:
    for view in REPORTING_VIEWS:
        assert view.sql_path.is_file(), f"missing SQL file for {view.name}"


def test_discover_sql_files_matches_registry() -> None:
    discovered = {p.name for p in discover_sql_files()}
    registered = {v.filename for v in REPORTING_VIEWS}
    # Every registered view has a discoverable file (extra files are allowed).
    assert registered <= discovered


def test_expected_view_names_present() -> None:
    names = {v.name for v in get_reporting_views()}
    assert {
        "daily_event_volume",
        "top_sources",
        "top_themes_or_entities",
        "data_quality_summary",
    } <= names


# ── template rendering ──────────────────────────────────────────────────────


def test_render_substitutes_placeholders() -> None:
    for view in REPORTING_VIEWS:
        sql = render_view_sql(view, source_table=SOURCE_TABLE, lookback_days=7, top_n=5)
        assert "{{" not in sql and "}}" not in sql
        assert f"`{SOURCE_TABLE}`" in sql


def test_render_applies_lookback_and_top_n() -> None:
    sql = render_view_sql(
        get_view("top_sources"), source_table=SOURCE_TABLE, lookback_days=14, top_n=9
    )
    assert "INTERVAL 14 DAY" in sql
    assert "LIMIT 9" in sql


def test_required_column_aliases_present() -> None:
    for view in REPORTING_VIEWS:
        sql = render_view_sql(view, source_table=SOURCE_TABLE)
        for col in view.required_columns:
            assert f"AS {col}" in sql, f"{view.name} missing alias {col}"


def test_rendered_sql_passes_guardrails() -> None:
    for view in REPORTING_VIEWS:
        sql = render_view_sql(view, source_table=SOURCE_TABLE)
        # Must not raise.
        validate_bigquery_sql(sql, [SOURCE_TABLE])


def test_rendered_sql_has_no_select_star() -> None:
    for view in REPORTING_VIEWS:
        assert "SELECT *" not in render_view_sql(view, source_table=SOURCE_TABLE)


def test_rendered_sql_bounds_scan_with_partitiontime() -> None:
    for view in REPORTING_VIEWS:
        assert "_PARTITIONTIME" in render_view_sql(view, source_table=SOURCE_TABLE)


def test_render_rejects_empty_source_table() -> None:
    with pytest.raises(ConfigError):
        render_view_sql(get_view("top_sources"), source_table="")


def test_render_rejects_non_positive_lookback() -> None:
    with pytest.raises(ConfigError):
        render_view_sql(get_view("top_sources"), source_table=SOURCE_TABLE, lookback_days=0)


def test_get_view_unknown_raises() -> None:
    with pytest.raises(ConfigError, match="unknown reporting view"):
        get_view("does_not_exist")


# ── CREATE VIEW building ─────────────────────────────────────────────────────


def test_qualified_view_id() -> None:
    assert qualified_view_id("proj", "ds", "v") == "proj.ds.v"


def test_qualified_view_id_rejects_blank() -> None:
    with pytest.raises(ConfigError):
        qualified_view_id("proj", "", "v")


def test_build_create_view_statement_wraps_select() -> None:
    select_sql = render_view_sql(get_view("daily_event_volume"), source_table=SOURCE_TABLE)
    stmt = build_create_view_statement("proj.kinetic_reporting.daily_event_volume", select_sql)
    assert stmt.startswith("CREATE OR REPLACE VIEW `proj.kinetic_reporting.daily_event_volume` AS")
    assert "SELECT" in stmt


# ── runner: dry-run + create (mocked) ───────────────────────────────────────


def test_process_view_dry_run_estimates(tmp_path: Path) -> None:
    client, fake = _safe_client(1 * _GIB)
    ledger = CostLedger(tmp_path / "l.jsonl")
    result = bv.process_view(
        get_view("daily_event_volume"),
        settings=_settings(),
        policy=_policy(),
        client=client,
        ledger=ledger,
        create=False,
    )
    assert result.status == "ok"
    assert result.validated is True
    assert result.created is False
    assert result.estimated_bytes == 1 * _GIB
    # Only a dry-run call happened.
    assert len(fake.calls) == 1
    assert fake.calls[0][1].dry_run is True
    assert ledger.read_all()[-1]["decision"] == "DRY_RUN_ONLY"


def test_process_view_create_runs_ddl(tmp_path: Path) -> None:
    client, fake = _safe_client(1 * _GIB)
    result = bv.process_view(
        get_view("top_sources"),
        settings=_settings(),
        policy=_policy(),
        client=client,
        create=True,
    )
    assert result.created is True
    # A dry-run estimate then a (non-dry-run) CREATE statement.
    exec_calls = [c for c in fake.calls if not c[1].dry_run]
    assert len(exec_calls) == 1
    assert exec_calls[0][0].startswith("CREATE OR REPLACE VIEW")


def test_build_report_covers_all_views() -> None:
    client, _ = _safe_client(1 * _GIB)
    results = bv.build_report(settings=_settings(), policy=_policy(), client=client)
    assert len(results) == len(REPORTING_VIEWS)
    assert all(r.status == "ok" for r in results)


def test_build_report_no_estimate_skips_bigquery() -> None:
    client, fake = _safe_client(1 * _GIB)
    results = bv.build_report(settings=_settings(), policy=_policy(), client=client, estimate=False)
    assert all(r.validated for r in results)
    assert fake.calls == []  # never touched BigQuery


def test_format_report_mentions_views() -> None:
    client, _ = _safe_client(1 * _GIB)
    results = bv.build_report(settings=_settings(), policy=_policy(), client=client)
    text = bv.format_report(results, create=False)
    for view in REPORTING_VIEWS:
        assert view.name in text


# ── config resolution ───────────────────────────────────────────────────────


def test_resolve_settings_from_config(tmp_path: Path) -> None:
    cfg = tmp_path / "reporting.yaml"
    cfg.write_text(
        "providers:\n"
        "  bigquery:\n"
        "    project_id: my-proj\n"
        "    table: gdelt-bq.gdeltv2.gkg_partitioned\n"
        "reporting:\n"
        "  dataset: kinetic_reporting\n"
        "  lookback_days: 7\n"
        "  top_n: 5\n"
        "  cost_controls:\n"
        "    execute_query: ENABLE\n"
        "    maximum_bytes_billed: 2000000000\n",
        encoding="utf-8",
    )
    args = bv.build_parser().parse_args(["--config", str(cfg)])
    settings = bv.resolve_settings(args, local_path=tmp_path / "no_local.yaml")
    assert settings.project_id == "my-proj"
    assert settings.dataset == "kinetic_reporting"
    assert settings.lookback_days == 7
    assert settings.top_n == 5
    assert settings.execute_query == "ENABLE"
    assert settings.maximum_bytes_billed == 2000000000


def test_resolve_settings_cli_overrides(tmp_path: Path) -> None:
    cfg = tmp_path / "reporting.yaml"
    cfg.write_text(
        "providers:\n  bigquery:\n    project_id: base-proj\nreporting:\n  dataset: ds\n",
        encoding="utf-8",
    )
    args = bv.build_parser().parse_args(
        ["--config", str(cfg), "--project", "override-proj", "--lookback-days", "3"]
    )
    settings = bv.resolve_settings(args, local_path=tmp_path / "no_local.yaml")
    assert settings.project_id == "override-proj"
    assert settings.lookback_days == 3


def test_resolve_settings_rejects_placeholder_project(tmp_path: Path) -> None:
    cfg = tmp_path / "reporting.yaml"
    cfg.write_text(
        "providers:\n  bigquery:\n    project_id: YOUR_PROJECT_ID\nreporting:\n  dataset: ds\n",
        encoding="utf-8",
    )
    args = bv.build_parser().parse_args(["--config", str(cfg)])
    with pytest.raises(ConfigError, match="real BigQuery project id"):
        bv.resolve_settings(args, local_path=tmp_path / "no_local.yaml")


def test_resolve_settings_requires_dataset(tmp_path: Path) -> None:
    cfg = tmp_path / "reporting.yaml"
    cfg.write_text(
        "providers:\n  bigquery:\n    project_id: my-proj\n",
        encoding="utf-8",
    )
    args = bv.build_parser().parse_args(["--config", str(cfg)])
    with pytest.raises(ConfigError, match="destination dataset"):
        bv.resolve_settings(args, local_path=tmp_path / "no_local.yaml")


def test_shipped_reporting_config_resolves() -> None:
    # The committed configs/reporting.yaml should parse and expose a dataset.
    args = bv.build_parser().parse_args(["--config", "configs/reporting.yaml", "--project", "p"])
    settings = bv.resolve_settings(args)
    assert settings.dataset
    assert settings.source_table == SOURCE_TABLE


def test_sql_dir_points_inside_package() -> None:
    assert views_mod.SQL_DIR.name == "sql"
    assert views_mod.SQL_DIR.parent.name == "reporting"

"""Config + env-resolution guards for the seeded theme-scoring experiment.

These tests never read real credentials or the real project id; they assert the
*mechanism* (placeholder rejection, local-override resolution, dry-run vs execute
differences, typed execute gate) without exposing any secret value.
"""

from pathlib import Path

import pytest
from common.config_builder import apply_local_overrides, load_config
from common.errors import DataSourceError
from news_data.bigquery.client import SafeBigQueryClient

DRYRUN = "configs/research/semiconductors_seeded_theme_scoring_30d_dryrun.yaml"
EXECUTE = "configs/research/semiconductors_seeded_theme_scoring_30d_execute.yaml"


def _ingest(path: str) -> dict:
    return load_config(path)["pipeline"]["ingest"]


def test_public_scoring_configs_use_placeholder_project_id() -> None:
    # Public configs must never carry a real project id; it comes from the
    # git-ignored configs/local.yaml override at runtime.
    for path in (DRYRUN, EXECUTE):
        cfg = load_config(path)
        assert cfg["providers"]["bigquery"]["project_id"] == "YOUR_PROJECT_ID"


def test_local_override_resolves_project_id_without_touching_base() -> None:
    base = load_config(DRYRUN)
    merged = apply_local_overrides(
        base,
        local_path=_write_tmp_local(),
    )
    assert merged["providers"]["bigquery"]["project_id"] == "example-project-1234"
    # Base config file content is unaffected (still placeholder).
    assert load_config(DRYRUN)["providers"]["bigquery"]["project_id"] == "YOUR_PROJECT_ID"


def _write_tmp_local() -> Path:
    import tempfile

    p = Path(tempfile.mkdtemp()) / "local.yaml"
    p.write_text(
        'providers:\n  bigquery:\n    project_id: "example-project-1234"\n',
        encoding="utf-8",
    )
    return p


def test_safe_client_rejects_placeholder_project_id() -> None:
    client = SafeBigQueryClient(project_id="YOUR_PROJECT_ID")
    with pytest.raises(DataSourceError):
        client.dry_run("SELECT 1 FROM t WHERE DATE BETWEEN 1 AND 2")


def test_dryrun_config_has_execution_disabled() -> None:
    cc = _ingest(DRYRUN)["cost_controls"]
    assert cc["dry_run"] is True
    assert cc["execute_query"] == ""


def test_execute_config_requires_typed_gate() -> None:
    cc = _ingest(EXECUTE)["cost_controls"]
    assert cc["dry_run"] is False
    assert cc["execute_query"] == "ENABLE"


def test_dryrun_and_execute_differ_only_where_expected() -> None:
    d, e = _ingest(DRYRUN), _ingest(EXECUTE)
    # Same query shape (seeds, columns, window, limits) -> identical SQL/bytes.
    for key in ("seed_specs", "seed_search_columns", "window", "limit", "min_support"):
        assert d[key] == e[key], key
    # The only meaningful difference is the execution gate.
    assert d["cost_controls"]["dry_run"] != e["cost_controls"]["dry_run"]
    assert d["cost_controls"]["execute_query"] != e["cost_controls"]["execute_query"]


def test_scoring_configs_carry_cost_controls_and_partition_filter() -> None:
    for path in (DRYRUN, EXECUTE):
        ing = _ingest(path)
        assert ing["partition_filter"]["enabled"] is True
        cc = ing["cost_controls"]
        assert cc["maximum_bytes_billed"] <= 35_000_000_000
        assert cc["cost_policy_path"] == "configs/cost_policy.yaml"
        assert ing["min_support"] >= 25

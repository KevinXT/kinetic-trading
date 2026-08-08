"""Tests for git-ignored local config overrides (configs/local.yaml)."""

from pathlib import Path

from kinetic.core.config import (
    apply_local_overrides,
    deep_merge_dicts,
    load_runtime_config,
)

REAL_PROJECT_ID = "kinetic-trading-497922"
PLACEHOLDER = "YOUR_PROJECT_ID"

BASE = {
    "providers": {
        "bigquery": {
            "project_id": PLACEHOLDER,
            "table": "gdelt-bq.gdeltv2.gkg_partitioned",
        }
    },
    "pipeline": {"ingest": {"source": "bigquery_gdelt_counts"}},
}


def _write(p: Path, text: str) -> Path:
    p.write_text(text, encoding="utf-8")
    return p


# ── apply_local_overrides ────────────────────────────────────────────────────


def test_no_local_file_leaves_config_unchanged(tmp_path: Path) -> None:
    missing = tmp_path / "local.yaml"
    out = apply_local_overrides(BASE, local_path=missing)
    assert out == BASE


def test_local_overrides_only_project_id_preserving_table(tmp_path: Path) -> None:
    local = _write(
        tmp_path / "local.yaml",
        'providers:\n  bigquery:\n    project_id: "kinetic-trading-497922"\n',
    )
    out = apply_local_overrides(BASE, local_path=local)
    bq = out["providers"]["bigquery"]
    assert bq["project_id"] == REAL_PROJECT_ID  # overridden
    assert bq["table"] == "gdelt-bq.gdeltv2.gkg_partitioned"  # preserved
    # Base is not mutated.
    assert BASE["providers"]["bigquery"]["project_id"] == PLACEHOLDER


def test_nested_override_is_recursive(tmp_path: Path) -> None:
    base = {"a": {"b": {"c": 1, "d": 2}, "keep": True}}
    override = {"a": {"b": {"c": 99}}}
    out = deep_merge_dicts(base, override)
    assert out == {"a": {"b": {"c": 99, "d": 2}, "keep": True}}
    # Missing keys never delete base keys.
    assert out["a"]["keep"] is True


def test_load_runtime_config_applies_local(tmp_path: Path) -> None:
    base = _write(
        tmp_path / "base.yaml",
        (
            "providers:\n"
            "  bigquery:\n"
            '    project_id: "YOUR_PROJECT_ID"\n'
            '    table: "gdelt-bq.gdeltv2.gkg_partitioned"\n'
        ),
    )
    local = _write(
        tmp_path / "local.yaml",
        'providers:\n  bigquery:\n    project_id: "kinetic-trading-497922"\n',
    )
    cfg = load_runtime_config(base, local_path=local)
    assert cfg["providers"]["bigquery"]["project_id"] == REAL_PROJECT_ID
    assert cfg["providers"]["bigquery"]["table"] == "gdelt-bq.gdeltv2.gkg_partitioned"


def test_load_runtime_config_without_local_is_unchanged(tmp_path: Path) -> None:
    base = _write(
        tmp_path / "base.yaml",
        'providers:\n  bigquery:\n    project_id: "YOUR_PROJECT_ID"\n',
    )
    cfg = load_runtime_config(base, local_path=tmp_path / "does_not_exist.yaml")
    assert cfg["providers"]["bigquery"]["project_id"] == PLACEHOLDER


# ── repo hygiene ─────────────────────────────────────────────────────────────


def _public_yaml_configs() -> list[Path]:
    # All committed YAML under configs/, excluding git-ignored local overrides.
    out = []
    for p in Path("configs").rglob("*.yaml"):
        name = p.name
        if name == "local.yaml" or name.endswith(".local.yaml"):
            continue
        out.append(p)
    return out


def test_public_configs_do_not_contain_real_project_id() -> None:
    offenders = [
        str(p) for p in _public_yaml_configs() if REAL_PROJECT_ID in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"public configs leak the real project id: {offenders}"


def test_local_example_exists() -> None:
    p = Path("configs/local.example.yaml")
    assert p.is_file()
    assert PLACEHOLDER in p.read_text(encoding="utf-8")


def test_gitignore_includes_local_config_patterns() -> None:
    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    assert "configs/local.yaml" in gitignore
    assert "configs/*.local.yaml" in gitignore

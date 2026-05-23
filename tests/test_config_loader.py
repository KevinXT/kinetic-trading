"""Tests for common.config_builder include resolution and validation."""

from pathlib import Path

import pytest
from common.config_builder import load_config
from common.errors import ConfigError


def test_circular_include_raises(tmp_path: Path) -> None:
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text("include:\n  - b.yaml\nx: 1\n", encoding="utf-8")
    b.write_text("include:\n  - a.yaml\ny: 2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Circular YAML include"):
        load_config(a)


def test_missing_include_file_message(tmp_path: Path) -> None:
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - missing.yaml\nkey: root\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Included YAML not found") as ei:
        load_config(root)
    msg = str(ei.value)
    assert "parent config:" in msg
    assert "missing.yaml" in msg or "include path:" in msg
    assert str(root.resolve()) in msg or "resolved path:" in msg


def test_include_must_be_list(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("include: not_a_list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a YAML list"):
        load_config(p)


def test_include_entries_must_be_strings(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("include:\n  - 42\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="non-empty string"):
        load_config(p)


def test_merge_order_file_overrides_includes(tmp_path: Path) -> None:
    inc = tmp_path / "base.yaml"
    root = tmp_path / "main.yaml"
    inc.write_text("shared:\n  a: 1\n  b: old\n", encoding="utf-8")
    root.write_text("include:\n  - base.yaml\nshared:\n  b: new\n", encoding="utf-8")
    cfg = load_config(root)
    assert cfg["shared"]["a"] == 1
    assert cfg["shared"]["b"] == "new"

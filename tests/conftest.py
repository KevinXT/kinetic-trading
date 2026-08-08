"""Shared test anchors.

Test modules live at varying depths under ``tests/unit/<subsystem>/``, so
``Path(__file__).parent`` is not a stable way to reach a fixture. Everything
anchors to :data:`REPO_ROOT` and :data:`FIXTURES` instead, which keeps a test
working no matter which directory it is filed under.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES

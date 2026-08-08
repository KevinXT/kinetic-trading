"""Tests for BigQuery cost estimation helpers."""

import pytest

from kinetic.ingestion.cost.estimate import (
    bytes_to_gib,
    bytes_to_tib,
    estimate_bigquery_cost_usd,
)

_GIB = 1024**3
_TIB = 1024**4


def test_zero_bytes_is_free() -> None:
    assert estimate_bigquery_cost_usd(0) == 0.0


def test_one_tib_is_default_price() -> None:
    assert estimate_bigquery_cost_usd(_TIB) == pytest.approx(6.25)


def test_ten_gib_is_about_six_cents() -> None:
    cost = estimate_bigquery_cost_usd(10 * _GIB)
    assert cost == pytest.approx(0.061, abs=0.001)


def test_custom_price_per_tib() -> None:
    assert estimate_bigquery_cost_usd(_TIB, usd_per_tib=5.0) == pytest.approx(5.0)


def test_negative_bytes_raises() -> None:
    with pytest.raises(ValueError):
        estimate_bigquery_cost_usd(-1)


def test_bytes_to_gib_and_tib() -> None:
    assert bytes_to_gib(_GIB) == pytest.approx(1.0)
    assert bytes_to_tib(_TIB) == pytest.approx(1.0)
    assert bytes_to_gib(0) == 0.0


def test_bytes_to_gib_negative_raises() -> None:
    with pytest.raises(ValueError):
        bytes_to_gib(-5)

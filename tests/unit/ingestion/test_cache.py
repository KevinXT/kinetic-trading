"""Tests for kinetic.ingestion.caching — cache-aside JSON response caching."""

import os
from inspect import signature
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kinetic.ingestion import caching as cache


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect CACHE_ROOT to a temp directory for every test."""
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)


# ── deterministic cache keys ──────────────────────────────────────────────


def test_make_cache_key_is_deterministic() -> None:
    a = cache.make_cache_key({"q": "ai", "max": 10})
    b = cache.make_cache_key({"max": 10, "q": "ai"})
    assert a == b, "sorted-keys hashing should be order-independent"


def test_make_cache_key_differs_for_different_payloads() -> None:
    a = cache.make_cache_key({"q": "ai"})
    b = cache.make_cache_key({"q": "ml"})
    assert a != b


def test_existing_public_cache_signatures_are_preserved() -> None:
    assert tuple(signature(cache.load_json_cache).parameters) == ("namespace", "key")
    assert tuple(signature(cache.save_json_cache).parameters) == ("namespace", "key", "data")
    assert tuple(signature(cache.get_or_fetch_json).parameters) == (
        "namespace",
        "payload",
        "fetch_fn",
        "force_refresh",
    )


# ── namespace separation ──────────────────────────────────────────────────


def test_namespace_isolates_cache_entries(tmp_path: Path) -> None:
    key = cache.make_cache_key({"x": 1})

    cache.save_json_cache("ns_a", key, {"src": "a"})
    cache.save_json_cache("ns_b", key, {"src": "b"})

    assert cache.load_json_cache("ns_a", key) == {"src": "a"}
    assert cache.load_json_cache("ns_b", key) == {"src": "b"}


# ── file persistence ─────────────────────────────────────────────────────


def test_save_creates_file_and_load_reads_it_back(tmp_path: Path) -> None:
    key = "abc123"
    data = {"temperature": 72.5, "unit": "F"}
    path = cache.save_json_cache("weather", key, data)

    assert path.exists()
    assert cache.load_json_cache("weather", key) == data


def test_load_returns_none_for_missing_entry() -> None:
    assert cache.load_json_cache("empty", "no_such_key") is None


# ── corrupted / invalid JSON handling ─────────────────────────────────────


def test_load_returns_none_for_corrupted_json(tmp_path: Path) -> None:
    ns, key = "corrupt_ns", "bad_key"
    bad_file = tmp_path / ns / f"{key}.json"
    bad_file.parent.mkdir(parents=True)
    bad_file.write_text("{invalid json{{{", encoding="utf-8")

    assert cache.load_json_cache(ns, key) is None


def test_load_returns_none_for_empty_file(tmp_path: Path) -> None:
    ns, key = "empty_ns", "empty_key"
    empty_file = tmp_path / ns / f"{key}.json"
    empty_file.parent.mkdir(parents=True)
    empty_file.write_text("", encoding="utf-8")

    assert cache.load_json_cache(ns, key) is None


# ── get_or_fetch_json: cache-aside flow ──────────────────────────────────


def test_cache_miss_calls_fetch_and_writes(tmp_path: Path) -> None:
    fetch = MagicMock(return_value={"result": 42})

    result = cache.get_or_fetch_json(
        namespace="test",
        payload={"q": "miss"},
        fetch_fn=fetch,
    )

    assert result == {"result": 42}
    fetch.assert_called_once()

    key = cache.make_cache_key({"q": "miss"})
    assert cache.load_json_cache("test", key) == {"result": 42}


def test_cache_hit_avoids_fetch_call(tmp_path: Path) -> None:
    payload = {"q": "hit"}
    key = cache.make_cache_key(payload)
    cache.save_json_cache("test", key, {"cached": True})

    fetch = MagicMock()

    result = cache.get_or_fetch_json(
        namespace="test",
        payload=payload,
        fetch_fn=fetch,
    )

    assert result == {"cached": True}
    fetch.assert_not_called()


def test_force_refresh_bypasses_existing_cache(tmp_path: Path) -> None:
    payload = {"q": "refresh"}
    key = cache.make_cache_key(payload)
    cache.save_json_cache("test", key, {"old": True})

    fetch = MagicMock(return_value={"new": True})

    result = cache.get_or_fetch_json(
        namespace="test",
        payload=payload,
        fetch_fn=fetch,
        force_refresh=True,
    )

    assert result == {"new": True}
    fetch.assert_called_once()
    assert cache.load_json_cache("test", key) == {"new": True}


def test_corrupted_cache_treated_as_miss(tmp_path: Path) -> None:
    payload = {"q": "corrupt"}
    key = cache.make_cache_key(payload)

    bad_file = tmp_path / "test" / f"{key}.json"
    bad_file.parent.mkdir(parents=True)
    bad_file.write_text("NOT JSON!", encoding="utf-8")

    fetch = MagicMock(return_value={"fresh": True})

    result = cache.get_or_fetch_json(
        namespace="test",
        payload=payload,
        fetch_fn=fetch,
    )

    assert result == {"fresh": True}
    fetch.assert_called_once()


def test_cache_policy_schema_version_changes_key() -> None:
    payload = {"provider": "alpaca", "endpoint": "bars"}
    first = cache.get_or_fetch_json_result(
        namespace="test",
        payload=payload,
        fetch_fn=lambda: {"version": 1},
        policy=cache.CachePolicy(schema_version="1"),
    )
    second = cache.get_or_fetch_json_result(
        namespace="test",
        payload=payload,
        fetch_fn=lambda: {"version": 2},
        policy=cache.CachePolicy(schema_version="2"),
    )
    assert first.key != second.key
    assert second.data == {"version": 2}


def test_cache_result_reports_hit_and_miss() -> None:
    fetch = MagicMock(return_value={"result": 42})
    miss = cache.get_or_fetch_json_result(namespace="test", payload={"q": "status"}, fetch_fn=fetch)
    hit = cache.get_or_fetch_json_result(namespace="test", payload={"q": "status"}, fetch_fn=fetch)
    assert miss.status == "miss"
    assert hit.status == "hit"
    fetch.assert_called_once()


def test_expired_ttl_fetches_fresh_data(tmp_path: Path) -> None:
    payload = {"q": "expired"}
    policy = cache.CachePolicy(ttl_seconds=60, schema_version="1")
    key = cache.make_cache_key({"schema_version": "1", "request": payload})
    path = cache.save_json_cache("test", key, {"old": True})
    old_time = path.stat().st_mtime - 120
    os.utime(path, (old_time, old_time))
    fetch = MagicMock(return_value={"fresh": True})
    result = cache.get_or_fetch_json_result(
        namespace="test", payload=payload, fetch_fn=fetch, policy=policy
    )
    assert result.data == {"fresh": True}
    fetch.assert_called_once()


def test_policy_force_refresh_bypasses_cache() -> None:
    payload = {"q": "policy-refresh"}
    policy = cache.CachePolicy(force_refresh=True)
    fetch = MagicMock(side_effect=[{"version": 1}, {"version": 2}])
    first = cache.get_or_fetch_json_result(
        namespace="test", payload=payload, fetch_fn=fetch, policy=policy
    )
    second = cache.get_or_fetch_json_result(
        namespace="test", payload=payload, fetch_fn=fetch, policy=policy
    )
    assert first.data == {"version": 1}
    assert second.data == {"version": 2}


def test_atomic_save_failure_preserves_existing_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = cache.save_json_cache("test", "key", {"old": True})
    original = path.read_bytes()

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(cache.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        cache.save_json_cache("test", "key", {"new": True})
    assert path.read_bytes() == original
    assert list(path.parent.glob("*.tmp")) == []

"""Unit tests for LLMResponseCache — the persistent SQLite cache for LLM responses."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.backtest.llm_response_cache import LLMResponseCache


class TestLLMResponseCacheInit:
    def test_creates_db_file_and_schema_on_first_use(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test_cache.db"
        assert not db_path.exists()

        cache = LLMResponseCache(db_path)
        try:
            assert db_path.exists()
            # Empty cache has 0 entries, 0 hits, 0 misses
            stats = cache.stats()
            assert stats["entry_count"] == 0
            assert stats["hits"] == 0
            assert stats["misses"] == 0
        finally:
            cache.close()

    def test_reopens_existing_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test_cache.db"
        cache1 = LLMResponseCache(db_path)
        cache1.put("sys", "usr", "claude-sonnet-4-6", 0.0, {"bullish_score": 7})
        cache1.close()

        cache2 = LLMResponseCache(db_path)
        try:
            result = cache2.get("sys", "usr", "claude-sonnet-4-6", 0.0)
            assert result == {"bullish_score": 7}
        finally:
            cache2.close()


class TestLLMResponseCachePutGet:
    def test_put_then_get_round_trip(self, tmp_path: Path) -> None:
        cache = LLMResponseCache(tmp_path / "cache.db")
        try:
            response = {"bullish_score": 8, "confidence": 7, "summary": "strong"}
            cache.put("sys-A", "usr-A", "claude-sonnet-4-6", 0.0, response)
            result = cache.get("sys-A", "usr-A", "claude-sonnet-4-6", 0.0)
            assert result == response
        finally:
            cache.close()

    def test_missing_key_returns_none_and_increments_miss(self, tmp_path: Path) -> None:
        cache = LLMResponseCache(tmp_path / "cache.db")
        try:
            result = cache.get("no", "such", "claude-sonnet-4-6", 0.0)
            assert result is None
            assert cache.misses == 1
            assert cache.hits == 0
        finally:
            cache.close()

    def test_hit_increments_hit_counter(self, tmp_path: Path) -> None:
        cache = LLMResponseCache(tmp_path / "cache.db")
        try:
            cache.put("sys", "usr", "claude-sonnet-4-6", 0.0, {"x": 1})
            cache.get("sys", "usr", "claude-sonnet-4-6", 0.0)
            cache.get("sys", "usr", "claude-sonnet-4-6", 0.0)
            assert cache.hits == 2
            assert cache.misses == 0
        finally:
            cache.close()

    def test_different_temperature_is_different_key(self, tmp_path: Path) -> None:
        cache = LLMResponseCache(tmp_path / "cache.db")
        try:
            cache.put("sys", "usr", "claude-sonnet-4-6", 0.0, {"at_zero": True})
            cache.put("sys", "usr", "claude-sonnet-4-6", 0.5, {"at_zero": False})
            a = cache.get("sys", "usr", "claude-sonnet-4-6", 0.0)
            b = cache.get("sys", "usr", "claude-sonnet-4-6", 0.5)
            assert a == {"at_zero": True}
            assert b == {"at_zero": False}
        finally:
            cache.close()

    def test_put_is_idempotent_on_duplicate_key(self, tmp_path: Path) -> None:
        cache = LLMResponseCache(tmp_path / "cache.db")
        try:
            cache.put("sys", "usr", "claude-sonnet-4-6", 0.0, {"v": 1})
            cache.put("sys", "usr", "claude-sonnet-4-6", 0.0, {"v": 2})
            result = cache.get("sys", "usr", "claude-sonnet-4-6", 0.0)
            assert result == {"v": 2}
            assert cache.stats()["entry_count"] == 1
        finally:
            cache.close()


class TestLLMResponseCacheContextManager:
    def test_with_block_closes_connection_on_normal_exit(self, tmp_path: Path) -> None:
        db_path = tmp_path / "cache.db"
        with LLMResponseCache(db_path) as cache:
            cache.put("sys", "usr", "claude-sonnet-4-6", 0.0, {"x": 1})
            assert cache.get("sys", "usr", "claude-sonnet-4-6", 0.0) == {"x": 1}
        # After the with block the underlying sqlite connection is closed —
        # any further query raises ProgrammingError.
        import sqlite3

        with pytest.raises(sqlite3.ProgrammingError):
            cache._conn.execute("SELECT 1")

    def test_with_block_closes_connection_on_exception(self, tmp_path: Path) -> None:
        db_path = tmp_path / "cache.db"
        cache_ref: LLMResponseCache | None = None
        with (
            pytest.raises(RuntimeError, match="boom"),
            LLMResponseCache(db_path) as cache,
        ):
            cache_ref = cache
            raise RuntimeError("boom")
        assert cache_ref is not None
        import sqlite3

        with pytest.raises(sqlite3.ProgrammingError):
            cache_ref._conn.execute("SELECT 1")

"""Persistent SQLite cache for Anthropic API responses.

Keyed on hash(system_prompt + user_prompt + model + temperature). Used by
backtests to make LLM-mode runs reproducible — identical inputs always
return the cached response without hitting the API.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_responses (
    cache_key TEXT PRIMARY KEY,
    system_prompt TEXT NOT NULL,
    user_prompt TEXT NOT NULL,
    model TEXT NOT NULL,
    temperature REAL NOT NULL,
    response_json TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    hit_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_created_at ON llm_responses(created_at);
"""


def _compute_cache_key(system_prompt: str, user_prompt: str, model: str, temperature: float) -> str:
    """Deterministic hash of the inputs. Identical inputs always produce the same key."""
    hasher = hashlib.sha256()
    hasher.update(system_prompt.encode())
    hasher.update(b"\x00")
    hasher.update(user_prompt.encode())
    hasher.update(b"\x00")
    hasher.update(model.encode())
    hasher.update(b"\x00")
    hasher.update(f"{temperature:.6f}".encode())
    return hasher.hexdigest()


class LLMResponseCache:
    """SQLite-backed persistent cache for LLM responses.

    Thread-safe for concurrent reads; single-writer for puts (SQLite handles
    locking internally). Use one instance per backtest run.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self.hits = 0
        self.misses = 0

    def get(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
    ) -> dict | None:
        """Return cached response or None on miss. Increments hit/miss counters.

        Read-only on cache hits — does not touch the hit_count column. The
        per-row hit_count exists in the schema for future stats queries but
        is never updated at hit time, because doing so promoted the SQLite
        connection from a shared-lock reader to an exclusive-lock writer
        on every analyst call (defeats concurrent access from the LangGraph
        fan-out). Process-level hit counts are tracked via self.hits.
        """
        cache_key = _compute_cache_key(system_prompt, user_prompt, model, temperature)
        row = self._conn.execute(
            "SELECT response_json FROM llm_responses WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None:
            self.misses += 1
            return None
        self.hits += 1
        return json.loads(row[0])

    def put(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        response: dict,
    ) -> None:
        """Store a response. Upserts on the cache key (idempotent)."""
        cache_key = _compute_cache_key(system_prompt, user_prompt, model, temperature)
        self._conn.execute(
            """
            INSERT INTO llm_responses
                (cache_key, system_prompt, user_prompt, model, temperature, response_json, created_at, hit_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(cache_key) DO UPDATE SET
                response_json = excluded.response_json
            """,
            (
                cache_key,
                system_prompt,
                user_prompt,
                model,
                temperature,
                json.dumps(response),
                datetime.utcnow().isoformat(),
            ),
        )
        self._conn.commit()

    def stats(self) -> dict:
        """Return cache statistics for inspection / diagnostics."""
        row = self._conn.execute("SELECT COUNT(*) FROM llm_responses").fetchone()
        entry_count = row[0] if row else 0
        db_size_bytes = self._db_path.stat().st_size if self._db_path.exists() else 0
        return {
            "entry_count": entry_count,
            "hits": self.hits,
            "misses": self.misses,
            "db_size_bytes": db_size_bytes,
        }

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> LLMResponseCache:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

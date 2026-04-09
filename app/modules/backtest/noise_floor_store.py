"""SQLite-backed store for noise floor estimates.

Keyed by config_hash (from hash_experiment_config). Each entry is a
serialized NoiseFloor dataclass. The store uses INSERT OR REPLACE for
upsert semantics — putting a noise floor with an existing config_hash
overwrites the previous entry.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.modules.backtest.statistics import MetricNoiseFloor, NoiseFloor

_SCHEMA = """
CREATE TABLE IF NOT EXISTS noise_floors (
    config_hash TEXT PRIMARY KEY,
    config_label TEXT NOT NULL,
    skill_bundle_hash TEXT NOT NULL,
    n_runs INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    last_updated_at TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    sample_run_ids_json TEXT NOT NULL
);
"""


class NoiseFloorStore:
    """Persistent store for noise floor estimates, backed by SQLite."""

    def __init__(self, db_path: Path = Path("data/noise_floor_cache.db")) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def get(self, config_hash: str) -> NoiseFloor | None:
        """Retrieve a noise floor by config_hash. Returns None if not found."""
        row = self._conn.execute(
            "SELECT * FROM noise_floors WHERE config_hash = ?",
            (config_hash,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_noise_floor(row)

    def put(self, noise_floor: NoiseFloor) -> None:
        """Insert or replace a noise floor entry."""
        metrics_json = json.dumps({
            name: {
                "metric_name": mnf.metric_name,
                "mean": mnf.mean,
                "stddev": mnf.stddev,
                "n": mnf.n,
                "sample_values": mnf.sample_values,
            }
            for name, mnf in noise_floor.metrics.items()
        })
        sample_run_ids_json = json.dumps(noise_floor.sample_run_ids)
        self._conn.execute(
            """INSERT OR REPLACE INTO noise_floors
            (config_hash, config_label, skill_bundle_hash, n_runs,
             created_at, last_updated_at, metrics_json, sample_run_ids_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                noise_floor.config_hash,
                noise_floor.config_label,
                noise_floor.skill_bundle_hash,
                noise_floor.n_runs,
                noise_floor.created_at.isoformat(),
                noise_floor.last_updated_at.isoformat(),
                metrics_json,
                sample_run_ids_json,
            ),
        )
        self._conn.commit()

    def invalidate(self, config_hash: str) -> bool:
        """Delete a noise floor entry. Returns True if it existed."""
        cursor = self._conn.execute(
            "DELETE FROM noise_floors WHERE config_hash = ?",
            (config_hash,),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def list_all(self) -> list[NoiseFloor]:
        """Return all stored noise floors, ordered by last_updated_at desc."""
        rows = self._conn.execute(
            "SELECT * FROM noise_floors ORDER BY last_updated_at DESC"
        ).fetchall()
        return [self._row_to_noise_floor(row) for row in rows]

    def close(self) -> None:
        """Close the SQLite connection."""
        self._conn.close()

    @staticmethod
    def _row_to_noise_floor(row: tuple) -> NoiseFloor:
        (
            config_hash,
            config_label,
            skill_bundle_hash,
            n_runs,
            created_at,
            last_updated_at,
            metrics_json,
            sample_run_ids_json,
        ) = row
        metrics_raw = json.loads(metrics_json)
        metrics = {name: MetricNoiseFloor(**data) for name, data in metrics_raw.items()}
        return NoiseFloor(
            config_hash=config_hash,
            config_label=config_label,
            skill_bundle_hash=skill_bundle_hash,
            n_runs=n_runs,
            created_at=datetime.fromisoformat(created_at),
            last_updated_at=datetime.fromisoformat(last_updated_at),
            metrics=metrics,
            sample_run_ids=json.loads(sample_run_ids_json),
        )

"""Unit tests for NoiseFloorStore — SQLite-backed noise floor persistence."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.modules.backtest.noise_floor_store import NoiseFloorStore
from app.modules.backtest.statistics import MetricNoiseFloor, NoiseFloor


def _make_noise_floor(config_hash: str = "abc123", **overrides) -> NoiseFloor:
    defaults = dict(
        config_hash=config_hash,
        config_label="medium / growth / 2025-12-31",
        skill_bundle_hash="def456" * 10 + "def4",
        n_runs=5,
        created_at=datetime(2026, 4, 9, 12, 0, 0),
        last_updated_at=datetime(2026, 4, 9, 12, 0, 0),
        metrics={
            "total_return": MetricNoiseFloor(
                metric_name="total_return",
                mean=0.10,
                stddev=0.015,
                n=5,
                sample_values=[0.09, 0.10, 0.11, 0.10, 0.10],
            ),
            "sharpe_ratio": MetricNoiseFloor(
                metric_name="sharpe_ratio",
                mean=1.2,
                stddev=0.08,
                n=5,
                sample_values=[1.15, 1.20, 1.25, 1.18, 1.22],
            ),
        },
        sample_run_ids=["probe_0", "probe_1", "probe_2", "probe_3", "probe_4"],
    )
    defaults.update(overrides)
    return NoiseFloor(**defaults)


class TestNoiseFloorStore:
    def test_put_and_get_round_trip(self, tmp_path: Path) -> None:
        store = NoiseFloorStore(tmp_path / "nf.db")
        try:
            nf = _make_noise_floor()
            store.put(nf)
            loaded = store.get("abc123")
            assert loaded is not None
            assert loaded.config_hash == "abc123"
            assert loaded.n_runs == 5
            assert "total_return" in loaded.metrics
            assert loaded.metrics["total_return"].mean == 0.10
            assert loaded.metrics["total_return"].stddev == 0.015
            assert loaded.sample_run_ids == nf.sample_run_ids
        finally:
            store.close()

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        store = NoiseFloorStore(tmp_path / "nf.db")
        try:
            assert store.get("nonexistent") is None
        finally:
            store.close()

    def test_put_overwrites_existing(self, tmp_path: Path) -> None:
        store = NoiseFloorStore(tmp_path / "nf.db")
        try:
            store.put(_make_noise_floor(n_runs=5))
            store.put(_make_noise_floor(n_runs=10))
            loaded = store.get("abc123")
            assert loaded is not None
            assert loaded.n_runs == 10
        finally:
            store.close()

    def test_invalidate_existing_returns_true(self, tmp_path: Path) -> None:
        store = NoiseFloorStore(tmp_path / "nf.db")
        try:
            store.put(_make_noise_floor())
            assert store.invalidate("abc123") is True
            assert store.get("abc123") is None
        finally:
            store.close()

    def test_invalidate_missing_returns_false(self, tmp_path: Path) -> None:
        store = NoiseFloorStore(tmp_path / "nf.db")
        try:
            assert store.invalidate("nonexistent") is False
        finally:
            store.close()

    def test_list_all(self, tmp_path: Path) -> None:
        store = NoiseFloorStore(tmp_path / "nf.db")
        try:
            store.put(_make_noise_floor("hash_a"))
            store.put(_make_noise_floor("hash_b"))
            all_nfs = store.list_all()
            assert len(all_nfs) == 2
            hashes = {nf.config_hash for nf in all_nfs}
            assert hashes == {"hash_a", "hash_b"}
        finally:
            store.close()

    def test_list_all_empty(self, tmp_path: Path) -> None:
        store = NoiseFloorStore(tmp_path / "nf.db")
        try:
            assert store.list_all() == []
        finally:
            store.close()

    def test_reopens_existing_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "nf.db"
        store1 = NoiseFloorStore(db_path)
        store1.put(_make_noise_floor())
        store1.close()

        store2 = NoiseFloorStore(db_path)
        try:
            loaded = store2.get("abc123")
            assert loaded is not None
            assert loaded.n_runs == 5
        finally:
            store2.close()

    def test_sample_values_preserved(self, tmp_path: Path) -> None:
        store = NoiseFloorStore(tmp_path / "nf.db")
        try:
            nf = _make_noise_floor()
            store.put(nf)
            loaded = store.get("abc123")
            assert loaded is not None
            assert loaded.metrics["total_return"].sample_values == [0.09, 0.10, 0.11, 0.10, 0.10]
        finally:
            store.close()

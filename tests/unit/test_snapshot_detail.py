"""Snapshot positions_detail passthrough + InMemory latest_by_branch."""

from datetime import UTC, datetime, timedelta

from app.modules.backtest.state import InMemorySnapshotRepository


async def test_inmemory_create_stores_positions_detail():
    repo = InMemorySnapshotRepository()
    detail = [{"symbol": "AAA", "weight": 0.5}]
    snap = await repo.create("pf-1", "b-1", positions_detail=detail)
    assert snap.positions_detail == detail


async def test_inmemory_create_without_detail_defaults_none():
    repo = InMemorySnapshotRepository()
    snap = await repo.create("pf-1", "b-1")
    assert snap.positions_detail is None


async def test_latest_by_branch_returns_most_recent():
    repo = InMemorySnapshotRepository()
    _s1 = await repo.create("pf-1", "b-1")
    s2 = await repo.create("pf-1", "b-1")
    s1_at = datetime.now(UTC) - timedelta(days=7)
    repo._store[0].snapshot_at = s1_at  # backdate first snapshot
    latest = await repo.latest_by_branch("b-1")
    assert latest.id == s2.id


async def test_latest_by_branch_before_excludes_recent():
    repo = InMemorySnapshotRepository()
    s1 = await repo.create("pf-1", "b-1")
    await repo.create("pf-1", "b-1")
    cutoff = datetime.now(UTC) - timedelta(days=1)
    repo._store[0].snapshot_at = cutoff - timedelta(days=6)
    latest = await repo.latest_by_branch("b-1", before=cutoff)
    assert latest is not None and latest.id == s1.id


async def test_latest_by_branch_none_when_empty():
    repo = InMemorySnapshotRepository()
    assert await repo.latest_by_branch("b-1") is None

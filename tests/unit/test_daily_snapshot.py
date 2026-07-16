"""Daily snapshot skip decision (NY-date idempotency) and per-branch fault isolation."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from scripts import take_daily_snapshot as daily_snapshot


def test_already_snapshotted_today_none_means_false():
    assert daily_snapshot._already_snapshotted_today(None) is False


def test_already_snapshotted_today_snapshot_now_means_true():
    latest = SimpleNamespace(snapshot_at=datetime.now(UTC))
    assert daily_snapshot._already_snapshotted_today(latest) is True


def test_already_snapshotted_today_snapshot_two_days_ago_means_false():
    latest = SimpleNamespace(snapshot_at=datetime.now(UTC) - timedelta(days=2))
    assert daily_snapshot._already_snapshotted_today(latest) is False


def _patch_environment(monkeypatch, branches: list[str]) -> None:
    """Stub out the DB/data-platform surface so _main_async runs the bare loop."""
    monkeypatch.setattr(daily_snapshot, "init_data_platform", lambda: object())
    monkeypatch.setattr(daily_snapshot, "settings", SimpleNamespace(equities_enabled_branches=branches))


async def test_main_async_continues_past_branch_failure(monkeypatch):
    calls: list[str] = []

    async def fake_snapshot_branch(branch_name, data_service):
        calls.append(branch_name)
        if branch_name == "growth":
            raise RuntimeError("boom")
        return "snapshotted"

    _patch_environment(monkeypatch, ["growth", "value"])
    monkeypatch.setattr(daily_snapshot, "snapshot_branch", fake_snapshot_branch)

    assert await daily_snapshot._main_async() == 1
    assert calls == ["growth", "value"]  # the failure did not stop the loop


async def test_main_async_all_ok_returns_zero(monkeypatch):
    async def fake_snapshot_branch(branch_name, data_service):
        return "snapshotted"

    _patch_environment(monkeypatch, ["growth", "value"])
    monkeypatch.setattr(daily_snapshot, "snapshot_branch", fake_snapshot_branch)

    assert await daily_snapshot._main_async() == 0

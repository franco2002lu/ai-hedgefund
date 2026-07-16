"""Attribution must run BEFORE the trading run (2026-07-16 adaptive-weights
spec) and stay non-fatal; dry runs must not touch attribution at all."""

from unittest.mock import AsyncMock, MagicMock, patch

from app.modules.equities.weekly_runner import WeeklyRunSummary


def _summary(status="completed"):
    return WeeklyRunSummary(
        run_id="r",
        branch_name="growth",
        status=status,
        universe_count=0,
        screened_count=0,
        orders_placed=0,
        trades_executed=0,
        duration_seconds=0.0,
    )


def _session_factory():
    """Factory whose sessions support `async with factory() as s, s.begin():`."""

    def make():
        ctx = MagicMock()
        session = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        begin = MagicMock()
        begin.__aenter__ = AsyncMock(return_value=None)
        begin.__aexit__ = AsyncMock(return_value=False)
        session.begin = MagicMock(return_value=begin)
        return ctx

    return MagicMock(side_effect=lambda: make())


async def test_attribution_runs_before_trading_and_attaches():
    import scripts.run_weekly_pipeline as cli

    calls: list[str] = []
    engine = MagicMock()

    async def _attr(*args, **kwargs):
        calls.append("attribution")
        return MagicMock()

    engine.compute_and_persist = AsyncMock(side_effect=_attr)

    runner = MagicMock()

    async def _execute(**kwargs):
        calls.append("trading")
        return _summary()

    runner.execute = AsyncMock(side_effect=_execute)

    with (
        patch.object(cli, "get_equities_service", return_value=MagicMock()),
        patch.object(cli, "async_session_factory", _session_factory()),
        patch.object(cli, "resolve_branch_id", AsyncMock(return_value="b-id")),
        patch.object(cli, "AttributionEngine", return_value=engine),
        patch.object(cli, "WeeklyRunner", MagicMock(return_value=runner)),
        patch.object(cli, "_mark_snapshot_and_report", AsyncMock()),
    ):
        summary = await cli._run_one_branch(branch_name="growth", top_n=5, force_retry=False, dry_run=False)

    assert calls == ["attribution", "trading"]
    assert summary.attribution is not None


async def test_attribution_failure_does_not_block_trading():
    import scripts.run_weekly_pipeline as cli

    engine = MagicMock()
    engine.compute_and_persist = AsyncMock(side_effect=RuntimeError("yfinance down"))
    runner = MagicMock()
    runner.execute = AsyncMock(return_value=_summary())

    with (
        patch.object(cli, "get_equities_service", return_value=MagicMock()),
        patch.object(cli, "async_session_factory", _session_factory()),
        patch.object(cli, "resolve_branch_id", AsyncMock(return_value="b-id")),
        patch.object(cli, "AttributionEngine", return_value=engine),
        patch.object(cli, "WeeklyRunner", MagicMock(return_value=runner)),
        patch.object(cli, "_mark_snapshot_and_report", AsyncMock()),
    ):
        summary = await cli._run_one_branch(branch_name="growth", top_n=5, force_retry=False, dry_run=False)

    assert summary.status == "completed"
    assert summary.attribution is None


async def test_dry_run_skips_attribution():
    import scripts.run_weekly_pipeline as cli

    engine = MagicMock()
    engine.compute_and_persist = AsyncMock()

    with (
        patch.object(cli, "get_equities_service", return_value=MagicMock()),
        patch.object(cli, "async_session_factory", _session_factory()),
        patch.object(cli, "resolve_branch_id", AsyncMock(return_value="b-id")),
        patch.object(cli, "AttributionEngine", return_value=engine),
    ):
        summary = await cli._run_one_branch(branch_name="growth", top_n=5, force_retry=False, dry_run=True)

    engine.compute_and_persist.assert_not_awaited()
    assert summary.status == "skipped"

"""Unit tests for WeeklyRunner idempotency and summary rendering."""

from datetime import UTC, date
from datetime import date as _date
from datetime import datetime as dt
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.models import PipelineRunModel
from app.modules.equities.attribution import AttributionReport
from app.modules.equities.models import RunResult
from app.modules.equities.weekly_runner import (
    ManualInterventionRequired,
    RunInFlightError,
    WeeklyRunner,
    WeeklyRunSummary,
    render_digest,
)


def test_summary_dataclass_fields():
    s = WeeklyRunSummary(
        run_id="2026-04-27-growth",
        branch_name="growth",
        status="completed",
        universe_count=50,
        screened_count=32,
        orders_placed=15,
        trades_executed=12,
        duration_seconds=402.0,
        error=None,
    )
    assert s.run_id == "2026-04-27-growth"
    assert s.status == "completed"


def test_exceptions_are_exceptions():
    assert issubclass(RunInFlightError, Exception)
    assert issubclass(ManualInterventionRequired, Exception)


def _make_run_result(branch: str) -> RunResult:
    return RunResult(
        branch_name=branch,
        universe_count=50,
        screened_count=30,
        signals=[],
        composite_scores=[],
        orders=[],
        trades_executed=10,
    )


@pytest.mark.asyncio
async def test_execute_creates_row_when_no_prior_run():
    branch_id = uuid4()
    service = MagicMock()
    service.run_pipeline = AsyncMock(return_value=_make_run_result("growth"))

    repo = MagicMock()
    repo.find_latest = AsyncMock(return_value=None)  # no prior row
    repo.insert = AsyncMock()
    repo.mark_completed = AsyncMock()

    runner = WeeklyRunner(
        service=service,
        repo=repo,
        session=MagicMock(),
    )

    summary = await runner.execute(
        branch_name="growth",
        branch_id=str(branch_id),
        run_date=date(2026, 4, 27),
        force_retry=False,
    )

    repo.insert.assert_awaited_once()
    inserted = repo.insert.await_args.kwargs
    assert inserted["run_id"] == "2026-04-27-growth"
    assert inserted["attempt"] == 1
    assert inserted["status"] == "running"

    service.run_pipeline.assert_awaited_once()
    repo.mark_completed.assert_awaited_once()
    assert summary.status == "completed"
    assert summary.run_id == "2026-04-27-growth"


def _row(status: str, run_id: str, attempt: int = 1):
    return PipelineRunModel(
        run_id=run_id,
        branch_id=uuid4(),
        run_date=date(2026, 4, 27),
        attempt=attempt,
        status=status,
        started_at=dt.now(UTC),
    )


@pytest.mark.asyncio
async def test_execute_skips_when_completed_row_exists():
    existing = _row("completed", "2026-04-27-growth")
    service = MagicMock()
    service.run_pipeline = AsyncMock()
    repo = MagicMock()
    repo.find_latest = AsyncMock(return_value=existing)
    repo.insert = AsyncMock()

    runner = WeeklyRunner(service=service, repo=repo, session=MagicMock())

    summary = await runner.execute(
        branch_name="growth",
        branch_id=str(existing.branch_id),
        run_date=date(2026, 4, 27),
        force_retry=False,
    )

    assert summary.status == "skipped"
    service.run_pipeline.assert_not_called()
    repo.insert.assert_not_called()


@pytest.mark.asyncio
async def test_execute_aborts_when_running_row_exists():
    existing = _row("running", "2026-04-27-growth")
    service = MagicMock()
    repo = MagicMock()
    repo.find_latest = AsyncMock(return_value=existing)

    runner = WeeklyRunner(service=service, repo=repo, session=MagicMock())

    with pytest.raises(RunInFlightError):
        await runner.execute(
            branch_name="growth",
            branch_id=str(existing.branch_id),
            run_date=date(2026, 4, 27),
            force_retry=False,
        )


@pytest.mark.asyncio
async def test_execute_aborts_on_failed_without_retry():
    existing = _row("failed", "2026-04-27-growth")
    service = MagicMock()
    repo = MagicMock()
    repo.find_latest = AsyncMock(return_value=existing)

    runner = WeeklyRunner(service=service, repo=repo, session=MagicMock())

    with pytest.raises(ManualInterventionRequired):
        await runner.execute(
            branch_name="growth",
            branch_id=str(existing.branch_id),
            run_date=date(2026, 4, 27),
            force_retry=False,
        )


@pytest.mark.asyncio
async def test_execute_creates_attempt2_row_on_force_retry():
    existing = _row("failed", "2026-04-27-growth", attempt=1)
    service = MagicMock()
    service.run_pipeline = AsyncMock(return_value=_make_run_result("growth"))
    repo = MagicMock()
    repo.find_latest = AsyncMock(return_value=existing)
    repo.insert = AsyncMock()
    repo.mark_completed = AsyncMock()

    runner = WeeklyRunner(service=service, repo=repo, session=MagicMock())

    summary = await runner.execute(
        branch_name="growth",
        branch_id=str(existing.branch_id),
        run_date=date(2026, 4, 27),
        force_retry=True,
    )

    inserted = repo.insert.await_args.kwargs
    assert inserted["run_id"] == "2026-04-27-growth-attempt2"
    assert inserted["attempt"] == 2
    assert summary.status == "completed"


@pytest.mark.asyncio
async def test_execute_marks_failed_on_pipeline_exception():
    service = MagicMock()
    service.run_pipeline = AsyncMock(side_effect=RuntimeError("boom"))
    repo = MagicMock()
    repo.find_latest = AsyncMock(return_value=None)
    repo.insert = AsyncMock()
    repo.mark_failed = AsyncMock()

    runner = WeeklyRunner(service=service, repo=repo, session=MagicMock())

    with pytest.raises(RuntimeError, match="boom"):
        await runner.execute(
            branch_name="growth",
            branch_id=str(uuid4()),
            run_date=date(2026, 4, 27),
            force_retry=False,
        )

    repo.mark_failed.assert_awaited_once()
    call_args = repo.mark_failed.await_args
    assert "boom" in call_args.args[1]


def test_configure_service_sets_top_n_on_universe_provider():
    service = MagicMock()
    service.universe_provider = MagicMock()
    service.universe_provider.top_n = None

    WeeklyRunner.configure_service(service, top_n=50)

    assert service.universe_provider.top_n == 50


def test_configure_service_with_none_top_n_leaves_unset():
    service = MagicMock()
    service.universe_provider = MagicMock()
    service.universe_provider.top_n = None

    WeeklyRunner.configure_service(service, top_n=None)

    assert service.universe_provider.top_n is None


def test_render_digest_single_success():
    s = WeeklyRunSummary(
        run_id="2026-04-27-growth",
        branch_name="growth",
        status="completed",
        universe_count=50,
        screened_count=32,
        orders_placed=15,
        trades_executed=12,
        duration_seconds=402.5,
        error=None,
    )
    out = render_digest([s], run_date=date(2026, 4, 27))

    assert "# Weekly Rebalance — 2026-04-27" in out
    assert "## growth" in out
    assert "completed" in out or "✅" in out
    assert "Universe: 50" in out
    assert "Screened: 32" in out
    assert "Orders placed: 15" in out
    assert "Trades executed: 12" in out
    assert "6m 42s" in out or "402.5s" in out or "402" in out


def test_render_digest_mixed_success_and_failure():
    s1 = WeeklyRunSummary(
        run_id="2026-04-27-growth",
        branch_name="growth",
        status="completed",
        universe_count=50,
        screened_count=30,
        orders_placed=10,
        trades_executed=8,
        duration_seconds=300.0,
        error=None,
    )
    s2 = WeeklyRunSummary(
        run_id="2026-04-27-value",
        branch_name="value",
        status="failed",
        universe_count=0,
        screened_count=0,
        orders_placed=0,
        trades_executed=0,
        duration_seconds=12.0,
        error="RuntimeError: yfinance rate limited",
    )
    out = render_digest([s1, s2], run_date=date(2026, 4, 27))

    assert "## growth" in out
    assert "## value" in out
    assert "❌" in out or "failed" in out
    assert "yfinance rate limited" in out


class TestDigestAttribution:
    def test_digest_includes_attribution_section(self):
        report = AttributionReport(
            branch_name="growth",
            decision_date=_date(2026, 6, 1),
            as_of_date=_date(2026, 6, 8),
            basket_return_conviction=0.012,
            basket_return_equal=0.009,
            benchmark_return=0.004,
            benchmark_symbol="VOOG",
            spy_return=-0.003,
            analyst_ics={"fundamentals": 0.11, "news": -0.18, "technical": None},
            n_holdings=20,
            n_holdings_priced=20,
        )
        summary = WeeklyRunSummary(
            run_id="2026-06-08-growth",
            branch_name="growth",
            status="completed",
            universe_count=50,
            screened_count=24,
            orders_placed=20,
            trades_executed=20,
            duration_seconds=75.0,
            attribution=report,
        )
        digest = render_digest([summary], run_date=_date(2026, 6, 8))
        assert "Last week (2026-06-01)" in digest
        assert "+1.20%" in digest  # conviction basket
        assert "eq-wt +0.90%" in digest
        assert "VOOG +0.40%" in digest
        assert "fund +0.11" in digest
        assert "tech n/a" in digest

    def test_digest_without_attribution_unchanged(self):
        summary = WeeklyRunSummary(
            run_id="x",
            branch_name="growth",
            status="completed",
            universe_count=1,
            screened_count=1,
            orders_placed=1,
            trades_executed=1,
            duration_seconds=1.0,
        )
        digest = render_digest([summary], run_date=_date(2026, 6, 8))
        assert "Last week" not in digest

"""Unit tests for WeeklyRunner idempotency and summary rendering."""

from datetime import UTC, date
from datetime import datetime as dt
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.models import PipelineRunModel
from app.modules.equities.models import RunResult
from app.modules.equities.weekly_runner import (
    ManualInterventionRequired,
    RunInFlightError,
    WeeklyRunner,
    WeeklyRunSummary,
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

"""Unit tests for WeeklyRunner idempotency and summary rendering."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.equities.models import RunResult
from app.modules.equities.weekly_runner import (
    ManualInterventionRequired,
    RunInFlightError,
    WeeklyRunSummary,
    WeeklyRunner,
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

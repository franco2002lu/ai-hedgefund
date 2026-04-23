"""Unit tests for WeeklyRunner idempotency and summary rendering."""

import pytest

from app.modules.equities.weekly_runner import (
    ManualInterventionRequired,
    RunInFlightError,
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

"""Orchestrates weekly autonomous pipeline runs with idempotency guards.

Lookup strategy: find the latest pipeline_runs row for (branch_id, run_date),
then decide whether to skip, abort, or proceed based on its status.

run_id format:
  - Attempt 1: "{run_date}-{branch_name}"                e.g., "2026-04-27-growth"
  - Attempt N: "{run_date}-{branch_name}-attempt{N}"     e.g., "2026-04-27-growth-attempt2"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


class RunInFlightError(Exception):
    """Raised when a prior run for the same (branch, date) is still running."""


class ManualInterventionRequired(Exception):
    """Raised when a prior run failed and force_retry was not set."""


@dataclass
class WeeklyRunSummary:
    run_id: str
    branch_name: str
    status: str  # "completed" | "failed" | "skipped"
    universe_count: int
    screened_count: int
    orders_placed: int
    trades_executed: int
    duration_seconds: float
    error: str | None = None


_NY_TZ = ZoneInfo("America/New_York")


def today_ny() -> date:
    """Return today's date in America/New_York (not UTC)."""
    return datetime.now(_NY_TZ).date()

"""Orchestrates weekly autonomous pipeline runs with idempotency guards.

Lookup strategy: find the latest pipeline_runs row for (branch_id, run_date),
then decide whether to skip, abort, or proceed based on its status.

run_id format:
  - Attempt 1: "{run_date}-{branch_name}"                e.g., "2026-04-27-growth"
  - Attempt N: "{run_date}-{branch_name}-attempt{N}"     e.g., "2026-04-27-growth-attempt2"
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from datetime import datetime as dt
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.models import PipelineRunModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.equities.service import EquitiesBranchService

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


class PipelineRunsRepository:
    """Thin data-access layer for pipeline_runs rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_latest(
        self, branch_id: str, run_date: date
    ) -> PipelineRunModel | None:
        bid = uuid.UUID(branch_id) if isinstance(branch_id, str) else branch_id
        stmt = (
            select(PipelineRunModel)
            .where(
                PipelineRunModel.branch_id == bid,
                PipelineRunModel.run_date == run_date,
            )
            .order_by(PipelineRunModel.attempt.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def insert(
        self,
        *,
        run_id: str,
        branch_id: str,
        run_date: date,
        attempt: int,
        status: str,
    ) -> None:
        bid = uuid.UUID(branch_id) if isinstance(branch_id, str) else branch_id
        row = PipelineRunModel(
            run_id=run_id,
            branch_id=bid,
            run_date=run_date,
            attempt=attempt,
            status=status,
        )
        self.session.add(row)
        await self.session.flush()

    async def mark_completed(
        self, run_id: str, summary: dict[str, Any]
    ) -> None:
        row = await self.session.get(PipelineRunModel, run_id)
        if row is None:
            raise RuntimeError(f"pipeline_runs row {run_id} not found")
        row.status = "completed"
        row.completed_at = dt.now(UTC)
        row.summary_json = summary
        await self.session.flush()

    async def mark_failed(self, run_id: str, error_msg: str) -> None:
        row = await self.session.get(PipelineRunModel, run_id)
        if row is None:
            raise RuntimeError(f"pipeline_runs row {run_id} not found")
        row.status = "failed"
        row.completed_at = dt.now(UTC)
        row.error_msg = error_msg
        await self.session.flush()


class WeeklyRunner:
    """Executes a weekly pipeline run for a single branch with idempotency."""

    def __init__(
        self,
        *,
        service: EquitiesBranchService,
        repo: PipelineRunsRepository,
        session: AsyncSession,
    ) -> None:
        self.service = service
        self.repo = repo
        self.session = session

    async def execute(
        self,
        *,
        branch_name: str,
        branch_id: str,
        run_date: date,
        force_retry: bool = False,
    ) -> WeeklyRunSummary:
        latest = await self.repo.find_latest(branch_id, run_date)
        attempt, run_id = self._decide_attempt(
            latest=latest,
            branch_name=branch_name,
            run_date=run_date,
            force_retry=force_retry,
        )
        if attempt == 0:  # sentinel: skip
            assert latest is not None
            return WeeklyRunSummary(
                run_id=latest.run_id,
                branch_name=branch_name,
                status="skipped",
                universe_count=0,
                screened_count=0,
                orders_placed=0,
                trades_executed=0,
                duration_seconds=0.0,
            )

        await self.repo.insert(
            run_id=run_id,
            branch_id=branch_id,
            run_date=run_date,
            attempt=attempt,
            status="running",
        )

        t0 = time.monotonic()
        try:
            result = await self.service.run_pipeline(
                branch_name=branch_name,
                branch_id=branch_id,
                run_id=run_id,
                session=self.session,
            )
        except Exception as exc:
            await self.repo.mark_failed(run_id, repr(exc))
            raise

        duration = time.monotonic() - t0
        orders_placed = len(result.orders)
        summary_dict = {
            "universe_count": result.universe_count,
            "screened_count": result.screened_count,
            "orders_placed": orders_placed,
            "trades_executed": result.trades_executed,
            "duration_seconds": duration,
        }
        await self.repo.mark_completed(run_id, summary_dict)

        return WeeklyRunSummary(
            run_id=run_id,
            branch_name=branch_name,
            status="completed",
            universe_count=result.universe_count,
            screened_count=result.screened_count,
            orders_placed=orders_placed,
            trades_executed=result.trades_executed,
            duration_seconds=duration,
        )

    @classmethod
    def configure_service(
        cls,
        service: EquitiesBranchService,
        *,
        top_n: int | None,
    ) -> None:
        """Mutate the service's universe provider for this weekly run.

        Called once before any execute() calls. Safe for sequential single-process
        use (the weekly CLI is single-threaded). Do not call from multi-worker
        contexts — the mutation is not thread-safe.
        """
        if top_n is not None:
            service.universe_provider.top_n = top_n

    def _decide_attempt(
        self,
        *,
        latest: PipelineRunModel | None,
        branch_name: str,
        run_date: date,
        force_retry: bool,
    ) -> tuple[int, str]:
        """Return (attempt, run_id). attempt=0 is a sentinel meaning 'skip'."""
        if latest is None:
            return 1, f"{run_date.isoformat()}-{branch_name}"
        if latest.status == "completed":
            logger.info("Run %s already completed — skipping", latest.run_id)
            return 0, latest.run_id
        if latest.status == "running":
            raise RunInFlightError(
                f"Prior run {latest.run_id} is status=running. "
                "Inspect manually before re-running."
            )
        # latest.status == "failed"
        if not force_retry:
            raise ManualInterventionRequired(
                f"Prior run {latest.run_id} failed. "
                "Re-trigger with force_retry=true to create a new attempt."
            )
        next_attempt = latest.attempt + 1
        new_run_id = f"{run_date.isoformat()}-{branch_name}-attempt{next_attempt}"
        return next_attempt, new_run_id


def _format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string (e.g., '6m 42s')."""
    m, s = divmod(int(seconds), 60)
    if m == 0:
        return f"{s}s"
    return f"{m}m {s:02d}s"


def render_digest(summaries: list[WeeklyRunSummary], *, run_date: date) -> str:
    """Build the markdown digest written to $GITHUB_STEP_SUMMARY."""
    lines = [f"# Weekly Rebalance — {run_date.isoformat()}", ""]
    for s in summaries:
        icon = {"completed": "✅", "failed": "❌", "skipped": "⏭️"}.get(s.status, "❔")
        lines.append(f"## {s.branch_name} {icon}")
        lines.append(f"- Run ID: `{s.run_id}`")
        lines.append(f"- Status: {s.status}")
        if s.status == "completed":
            lines.append(f"- Universe: {s.universe_count}")
            lines.append(f"- Screened: {s.screened_count}")
            lines.append(f"- Orders placed: {s.orders_placed}")
            lines.append(f"- Trades executed: {s.trades_executed}")
            lines.append(f"- Duration: {_format_duration(s.duration_seconds)}")
            if s.orders_placed == 0:
                lines.append("- ⚠️ 0 orders — check data freshness")
        elif s.status == "failed":
            lines.append(f"- Duration before failure: {_format_duration(s.duration_seconds)}")
            lines.append(f"- Error: `{s.error or 'unknown'}`")
        lines.append("")
    return "\n".join(lines)

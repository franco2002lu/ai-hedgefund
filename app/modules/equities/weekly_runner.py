"""Orchestrates weekly autonomous pipeline runs with idempotency guards.

Lookup strategy: find the latest pipeline_runs row for (branch_id, run_date),
then decide whether to skip, abort, or proceed based on its status.

run_id format:
  - Attempt 1: "{run_date}-{branch_name}"                e.g., "2026-04-27-growth"
  - Attempt N: "{run_date}-{branch_name}-attempt{N}"     e.g., "2026-04-27-growth-attempt2"

Transaction isolation design:
  Each state-change to pipeline_runs lives in its own dedicated session so that
  failures in the trading transaction cannot roll back bookkeeping rows:

    Txn 1 (bk1)  — insert status="running"  → committed before trading starts
    Txn 2        — run_fn (trading data)     → committed if pipeline succeeds
    Txn 3 (bk2)  — mark status="completed"  → committed only after trading data is durable
    Txn 3'(bk-f) — mark status="failed"     → committed if trading txn raised
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from datetime import datetime as dt
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.models import PipelineRunModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.equities.attribution import AttributionReport
    from app.modules.equities.models import RunResult
    from app.modules.equities.service import EquitiesBranchService

logger = logging.getLogger(__name__)


class RunInFlightError(Exception):
    """Raised when a prior run for the same (branch, date) is still running."""


class ManualInterventionRequired(Exception):
    """Raised when a prior run failed and force_retry was not set."""


@dataclass
class PortfolioReport:
    """Marked portfolio state attached to a WeeklyRunSummary for the digest."""

    nav: float
    cash: float
    cash_pct: float
    unrealized_pnl: float
    realized_pnl: float
    initial_capital: float
    inception_return_pct: float | None
    wow_return_pct: float | None
    top_holdings: list[dict]  # [{symbol, weight}, ...] best-first
    trades: list[dict]  # [{symbol, side, quantity, price, notional}, ...]
    unpriced: int = 0


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
    attribution: AttributionReport | None = None
    portfolio_report: PortfolioReport | None = None


_NY_TZ = ZoneInfo("America/New_York")


def today_ny() -> date:
    """Return today's date in America/New_York (not UTC)."""
    return datetime.now(_NY_TZ).date()


def ny_date(ts: datetime) -> date:
    """The America/New_York calendar date of an aware timestamp."""
    return ts.astimezone(_NY_TZ).date()


class PipelineRunsRepository:
    """Thin data-access layer for pipeline_runs rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_latest(self, branch_id: str, run_date: date) -> PipelineRunModel | None:
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

    async def mark_completed(self, run_id: str, summary: dict[str, Any]) -> None:
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
    """Executes a weekly pipeline run for a single branch with idempotency.

    Uses three dedicated transactions to ensure pipeline_runs state survives
    trading failures:

      bk1  — insert status="running", committed immediately so other processes
              can see the in-flight guard before trading begins.
      trade — run_fn executes all trading work; commits on success, rolls back
              on exception (trading data never partially persists).
      bk2   — mark status="completed", only reached after trading data is durable.
      bk-f  — mark status="failed", opened in the except branch; the original
              pipeline exception is always re-raised regardless of whether this
              bookkeeping step itself errors.

    Hard-kill windows: a kill during the trading transaction leaves a committed
    'running' row with no trades; a kill between the trading commit and
    bookkeeping txn 2 leaves a 'running' row WITH trades committed. Both require
    the manual recovery flow (flip status to 'failed', then force_retry).
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any],
    ) -> None:
        self.session_factory = session_factory

    async def execute(
        self,
        *,
        branch_name: str,
        branch_id: str,
        run_date: date,
        force_retry: bool = False,
        run_fn: Callable[[AsyncSession, str], Awaitable[RunResult]],
    ) -> WeeklyRunSummary:
        # ------------------------------------------------------------------
        # Bookkeeping txn 1: decide attempt + insert "running" row.
        # This transaction commits immediately so the row is durable before
        # the trading session opens.  Other processes can observe it via
        # the RunInFlightError guard.
        # ------------------------------------------------------------------
        async with self.session_factory() as bk, bk.begin():
            repo = PipelineRunsRepository(bk)
            latest = await repo.find_latest(branch_id, run_date)
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
            await repo.insert(
                run_id=run_id,
                branch_id=branch_id,
                run_date=run_date,
                attempt=attempt,
                status="running",
            )
        # bk1 committed here — "running" row is now visible to other processes

        # ------------------------------------------------------------------
        # Trading transaction: all portfolio / trade data committed atomically.
        # If anything raises, we catch it, persist "failed" in a dedicated
        # session, then re-raise.
        # ------------------------------------------------------------------
        t0 = time.monotonic()
        try:
            async with self.session_factory() as session, session.begin():
                result = await run_fn(session, run_id)
            # trading data durably committed here
        except Exception as exc:
            await self._mark_failed(run_id, repr(exc))
            raise

        # ------------------------------------------------------------------
        # Bookkeeping txn 2: mark "completed" — only reached after trading
        # data is durable.
        # ------------------------------------------------------------------
        duration = time.monotonic() - t0
        orders_placed = len(result.orders)
        summary_dict = {
            "universe_count": result.universe_count,
            "screened_count": result.screened_count,
            "orders_placed": orders_placed,
            "trades_executed": result.trades_executed,
            "duration_seconds": duration,
        }
        await self._mark_completed(run_id, summary_dict)

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

    async def _mark_completed(self, run_id: str, summary: dict[str, Any]) -> None:
        """Persist completed status in a dedicated session."""
        async with self.session_factory() as bk, bk.begin():
            repo = PipelineRunsRepository(bk)
            await repo.mark_completed(run_id, summary)

    async def _mark_failed(self, run_id: str, error_msg: str) -> None:
        """Persist failed status in a dedicated session.

        Any exception raised here is logged and suppressed so the original
        pipeline exception remains the one the caller sees.
        """
        try:
            async with self.session_factory() as bk, bk.begin():
                repo = PipelineRunsRepository(bk)
                await repo.mark_failed(run_id, error_msg)
        except Exception:
            logger.warning(
                "Failed to persist mark_failed for run_id=%s — the original pipeline error will still propagate",
                run_id,
                exc_info=True,
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
            raise RunInFlightError(f"Prior run {latest.run_id} is status=running. Inspect manually before re-running.")
        # latest.status == "failed"
        if not force_retry:
            raise ManualInterventionRequired(
                f"Prior run {latest.run_id} failed. Re-trigger with force_retry=true to create a new attempt."
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


def _fmt_pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.2%}"


def _fmt_ic(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.2f}"


def _fmt_money(x: float) -> str:
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"


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
            if s.attribution is not None:
                a = s.attribution
                ics = a.analyst_ics
                lines.append(
                    f"- Last week ({a.decision_date.isoformat()}): "
                    f"basket {_fmt_pct(a.basket_return_conviction)} "
                    f"(eq-wt {_fmt_pct(a.basket_return_equal)}) vs "
                    f"{a.benchmark_symbol} {_fmt_pct(a.benchmark_return)}, "
                    f"SPY {_fmt_pct(a.spy_return)} · "
                    f"IC fund {_fmt_ic(ics.get('fundamentals'))} / "
                    f"news {_fmt_ic(ics.get('news'))} / "
                    f"tech {_fmt_ic(ics.get('technical'))}"
                )
            if s.portfolio_report is not None:
                r = s.portfolio_report
                # No baseline (initial_capital == 0 → inception_return_pct is
                # None): render "n/a" with no dollar delta — `nav - 0` would
                # present the entire NAV as profit.
                if r.inception_return_pct is None:
                    inception = "n/a"
                else:
                    inception = f"{_fmt_pct(r.inception_return_pct)} / {_fmt_money(r.nav - r.initial_capital)}"
                lines.append(
                    f"- NAV: {_fmt_money(r.nav)} (WoW {_fmt_pct(r.wow_return_pct)}, since inception {inception})"
                )
                lines.append(
                    f"- Cash: {_fmt_money(r.cash)} ({r.cash_pct:.1%}) · "
                    f"Unrealized P&L: {_fmt_money(r.unrealized_pnl)} · "
                    f"Realized: {_fmt_money(r.realized_pnl)}"
                )
                if r.top_holdings:
                    tops = ", ".join(f"{h['symbol']} {h['weight']:.1%}" for h in r.top_holdings[:5])
                    lines.append(f"- Top holdings: {tops}")
                if r.trades:
                    lines.append("- Trades this run:")
                    lines.append("")
                    lines.append("  | Symbol | Side | Qty | Fill | Notional |")
                    lines.append("  |---|---|---|---|---|")
                    for t in r.trades:
                        lines.append(
                            f"  | {t['symbol']} | {t['side']} | {t['quantity']:g} "
                            f"| ${t['price']:,.2f} | {_fmt_money(t['notional'])} |"
                        )
                    lines.append("")
                if r.unpriced > 0:
                    lines.append(f"- ⚠️ {r.unpriced} position(s) unpriced — carried at cost basis")
                if r.cash < 0:
                    lines.append("- ⚠️ Negative cash balance — check order sizing")
        elif s.status == "failed":
            lines.append(f"- Duration before failure: {_format_duration(s.duration_seconds)}")
            lines.append(f"- Error: `{s.error or 'unknown'}`")
        lines.append("")
    return "\n".join(lines)

"""CLI entrypoint for the weekly autonomous paper-trading pipeline.

Reads configuration from environment variables (HEDGE_EQUITIES_TOP_N,
HEDGE_EQUITIES_ENABLED_BRANCHES) and from workflow_dispatch inputs
exported into the env by the GH Actions workflow.

Exit codes:
  0 — all enabled branches ran (completed or skipped as idempotent)
  1 — at least one branch failed
  2 — infrastructure error (DB unreachable, missing secret, etc.)

Writes the markdown digest to the file named by $GITHUB_STEP_SUMMARY
(if set — it is set on GH Actions). Otherwise prints to stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # Make ANTHROPIC_API_KEY and HEDGE_* available

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.connection import async_session_factory  # noqa: E402
from app.db.models import BranchModel  # noqa: E402
from app.dependencies import get_equities_service, get_paper_broker, init_services  # noqa: E402
from app.modules.data_platform.adapters.yahoo_finance import YahooFinanceAdapter  # noqa: E402
from app.modules.data_platform.cache import DataCache  # noqa: E402
from app.modules.data_platform.rate_limiter import RateLimiter  # noqa: E402
from app.modules.data_platform.service import DataPlatformService  # noqa: E402
from app.modules.equities.attribution import AttributionEngine  # noqa: E402
from app.modules.equities.weekly_runner import (  # noqa: E402
    ManualInterventionRequired,
    PipelineRunsRepository,
    RunInFlightError,
    WeeklyRunner,
    WeeklyRunSummary,
    render_digest,
    today_ny,
)
from app.modules.event_log.repository import PostgresEventLogRepository  # noqa: E402
from app.modules.portfolio.repository import (  # noqa: E402
    PostgresPortfolioRepository,
    PostgresPositionRepository,
    PostgresSnapshotRepository,
)
from app.modules.portfolio.service import PortfolioService  # noqa: E402
from app.modules.trade_execution.repository import (  # noqa: E402
    PostgresOrderRepository,
    PostgresTradeRepository,
)
from app.modules.trade_execution.service import TradeExecutionService  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("yfinance").setLevel(logging.WARNING)
logger = logging.getLogger("weekly_pipeline")


def _init_data_platform() -> DataPlatformService:
    """Reproduce what app/main.py::lifespan does for DataPlatformService."""
    yahoo = YahooFinanceAdapter()
    registry = {
        "prices": {"equity": [yahoo], "crypto": [yahoo], "all": [yahoo]},
        "fundamentals": {"equity": [yahoo]},
        "news": {"all": [yahoo]},
    }
    return DataPlatformService(
        adapter_registry=registry,
        cache=DataCache(),
        rate_limiter=RateLimiter(),
    )


async def _resolve_branch_id(session, branch_name: str) -> str:
    """Resolve a short branch key (e.g., 'growth') to its branches.id UUID.

    Uses a case-insensitive substring match (branches are named 'Equities Growth'
    but the CLI accepts 'growth'). If multiple branches match, raises — the
    caller should pick a more specific name rather than letting us guess.
    """
    stmt = select(BranchModel).where(BranchModel.name.ilike(f"%{branch_name}%"))
    result = await session.execute(stmt)
    rows = result.scalars().all()
    if not rows:
        raise RuntimeError(f"No branch found matching '{branch_name}'")
    if len(rows) > 1:
        names = sorted(r.name for r in rows)
        raise RuntimeError(
            f"Branch name '{branch_name}' matched {len(rows)} branches: {names}. "
            "Use a more specific name."
        )
    return str(rows[0].id)


async def _run_one_branch(
    *,
    branch_name: str,
    top_n: int | None,
    force_retry: bool,
    dry_run: bool,
) -> WeeklyRunSummary:
    equities_service = get_equities_service()
    WeeklyRunner.configure_service(equities_service, top_n=top_n)

    # session.begin() wraps the work in an explicit transaction that commits on
    # clean exit and rolls back on exception — matches the pattern used by
    # app/db/connection.py::get_session for FastAPI requests.
    async with async_session_factory() as session, session.begin():
        branch_id = await _resolve_branch_id(session, branch_name)

        if dry_run:
            logger.info("[dry_run] Would have executed branch=%s top_n=%s", branch_name, top_n)
            return WeeklyRunSummary(
                run_id=f"dry-run-{branch_name}",
                branch_name=branch_name,
                status="skipped",
                universe_count=0, screened_count=0, orders_placed=0,
                trades_executed=0, duration_seconds=0.0,
            )

        # Build per-request services (same as FastAPI Depends())
        event_log = PostgresEventLogRepository(session)
        portfolio_service = PortfolioService(
            portfolio_repo=PostgresPortfolioRepository(session),
            position_repo=PostgresPositionRepository(session),
            snapshot_repo=PostgresSnapshotRepository(session),
            event_log=event_log,
        )
        trade_execution_service = TradeExecutionService(
            order_repo=PostgresOrderRepository(session),
            trade_repo=PostgresTradeRepository(session),
            broker=get_paper_broker(),
            event_log=event_log,
            portfolio_service=portfolio_service,
        )

        # Attach per-request services to the singleton for this run only.
        # Safe because branches run sequentially in _main_async; NOT safe for
        # concurrent callers.
        equities_service.trade_execution_service = trade_execution_service
        equities_service.portfolio_service = portfolio_service
        equities_service.event_log = event_log

        repo = PipelineRunsRepository(session)
        runner = WeeklyRunner(
            service=equities_service, repo=repo, session=session
        )

        summary = await runner.execute(
            branch_name=branch_name,
            branch_id=branch_id,
            run_date=today_ny(),
            force_retry=force_retry,
        )

        # Phase D: score last week's decision now that a week of prices exists.
        # Never allowed to fail the trading run.
        try:
            engine = AttributionEngine(data_service=equities_service.data_service)
            summary.attribution = await engine.compute_and_persist(
                session,
                branch_id=branch_id,
                branch_name=branch_name,
                as_of=today_ny(),
            )
        except Exception:
            logger.warning("Attribution failed for %s — continuing", branch_name, exc_info=True)

        return summary


async def _main_async(args: argparse.Namespace) -> int:
    data_service = _init_data_platform()
    init_services(data_service)

    # Resolve configuration
    top_n = args.top_n if args.top_n is not None else settings.equities_top_n
    branches = args.branches or settings.equities_enabled_branches

    logger.info(
        "Weekly run: branches=%s top_n=%s force_retry=%s dry_run=%s",
        branches, top_n, args.force_retry, args.dry_run,
    )

    summaries: list[WeeklyRunSummary] = []
    any_failed = False
    for branch in branches:
        try:
            s = await _run_one_branch(
                branch_name=branch,
                top_n=top_n,
                force_retry=args.force_retry,
                dry_run=args.dry_run,
            )
            summaries.append(s)
        except (RunInFlightError, ManualInterventionRequired) as exc:
            logger.error("Branch %s aborted: %s", branch, exc)
            summaries.append(WeeklyRunSummary(
                run_id=f"aborted-{branch}",
                branch_name=branch,
                status="failed",
                universe_count=0, screened_count=0, orders_placed=0,
                trades_executed=0, duration_seconds=0.0,
                error=repr(exc),
            ))
            any_failed = True
        except Exception as exc:
            logger.exception("Branch %s failed", branch)
            summaries.append(WeeklyRunSummary(
                run_id=f"failed-{branch}",
                branch_name=branch,
                status="failed",
                universe_count=0, screened_count=0, orders_placed=0,
                trades_executed=0, duration_seconds=0.0,
                error=repr(exc),
            ))
            any_failed = True

    digest = render_digest(summaries, run_date=today_ny())
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        Path(summary_path).write_text(digest, encoding="utf-8")
    else:
        print(digest)

    return 1 if any_failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the weekly equities paper-trading pipeline")
    parser.add_argument("--branches", nargs="*", default=None,
                        help="Branches to run (default: $HEDGE_EQUITIES_ENABLED_BRANCHES)")
    parser.add_argument("--top-n", type=int, default=None,
                        help="Top N holdings per branch (default: $HEDGE_EQUITIES_TOP_N)")
    parser.add_argument("--force-retry", action="store_true",
                        help="If prior run for today failed, create a new attempt row")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate plumbing without invoking the pipeline")
    args = parser.parse_args()

    try:
        exit_code = asyncio.run(_main_async(args))
    except Exception:
        logger.exception("Infrastructure error (DB unreachable / missing secret / etc.)")
        sys.exit(2)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

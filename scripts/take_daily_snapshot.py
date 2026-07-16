"""Daily EOD mark-to-market + snapshot for each enabled branch.

Run by .github/workflows/daily-snapshot.yml on weekday evenings (~after close).
Idempotent: a branch already snapshotted today (NY) is skipped, so re-runs and
Monday overlap with the weekly pipeline are harmless.

Exit codes:
  0 — all branches snapshotted or skipped
  1 — at least one branch failed (others still attempted)
  2 — infrastructure error (DB unreachable, missing secret, etc.)
"""

from __future__ import annotations

import asyncio
import logging
import sys

from dotenv import load_dotenv

load_dotenv()

from app.config import settings  # noqa: E402
from app.db.connection import async_session_factory  # noqa: E402
from app.modules.equities.weekly_runner import ny_date, today_ny  # noqa: E402
from app.modules.event_log.repository import PostgresEventLogRepository  # noqa: E402
from app.modules.portfolio.repository import (  # noqa: E402
    PostgresPortfolioRepository,
    PostgresPositionRepository,
    PostgresSnapshotRepository,
)
from app.modules.portfolio.service import PortfolioService  # noqa: E402
from scripts.common import init_data_platform, resolve_branch_id  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("yfinance").setLevel(logging.WARNING)
logger = logging.getLogger("daily_snapshot")


def _already_snapshotted_today(latest) -> bool:
    return latest is not None and ny_date(latest.snapshot_at) == today_ny()


async def snapshot_branch(branch_name: str, data_service) -> str:
    """Returns 'snapshotted' | 'skipped' | 'no-portfolio'."""
    async with async_session_factory() as session, session.begin():
        branch_id = await resolve_branch_id(session, branch_name)
        snapshot_repo = PostgresSnapshotRepository(session)
        portfolio_service = PortfolioService(
            portfolio_repo=PostgresPortfolioRepository(session),
            position_repo=PostgresPositionRepository(session),
            snapshot_repo=snapshot_repo,
            event_log=PostgresEventLogRepository(session),
        )
        portfolio = await portfolio_service.get_portfolio(branch_id)
        if portfolio is None:
            logger.warning("No portfolio for %s", branch_name)
            return "no-portfolio"

        latest = await snapshot_repo.latest_by_branch(branch_id)
        if _already_snapshotted_today(latest):
            logger.info("%s already snapshotted today — skipping", branch_name)
            return "skipped"

        prices: dict[str, float | None] = {}
        for pos in portfolio.positions:
            if pos.long_quantity > 0:
                prices[pos.symbol] = await data_service.get_current_price(pos.symbol)

        mtm = await portfolio_service.mark_to_market(branch_id, prices)
        await portfolio_service.take_snapshot(branch_id, positions_detail=mtm.positions_detail)
        logger.info(
            "%s: NAV %.2f (unrealized %.2f, %d unpriced)",
            branch_name,
            mtm.nav,
            mtm.unrealized_pnl,
            mtm.unpriced,
        )
        return "snapshotted"


async def _main_async() -> int:
    data_service = init_data_platform()
    outcomes: dict[str, str] = {}
    for branch in settings.equities_enabled_branches:
        try:
            outcomes[branch] = await snapshot_branch(branch, data_service)
        except Exception:
            logger.exception("Branch %s snapshot failed", branch)
            outcomes[branch] = "failed"
    logger.info("done: %s", " ".join(f"{b}={o}" for b, o in outcomes.items()))
    return 1 if "failed" in outcomes.values() else 0


def main() -> None:
    try:
        sys.exit(asyncio.run(_main_async()))
    except Exception:
        logger.exception("Infrastructure error")
        sys.exit(2)


if __name__ == "__main__":
    main()

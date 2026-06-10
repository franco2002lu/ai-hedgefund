"""Backfill attribution_reports for all historical portfolio decisions.

Usage:
    HEDGE_DATABASE_URL=postgresql+asyncpg://... python -m scripts.backfill_attribution
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

from app.db.connection import async_session_factory  # noqa: E402
from app.db.models import BranchModel, PortfolioDecisionModel  # noqa: E402
from app.modules.equities.attribution import AttributionEngine  # noqa: E402
from app.modules.equities.weekly_runner import today_ny  # noqa: E402
from scripts.run_weekly_pipeline import _init_data_platform  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("backfill_attribution")


async def main() -> None:
    engine = AttributionEngine(data_service=_init_data_platform())
    async with async_session_factory() as session, session.begin():
        decisions = (
            (await session.execute(select(PortfolioDecisionModel).order_by(PortfolioDecisionModel.decided_at)))
            .scalars()
            .all()
        )
        branches = {b.id: b.name for b in (await session.execute(select(BranchModel))).scalars().all()}
        for d in decisions:
            # Score each decision one week after it was made (or today if sooner).
            as_of = min(d.decided_at.date() + timedelta(days=7), today_ny())
            report = await engine.compute_and_persist(
                session,
                branch_id=str(d.branch_id),
                branch_name=d.branch_name or branches.get(d.branch_id, "unknown"),
                as_of=as_of,
            )
            if report:
                logger.info(
                    "%s %s: basket %+.2f%% (eq %+.2f%%) vs %s %+.2f%%",
                    report.branch_name,
                    report.decision_date,
                    report.basket_return_conviction * 100,
                    report.basket_return_equal * 100,
                    report.benchmark_symbol,
                    (report.benchmark_return or 0) * 100,
                )


if __name__ == "__main__":
    asyncio.run(main())

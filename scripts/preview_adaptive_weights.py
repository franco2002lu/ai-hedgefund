"""Preview the adaptive analyst weights the next weekly run would use.

Read-only: SELECTs attribution_reports + the latest portfolio_decisions row
per branch, runs the same resolution code as the pipeline, prints the result.

    python -m scripts.preview_adaptive_weights --branches growth value
"""

from __future__ import annotations

import argparse
import asyncio

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

from app.db.connection import async_session_factory  # noqa: E402
from app.db.models import AttributionReportModel  # noqa: E402
from app.modules.equities.adaptive_weights import ANALYSTS, resolve_analyst_weights  # noqa: E402
from app.modules.equities.config import EquitiesConfig  # noqa: E402
from scripts.common import resolve_branch_id  # noqa: E402


def _fmt(x) -> str:
    return "  n/a" if x is None else f"{float(x):+.2f}"


async def _preview(branch_name: str) -> None:
    config = EquitiesConfig()
    async with async_session_factory() as session:
        branch_id = await resolve_branch_id(session, branch_name)
        stmt = (
            select(AttributionReportModel)
            .where(AttributionReportModel.branch_id == branch_id)
            .order_by(AttributionReportModel.decision_date.desc())
            .limit(config.agents.adaptive.lookback_weeks)
        )
        rows = (await session.execute(stmt)).scalars().all()
        print(f"\n=== {branch_name} — {len(rows)} attribution report(s) ===")
        print("decision_date   fund   news   tech   comp")
        for row in rows:
            ics = row.analyst_ics or {}
            print(
                f"{row.decision_date}     {_fmt(ics.get('fundamentals'))}  {_fmt(ics.get('news'))}  "
                f"{_fmt(ics.get('technical'))}  {_fmt(ics.get('composite'))}"
            )
        report = await resolve_analyst_weights(session=session, branch_id=branch_id, agents_config=config.agents)
        print(f"mode={report.mode} reason={report.reason} valid_weeks={report.valid_weeks}")
        print("EWICs: " + "  ".join(f"{a} {_fmt(report.ewics.get(a))}" for a in ANALYSTS))
        print("Next-run weights: " + "  ".join(f"{a} {report.weights[a]:.4f}" for a in ANALYSTS))
        for alert in report.alerts:
            print(f"ALERT: {alert['analyst']} rolling IC <= 0 for {alert['streak']} weeks (EWIC {_fmt(alert['ewic'])})")


async def _main(branches: list[str]) -> None:
    for b in branches:
        await _preview(b)


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview next run's adaptive analyst weights (read-only)")
    parser.add_argument("--branches", nargs="*", default=["growth", "value"])
    args = parser.parse_args()
    asyncio.run(_main(args.branches))


if __name__ == "__main__":
    main()

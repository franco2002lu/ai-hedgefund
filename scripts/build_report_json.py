"""Regenerate scheduled_run_results/report.json wholesale from the database.

Self-healing: every run rebuilds the full file (NAV series, holdings, trades,
attribution, inception metrics per branch), so a missed week or a manual DB fix
never leaves the report stale. Intended to be run by the weekly workflow after
the pipeline, then committed to the repo; also runnable manually.

Series note: snapshots are deduped to the LAST snapshot per NY date. Monday's
point is the weekly pipeline's post-rebalance morning mark; Tue-Fri points are
EOD (daily-snapshot workflow).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

from app.db.connection import async_session_factory  # noqa: E402
from app.db.models import (  # noqa: E402
    AttributionReportModel,
    BranchModel,
    PortfolioModel,
    PortfolioSnapshotModel,
    TradeModel,
)
from scripts.common import resolve_branch_id  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("build_report_json")

_NY_TZ = ZoneInfo("America/New_York")


def dedupe_last_per_day(snapshots: list[dict]) -> list[tuple[str, dict]]:
    """Collapse snapshots to the last one per NY date, ascending by date."""
    by_day: dict[str, dict] = {}
    for s in sorted(snapshots, key=lambda s: s["snapshot_at"]):
        by_day[s["snapshot_at"].astimezone(_NY_TZ).date().isoformat()] = s
    return sorted(by_day.items())


# Standing investor-reporting disclosures. Rebuilds are wholesale, so notes
# must live in code to survive every regeneration.
FUND_NOTES = [
    {
        "period": "2026-06-15/2026-07-20",
        "note": (
            "Execution before 2026-07-16 could fill buys ahead of sells with no "
            "fill-time cash check; branches ran negative cash (unintended leverage — "
            "the value branch peaked near -24% of NAV in the week of 2026-06-22). "
            "Fixed on 2026-07-16 (fill-time cash gate + sells-first ordering). "
            "Returns in this window reflect exposure above 100% of allocated capital."
        ),
    },
]


def build_fund_summary(branches: dict) -> dict:
    """Fund-level rollup across branch payloads, including standing notes."""
    totals_initial = sum(b["initial_capital"] for b in branches.values())
    totals_nav = sum(b["nav"] for b in branches.values())
    return {
        "initial_capital": totals_initial,
        "nav": totals_nav,
        "total_pnl": totals_nav - totals_initial if totals_initial > 0 else None,
        "total_return_pct": ((totals_nav - totals_initial) / totals_initial if totals_initial > 0 else None),
        "notes": list(FUND_NOTES),
    }


async def _branch_payload(session, branch_name: str) -> dict:
    branch_id = await resolve_branch_id(session, branch_name)
    bid = uuid.UUID(branch_id)

    branch = await session.get(BranchModel, bid)
    pf = (await session.execute(select(PortfolioModel).where(PortfolioModel.branch_id == bid))).scalar_one_or_none()

    snaps = [
        {
            "snapshot_at": r.snapshot_at,
            "nav": float(r.nav),
            "cash": float(r.cash),
            "unrealized_pnl": float(r.unrealized_pnl),
            "realized_pnl": float(r.realized_pnl),
            "positions_detail": r.positions_detail,
        }
        for r in (
            await session.execute(
                select(PortfolioSnapshotModel)
                .where(PortfolioSnapshotModel.branch_id == bid)
                .order_by(PortfolioSnapshotModel.snapshot_at)
            )
        )
        .scalars()
        .all()
    ]
    series = dedupe_last_per_day(snaps)

    trades = [
        {
            "date": r.executed_at.astimezone(_NY_TZ).date().isoformat(),
            "symbol": r.symbol,
            "side": r.side,
            "quantity": float(r.quantity),
            "price": float(r.price),
            "notional": float(r.quantity) * float(r.price),
        }
        for r in (
            await session.execute(
                select(TradeModel).where(TradeModel.branch_id == bid).order_by(TradeModel.executed_at)
            )
        )
        .scalars()
        .all()
    ]

    attribution = [
        {
            "decision_date": r.decision_date.isoformat(),
            "as_of_date": r.as_of_date.isoformat(),
            "basket_return_conviction": float(r.basket_return_conviction),
            "basket_return_equal": float(r.basket_return_equal),
            "benchmark_return": float(r.benchmark_return) if r.benchmark_return is not None else None,
            "benchmark_symbol": r.benchmark_symbol,
            "spy_return": float(r.spy_return) if r.spy_return is not None else None,
            "analyst_ics": r.analyst_ics,
        }
        for r in (
            await session.execute(
                select(AttributionReportModel)
                .where(AttributionReportModel.branch_id == bid)
                .order_by(AttributionReportModel.decision_date)
            )
        )
        .scalars()
        .all()
    ]

    initial = float(branch.allocated_capital) if branch else 0.0
    latest_nav = series[-1][1]["nav"] if series else (float(pf.nav) if pf else 0.0)
    latest_detail = next((s["positions_detail"] for _, s in reversed(series) if s.get("positions_detail")), None)
    return {
        "initial_capital": initial,
        "inception_date": pf.created_at.date().isoformat() if pf and pf.created_at else None,
        "nav": latest_nav,
        "cash": float(pf.cash) if pf else None,
        "total_pnl": latest_nav - initial if initial > 0 else None,
        "total_return_pct": (latest_nav - initial) / initial if initial > 0 else None,
        "nav_series": [
            {
                "date": d,
                "nav": s["nav"],
                "cash": s["cash"],
                "unrealized_pnl": s["unrealized_pnl"],
                "realized_pnl": s["realized_pnl"],
            }
            for d, s in series
        ],
        "holdings": latest_detail,
        "trades": trades,
        "attribution": attribution,
    }


async def _main_async(args) -> int:
    payload: dict = {
        "generated_at": datetime.now(UTC).isoformat(),
        "branches": {},
    }
    async with async_session_factory() as session:
        for branch in args.branches:
            payload["branches"][branch] = await _branch_payload(session, branch)

    payload["fund"] = build_fund_summary(payload["branches"])

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, out)
    logger.info("Wrote %s", out)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branches", nargs="+", default=["growth", "value"])
    parser.add_argument("--out", default="scheduled_run_results/report.json")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(_main_async(args)))
    except Exception:
        logger.exception("report.json build failed")
        sys.exit(2)


if __name__ == "__main__":
    main()

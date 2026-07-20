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
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # Make ANTHROPIC_API_KEY and HEDGE_* available

from app.config import settings  # noqa: E402
from app.db.connection import async_session_factory  # noqa: E402
from app.dependencies import get_equities_service, get_paper_broker, init_services  # noqa: E402
from app.modules.equities.attribution import AttributionEngine  # noqa: E402
from app.modules.equities.risk_checks import evaluate_post_run_invariants, persist_alerts, to_digest_dicts  # noqa: E402
from app.modules.equities.weekly_runner import (  # noqa: E402
    ManualInterventionRequired,
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
    PostgresRiskAlertRepository,
    PostgresSnapshotRepository,
)
from app.modules.portfolio.service import PortfolioService  # noqa: E402
from app.modules.trade_execution.repository import (  # noqa: E402
    PostgresOrderRepository,
    PostgresTradeRepository,
)
from app.modules.trade_execution.service import TradeExecutionService  # noqa: E402
from scripts.common import init_data_platform, resolve_branch_id  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("yfinance").setLevel(logging.WARNING)
logger = logging.getLogger("weekly_pipeline")


async def _run_one_branch(
    *,
    branch_name: str,
    top_n: int | None,
    force_retry: bool,
    dry_run: bool,
) -> WeeklyRunSummary:
    equities_service = get_equities_service()
    WeeklyRunner.configure_service(equities_service, top_n=top_n)

    # Resolve branch_id in a short read-only session before building the runner.
    async with async_session_factory() as session:
        branch_id = await resolve_branch_id(session, branch_name)

    # Single date for the whole run — a run straddling midnight ET must not
    # use different dates for the trading run and the attribution pass.
    run_date = today_ny()

    if dry_run:
        logger.info("[dry_run] Would have executed branch=%s top_n=%s", branch_name, top_n)
        return WeeklyRunSummary(
            run_id=f"dry-run-{branch_name}",
            branch_name=branch_name,
            status="skipped",
            universe_count=0,
            screened_count=0,
            orders_placed=0,
            trades_executed=0,
            duration_seconds=0.0,
        )

    # Phase D: score last week's decision BEFORE trading (2026-07-16 spec) so
    # this run's adaptive weights see the freshest IC, and so the IC series
    # keeps accruing even when the trading run later fails. Own session;
    # never allowed to block the trading run. Which decision gets scored is
    # unchanged (the engine selects decided_at < midnight of run_date).
    attribution_report = None
    try:
        engine = AttributionEngine(data_service=equities_service.data_service)
        async with async_session_factory() as session, session.begin():
            attribution_report = await engine.compute_and_persist(
                session,
                branch_id=branch_id,
                branch_name=branch_name,
                as_of=run_date,
            )
    except Exception:
        logger.warning("Attribution failed for %s — continuing", branch_name, exc_info=True)

    # The run_fn closure captures equities_service and branch_name.
    # It receives the trading session and the run_id (decided by WeeklyRunner)
    # so it can pass run_id to run_pipeline for logging.
    async def _run_pipeline_in(session, run_id):
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

        return await equities_service.run_pipeline(
            branch_name=branch_name,
            branch_id=branch_id,
            run_id=run_id,
            session=session,
        )

    runner = WeeklyRunner(session_factory=async_session_factory)

    run_started_at = datetime.now(UTC)
    summary = await runner.execute(
        branch_name=branch_name,
        branch_id=branch_id,
        run_date=run_date,
        force_retry=force_retry,
        run_fn=_run_pipeline_in,
    )
    summary.attribution = attribution_report

    # Mark to market, snapshot, and build the portfolio report for the digest.
    # Runs in its own dedicated session after the trading data is durable.
    # render_digest only renders portfolio_report for status == "completed";
    # this still runs on "skipped" (idempotent rerun) because the point there
    # is refreshing the mark and taking the once-per-day snapshot, not
    # populating today's digest.
    if summary.status in ("completed", "skipped"):
        await _mark_snapshot_and_report(
            branch_id=branch_id,
            branch_name=branch_name,
            data_service=equities_service.data_service,
            portfolio_config=equities_service.config.portfolio,
            summary=summary,
            run_started_at=run_started_at,
        )

    return summary


async def _mark_snapshot_and_report(
    *,
    branch_id: str,
    branch_name: str,
    data_service,
    portfolio_config,
    summary: WeeklyRunSummary,
    run_started_at: datetime,
) -> None:
    """Mark to market, snapshot (once per NY day), and attach a PortfolioReport.

    Runs in its own session after trading data is durable. Never raises.
    """
    from datetime import time as dtime
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from app.modules.equities.weekly_runner import PortfolioReport, ny_date

    try:
        async with async_session_factory() as session, session.begin():
            event_log = PostgresEventLogRepository(session)
            snapshot_repo = PostgresSnapshotRepository(session)
            portfolio_service = PortfolioService(
                portfolio_repo=PostgresPortfolioRepository(session),
                position_repo=PostgresPositionRepository(session),
                snapshot_repo=snapshot_repo,
                event_log=event_log,
            )
            portfolio = await portfolio_service.get_portfolio(branch_id)
            if portfolio is None:
                logger.warning("No portfolio for %s — skipping mark/snapshot", branch_name)
                return

            prices: dict[str, float | None] = {}
            for pos in portfolio.positions:
                if pos.long_quantity > 0:
                    prices[pos.symbol] = await data_service.get_current_price(pos.symbol)

            mtm = await portfolio_service.mark_to_market(branch_id, prices)

            today = today_ny()
            latest = await snapshot_repo.latest_by_branch(branch_id)
            if latest is None or ny_date(latest.snapshot_at) != today:
                await portfolio_service.take_snapshot(branch_id, positions_detail=mtm.positions_detail)

            # WoW baseline = latest snapshot strictly before (today − 6d)
            # NY-midnight, i.e. the prior week's mark. Anchoring at plain
            # "before today" would make Monday's baseline Friday's EOD
            # snapshot (~18h window, ≈0%) once the daily job fills Tue–Fri;
            # −6d resolves to the prior Monday's mark, falling back to an
            # older EOD if that's missing (no snapshot at all → None → n/a).
            ny_midnight = datetime.combine(today, dtime.min, tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)
            wow_anchor = ny_midnight - timedelta(days=6)
            prev = await snapshot_repo.latest_by_branch(branch_id, before=wow_anchor)
            wow = (mtm.nav - prev.nav) / prev.nav if prev and prev.nav > 0 else None

            initial = float(portfolio.allocated_capital)
            trades, _total = await PostgresTradeRepository(session).list_trades(
                branch_id=branch_id, since=run_started_at, limit=200
            )
            report = PortfolioReport(
                nav=mtm.nav,
                cash=mtm.cash,
                cash_pct=mtm.cash / mtm.nav if mtm.nav > 0 else 0.0,
                unrealized_pnl=mtm.unrealized_pnl,
                realized_pnl=mtm.realized_pnl,
                initial_capital=initial,
                inception_return_pct=(mtm.nav - initial) / initial if initial > 0 else None,
                wow_return_pct=wow,
                top_holdings=[{"symbol": d["symbol"], "weight": d["weight"]} for d in mtm.positions_detail[:5]],
                trades=[
                    {
                        "symbol": t.symbol,
                        "side": str(t.side),
                        "quantity": float(t.quantity),
                        "price": float(t.price),
                        "notional": float(t.quantity) * float(t.price),
                    }
                    for t in trades
                ],
                unpriced=mtm.unpriced,
            )

            # Theme A1: post-run invariant checks — completed runs only
            # (a skipped idempotent rerun must not duplicate alert rows).
            if summary.status == "completed":
                weights = {d["symbol"]: d["weight"] for d in mtm.positions_detail}
                alerts = evaluate_post_run_invariants(
                    cash=mtm.cash,
                    nav=mtm.nav,
                    position_weights=weights,
                    portfolio_config=portfolio_config,
                    branch_id=branch_id,
                    branch_name=branch_name,
                )
                report.risk_alerts = to_digest_dicts(alerts)
                if alerts:
                    try:
                        # Savepoint: a persistence failure must not roll back
                        # the snapshot/mark in the enclosing transaction.
                        async with session.begin_nested():
                            await persist_alerts(
                                alerts,
                                repo=PostgresRiskAlertRepository(session),
                                event_log=event_log,
                            )
                    except Exception:
                        logger.warning(
                            "Risk alert persistence failed for %s — digest still shows them",
                            branch_name,
                            exc_info=True,
                        )
                        report.risk_alerts.append(
                            {"level": "warning", "message": "Risk alert persistence failed — see run logs"}
                        )
        # Assign only after the `async with` block above has exited normally,
        # i.e. the transaction committed. If commit itself raises, control
        # skips straight to `except` below and portfolio_report is left
        # unset — a failed commit can never leave unpersisted numbers in the
        # digest.
        summary.portfolio_report = report
    except Exception:
        logger.warning("Mark/snapshot/report failed for %s — continuing", branch_name, exc_info=True)


async def _main_async(args: argparse.Namespace) -> int:
    data_service = init_data_platform()
    init_services(data_service)

    # Resolve configuration
    top_n = args.top_n if args.top_n is not None else settings.equities_top_n
    branches = args.branches or settings.equities_enabled_branches

    logger.info(
        "Weekly run: branches=%s top_n=%s force_retry=%s dry_run=%s",
        branches,
        top_n,
        args.force_retry,
        args.dry_run,
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
            summaries.append(
                WeeklyRunSummary(
                    run_id=f"aborted-{branch}",
                    branch_name=branch,
                    status="failed",
                    universe_count=0,
                    screened_count=0,
                    orders_placed=0,
                    trades_executed=0,
                    duration_seconds=0.0,
                    error=repr(exc),
                )
            )
            any_failed = True
        except Exception as exc:
            logger.exception("Branch %s failed", branch)
            summaries.append(
                WeeklyRunSummary(
                    run_id=f"failed-{branch}",
                    branch_name=branch,
                    status="failed",
                    universe_count=0,
                    screened_count=0,
                    orders_placed=0,
                    trades_executed=0,
                    duration_seconds=0.0,
                    error=repr(exc),
                )
            )
            any_failed = True

    digest = render_digest(summaries, run_date=today_ny())
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        Path(summary_path).write_text(digest, encoding="utf-8")
    else:
        print(digest)

    # Skip the file write on dry runs — a dry-run dispatch must not overwrite
    # a real same-day digest in scheduled_run_results/.
    if args.report_dir and not args.dry_run:
        report_dir = Path(args.report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / f"{today_ny().isoformat()}.md").write_text(digest, encoding="utf-8")

    return 1 if any_failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the weekly equities paper-trading pipeline")
    parser.add_argument(
        "--branches", nargs="*", default=None, help="Branches to run (default: $HEDGE_EQUITIES_ENABLED_BRANCHES)"
    )
    parser.add_argument(
        "--top-n", type=int, default=None, help="Top N holdings per branch (default: $HEDGE_EQUITIES_TOP_N)"
    )
    parser.add_argument(
        "--force-retry", action="store_true", help="If prior run for today failed, create a new attempt row"
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate plumbing without invoking the pipeline")
    parser.add_argument(
        "--report-dir",
        default=None,
        help="Also write the digest to <dir>/<run_date>.md (workflow passes scheduled_run_results)",
    )
    args = parser.parse_args()

    try:
        exit_code = asyncio.run(_main_async(args))
    except Exception:
        logger.exception("Infrastructure error (DB unreachable / missing secret / etc.)")
        sys.exit(2)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

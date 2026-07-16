"""Backfill daily portfolio_snapshots from the trades ledger + Yahoo closes.

Reconstructs each branch's cash/positions day by day using the same average-cost
math as PortfolioService.handle_trade_executed, values positions at daily closes
(carrying the last known close forward across gaps), and inserts one snapshot per
NY trading day at 16:00 ET. Days that already have a snapshot are skipped.

Safety: --dry-run (default) writes nothing. --apply refuses to write unless the
reconstructed final cash and per-symbol quantities match the live portfolios/
positions rows (within $0.50 / 1e-6 shares) and every traded symbol returned at
least one price bar.

Usage:
    python -m scripts.backfill_snapshots --branches growth value            # dry run
    python -m scripts.backfill_snapshots --branches growth value --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

from app.common.enums import OrderSide  # noqa: E402
from app.common.models.trade import Trade  # noqa: E402
from app.db.connection import async_session_factory  # noqa: E402
from app.db.models import PortfolioModel, PortfolioSnapshotModel, PositionModel, TradeModel  # noqa: E402
from scripts.common import init_data_platform, resolve_branch_id  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("yfinance").setLevel(logging.WARNING)
logger = logging.getLogger("backfill_snapshots")

_NY_TZ = ZoneInfo("America/New_York")


@dataclass
class DailyState:
    day: date
    cash: float
    nav: float
    unrealized_pnl: float
    realized_pnl: float
    # symbol -> {quantity, cost_basis, close (float|None), market_value}
    positions: dict[str, dict] = field(default_factory=dict)


def reconstruct_daily_states(
    trades: list[Trade],
    *,
    initial_cash: float,
    closes: dict[str, dict[date, float]],
    trading_days: list[date],
) -> list[DailyState]:
    """Replay trades over trading_days; value positions at (carried-forward) closes."""
    for t in trades:
        if t.side not in (OrderSide.BUY, OrderSide.SELL):
            raise ValueError(f"Backfill supports long-only trades, got {t.side} for {t.symbol}")

    by_day: dict[date, list[Trade]] = {}
    for t in sorted(trades, key=lambda t: t.executed_at):
        by_day.setdefault(t.executed_at.astimezone(_NY_TZ).date(), []).append(t)

    cash = initial_cash
    realized_total = 0.0
    qty: dict[str, float] = {}
    cost: dict[str, float] = {}
    last_close: dict[str, float] = {}
    states: list[DailyState] = []

    for day in sorted(trading_days):
        for t in by_day.get(day, []):
            if t.side == OrderSide.BUY:
                trade_cost = t.price * t.quantity + t.commission
                qty[t.symbol] = qty.get(t.symbol, 0.0) + t.quantity
                cost[t.symbol] = cost.get(t.symbol, 0.0) + trade_cost
                cash -= trade_cost
            else:  # SELL — same avg-cost math as handle_trade_executed
                held = qty.get(t.symbol, 0.0)
                if held < t.quantity - 1e-9:
                    raise ValueError(f"Ledger inconsistency: sell {t.quantity} {t.symbol}, hold {held}")
                avg = cost.get(t.symbol, 0.0) / held if held > 0 else 0.0
                realized_total += (t.price - avg) * t.quantity - t.commission
                cost[t.symbol] = cost.get(t.symbol, 0.0) - avg * t.quantity
                qty[t.symbol] = held - t.quantity
                cash += t.price * t.quantity - t.commission

        total_mv = 0.0
        total_cost = 0.0
        positions: dict[str, dict] = {}
        for sym, q in qty.items():
            if q <= 1e-9:
                continue
            day_close = closes.get(sym, {}).get(day)
            if day_close is not None:
                last_close[sym] = day_close
            close = last_close.get(sym)
            mv = close * q if close is not None else cost.get(sym, 0.0)
            total_mv += mv
            total_cost += cost.get(sym, 0.0)
            positions[sym] = {
                "quantity": q,
                "cost_basis": cost.get(sym, 0.0),
                "close": close,
                "market_value": mv,
            }

        states.append(
            DailyState(
                day=day,
                cash=cash,
                nav=cash + total_mv,
                unrealized_pnl=total_mv - total_cost,
                realized_pnl=realized_total,
                positions=positions,
            )
        )
    return states


def validate_final_state(final: DailyState, *, live_cash: float, live_positions: dict[str, float]) -> list[str]:
    """Compare reconstruction vs live DB. Empty list == valid."""
    problems = []
    if abs(final.cash - live_cash) > 0.50:
        problems.append(f"cash mismatch: reconstructed {final.cash:.4f} vs live {live_cash:.4f}")
    recon_qty = {s: p["quantity"] for s, p in final.positions.items()}
    for sym in sorted(set(recon_qty) | set(live_positions)):
        recon, live = recon_qty.get(sym, 0.0), live_positions.get(sym, 0.0)
        if abs(recon - live) > 1e-6:
            problems.append(f"{sym} quantity mismatch: reconstructed {recon} vs live {live}")
    return problems


def snapshot_positions_detail(state: DailyState) -> list[dict]:
    detail = [
        {
            "symbol": sym,
            "quantity": p["quantity"],
            "price": p["close"],
            "market_value": p["market_value"],
            "cost_basis": p["cost_basis"],
            "unrealized_pnl": p["market_value"] - p["cost_basis"],
            "weight": p["market_value"] / state.nav if state.nav > 0 else 0.0,
        }
        for sym, p in state.positions.items()
    ]
    detail.sort(key=lambda d: (-d["market_value"], d["symbol"]))
    return detail


async def _load_trades(session, branch_id: str) -> list[Trade]:
    stmt = select(TradeModel).where(TradeModel.branch_id == uuid.UUID(branch_id)).order_by(TradeModel.executed_at)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        Trade(
            id=str(r.id),
            order_id=str(r.order_id),
            branch_id=str(r.branch_id),
            instrument_id=str(r.instrument_id),
            symbol=r.symbol,
            side=OrderSide(r.side),
            quantity=float(r.quantity),
            price=float(r.price),
            commission=float(r.commission),
            slippage=float(r.slippage),
            execution_mode=r.execution_mode,
            executed_at=r.executed_at,
        )
        for r in rows
    ]


async def _fetch_closes(data_service, symbols, start: date, end: date):
    closes: dict[str, dict[date, float]] = {}
    trading_days: set[date] = set()
    for sym in sorted(symbols):
        result = await data_service.get_prices(sym, start, end + timedelta(days=1))
        closes[sym] = {}
        for bar in result.get("bars", []):
            # PriceBar.timestamp is a plain date (see app/common/interfaces/price_data.py)
            d = bar["timestamp"]
            if not isinstance(d, date):
                d = datetime.fromisoformat(str(d)).date()
            closes[sym][d] = float(bar["close"])
            trading_days.add(d)
    return closes, trading_days


async def backfill_branch(branch_name: str, data_service, *, apply: bool) -> None:
    async with async_session_factory() as session, session.begin():
        branch_id = await resolve_branch_id(session, branch_name)
        trades = await _load_trades(session, branch_id)
        if not trades:
            logger.info("%s: no trades — nothing to backfill", branch_name)
            return

        pf = (
            await session.execute(select(PortfolioModel).where(PortfolioModel.branch_id == uuid.UUID(branch_id)))
        ).scalar_one()
        live_positions = {
            r.symbol: float(r.long_quantity)
            for r in (await session.execute(select(PositionModel).where(PositionModel.portfolio_id == pf.id)))
            .scalars()
            .all()
            if float(r.long_quantity) > 0
        }
        # Initial cash: each branch portfolio was seeded with $1M before its first
        # trade (2026-06-10). The validation gate below catches this if wrong.
        initial_cash = 1_000_000.0

        first_day = trades[0].executed_at.astimezone(_NY_TZ).date()
        yesterday = datetime.now(_NY_TZ).date() - timedelta(days=1)
        symbols = {t.symbol for t in trades}
        closes, trading_days = await _fetch_closes(data_service, symbols, first_day, yesterday)
        empty_symbols = sorted(s for s in symbols if not closes.get(s))
        if empty_symbols:
            logger.warning(
                "%s: no price bars for %d symbol(s): %s — their NAV contribution would be "
                "cost-basis-flat (Yahoo rate-limited?)",
                branch_name,
                len(empty_symbols),
                ", ".join(empty_symbols),
            )
        days = sorted(d for d in trading_days if first_day <= d <= yesterday)
        if not days:
            logger.warning(
                "%s: no trading days with price data in window %s → %s — skipping (Yahoo rate-limited?)",
                branch_name,
                first_day,
                yesterday,
            )
            return

        states = reconstruct_daily_states(trades, initial_cash=initial_cash, closes=closes, trading_days=days)

        problems = validate_final_state(states[-1], live_cash=float(pf.cash), live_positions=live_positions)
        print(f"\n=== {branch_name}: {len(states)} trading days ({states[0].day} → {states[-1].day}) ===")
        print(f"{'day':<12}{'cash':>14}{'nav':>14}{'unreal':>12}{'real':>10}{'pos':>5}")
        for s in states:
            print(
                f"{s.day.isoformat():<12}{s.cash:>14,.2f}{s.nav:>14,.2f}"
                f"{s.unrealized_pnl:>12,.2f}{s.realized_pnl:>10,.2f}{len(s.positions):>5}"
            )
        if empty_symbols:
            print(f"\nWARNING: no price bars for {len(empty_symbols)} symbol(s): {', '.join(empty_symbols)}")
        if problems:
            print("\nVALIDATION FAILED:")
            for p in problems:
                print(f"  ✗ {p}")
        else:
            print("\nValidation: reconstructed cash and quantities match live DB ✓")

        if not apply:
            logger.info("%s: dry run — nothing written", branch_name)
            return
        if problems:
            raise SystemExit(f"{branch_name}: refusing to --apply with validation failures")
        if empty_symbols:
            raise SystemExit(f"{branch_name}: refusing to --apply with {len(empty_symbols)} unpriced symbol(s)")

        existing_days = {
            row.astimezone(_NY_TZ).date()
            for row in (
                await session.execute(
                    select(PortfolioSnapshotModel.snapshot_at).where(
                        PortfolioSnapshotModel.branch_id == uuid.UUID(branch_id)
                    )
                )
            )
            .scalars()
            .all()
        }
        written = 0
        for s in states:
            if s.day in existing_days:
                continue
            total_mv = sum(p["market_value"] for p in s.positions.values())
            session.add(
                PortfolioSnapshotModel(
                    portfolio_id=pf.id,
                    branch_id=uuid.UUID(branch_id),
                    cash=s.cash,
                    nav=s.nav,
                    total_long_exposure=total_mv,
                    total_short_exposure=0.0,
                    gross_exposure=total_mv,
                    net_exposure=total_mv,
                    unrealized_pnl=s.unrealized_pnl,
                    realized_pnl=s.realized_pnl,
                    margin_used=0.0,
                    position_count=len(s.positions),
                    positions_detail=snapshot_positions_detail(s),
                    snapshot_at=datetime.combine(s.day, dtime(16, 0), tzinfo=_NY_TZ).astimezone(UTC),
                )
            )
            written += 1
        logger.info("%s: wrote %d snapshots (%d already existed)", branch_name, written, len(states) - written)


async def _main_async(args) -> int:
    data_service = init_data_platform()
    for branch in args.branches:
        await backfill_branch(branch, data_service, apply=args.apply)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branches", nargs="+", default=["growth", "value"])
    parser.add_argument("--apply", action="store_true", help="Write snapshots (default: dry run)")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(_main_async(args)))
    except SystemExit:
        raise
    except Exception:
        logger.exception("Backfill failed")
        sys.exit(2)


if __name__ == "__main__":
    main()

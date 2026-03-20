"""Run backtests using small test universes (~20 stocks each).

Usage:
    python run_backtest.py                  # both branches
    python run_backtest.py growth           # growth only
    python run_backtest.py value            # value only
"""

import asyncio
import logging
import sys
from datetime import date

from app.modules.backtest.config import BacktestConfig, RebalanceFrequency
from app.modules.backtest.engine import BacktestEngine

logging.basicConfig(level=logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


async def run_one(branch: str):
    # Prefix with "test_" to load from test_{branch}_universe.csv
    test_branch = f"test_{branch}"

    print(f"\n{'='*60}")
    print(f"  BACKTEST: {branch.upper()} BRANCH (20-stock test universe)")
    print(f"  Period: 2024-06-01 to 2024-06-28 | Weekly Rebalance")
    print(f"  Capital: $100,000 | Quantitative Analysts")
    print(f"{'='*60}")

    config = BacktestConfig(
        start_date=date(2024, 6, 1),
        end_date=date(2024, 6, 28),
        initial_capital=100_000.0,
        rebalance_frequency=RebalanceFrequency.WEEKLY,
        branch_name=test_branch,
        use_llm_agents=False,
        benchmark_symbol="SPY",
    )

    engine = BacktestEngine()
    print("\nLoading data & running simulation...")
    result = await engine.run(config)

    print(f"\nStatus: {result.status}")
    if result.error_message:
        print(f"Error: {result.error_message}")
        return result

    print(f"Duration: {result.duration_seconds:.1f}s")
    print(f"Trading Days: {len(result.snapshots)}")
    print(f"Rebalances: {result.rebalance_count}")
    print(f"Trades Executed: {len(result.trades)}")

    if result.metrics:
        m = result.metrics
        print(f"\n--- Portfolio Performance ---")
        print(f"  Total Return:        {m.total_return:+.2%}")
        print(f"  Annualized Return:   {m.annualized_return:+.2%}")
        print(f"  Volatility (ann):    {m.volatility:.2%}")
        print(f"  Sharpe Ratio:        {m.sharpe_ratio:.2f}")
        print(f"  Sortino Ratio:       {m.sortino_ratio:.2f}")
        print(f"  Calmar Ratio:        {m.calmar_ratio:.2f}")
        print(f"  Max Drawdown:        {m.max_drawdown:.2%}")
        print(f"  Max DD Duration:     {m.max_drawdown_duration_days} days")
        print(f"\n--- Trade Analysis ---")
        print(f"  Total Trades:        {m.total_trades}")
        print(f"  Win Rate:            {m.win_rate:.1%}")
        print(f"  Profit Factor:       {m.profit_factor:.2f}")
        print(f"  Avg Win:             ${m.avg_win:,.2f}")
        print(f"  Avg Loss:            ${m.avg_loss:,.2f}")
        print(f"\n--- Positioning ---")
        print(f"  Avg Position Count:  {m.avg_position_count:.1f}")
        print(f"  Max Position Count:  {m.max_position_count}")
        print(f"  Avg Long Exposure:   {m.avg_long_exposure:.1%}")

    if result.benchmark:
        b = result.benchmark
        print(f"\n--- Benchmark (SPY) ---")
        print(f"  Strategy Return:     {m.total_return:+.2%}")
        print(f"  Benchmark Return:    {b.benchmark_total_return:+.2%}")
        print(f"  Alpha:               {b.alpha:+.2%}")
        print(f"  Beta:                {b.beta:.2f}")
        print(f"  Info Ratio:          {b.information_ratio:.2f}")
        print(f"  Tracking Error:      {b.tracking_error:.2%}")

    if result.snapshots:
        print(f"\n--- NAV Timeline ---")
        for snap in result.snapshots[::4]:
            print(f"  {snap.date}: NAV=${snap.nav:,.0f}  positions={snap.position_count}  return={snap.cumulative_return:+.2%}")
        last = result.snapshots[-1]
        if last.date != result.snapshots[::4][-1].date:
            print(f"  {last.date}: NAV=${last.nav:,.0f}  positions={last.position_count}  return={last.cumulative_return:+.2%}")

    if result.trades:
        print(f"\n--- Trades (first 20) ---")
        for t in result.trades[:20]:
            print(f"  {t.trade_date} {t.side:4s} {t.quantity:>6.0f} {t.symbol:<6s} @ ${t.price:>8.2f}")

    return result


async def main():
    branches = sys.argv[1:] if len(sys.argv) > 1 else ["growth", "value"]
    results = {}
    for branch in branches:
        results[branch] = await run_one(branch)

    if len(results) == 2 and all(r.metrics for r in results.values()):
        g, v = results["growth"].metrics, results["value"].metrics
        print(f"\n{'='*60}")
        print(f"  COMPARISON: GROWTH vs VALUE")
        print(f"{'='*60}")
        print(f"  {'Metric':<22s} {'Growth':>10s} {'Value':>10s}")
        print(f"  {'-'*44}")
        print(f"  {'Total Return':<22s} {g.total_return:>+9.2%} {v.total_return:>+9.2%}")
        print(f"  {'Volatility':<22s} {g.volatility:>9.2%} {v.volatility:>9.2%}")
        print(f"  {'Sharpe Ratio':<22s} {g.sharpe_ratio:>10.2f} {v.sharpe_ratio:>10.2f}")
        print(f"  {'Max Drawdown':<22s} {g.max_drawdown:>9.2%} {v.max_drawdown:>9.2%}")
        print(f"  {'Win Rate':<22s} {g.win_rate:>9.1%} {v.win_rate:>9.1%}")
        print(f"  {'Trades':<22s} {g.total_trades:>10d} {v.total_trades:>10d}")
        print(f"  {'Avg Positions':<22s} {g.avg_position_count:>10.1f} {v.avg_position_count:>10.1f}")
        if results["growth"].benchmark and results["value"].benchmark:
            gb, vb = results["growth"].benchmark, results["value"].benchmark
            print(f"  {'Alpha':<22s} {gb.alpha:>+9.2%} {vb.alpha:>+9.2%}")
            print(f"  {'Beta':<22s} {gb.beta:>10.2f} {vb.beta:>10.2f}")


if __name__ == "__main__":
    asyncio.run(main())

"""Run backtests using N-PORT ETF holdings (VOOG for growth, VOOV for value).

Usage:
    python scripts/run_backtest.py 2025-01-01 2025-12-31              # both branches, full universe
    python scripts/run_backtest.py 2025-01-01 2025-06-30 growth       # growth only
    python scripts/run_backtest.py 2025-01-01 2025-06-30 --top-n 30   # top 30 holdings for both
    python scripts/run_backtest.py 2025-01-01 2025-06-30 --growth-top-n 30 --value-top-n 50
    python scripts/run_backtest.py 2025-01-01 2025-06-30 --capital 10000
"""

import argparse
import asyncio
import logging
from datetime import date

from app.modules.backtest.config import BacktestConfig, RebalanceFrequency
from app.modules.backtest.engine import BacktestEngine

logging.basicConfig(level=logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


async def run_one(branch: str, start: date, end: date, capital: float, top_n: int | None = None):
    universe_desc = f"top-{top_n}" if top_n is not None else "full"

    print(f"\n{'='*60}")
    print(f"  BACKTEST: {branch.upper()} BRANCH ({universe_desc} universe)")
    print(f"  Period: {start} to {end} | Weekly Rebalance")
    print(f"  Capital: ${capital:,.0f} | Quantitative Analysts")
    print(f"{'='*60}")

    config = BacktestConfig(
        start_date=start,
        end_date=end,
        initial_capital=capital,
        rebalance_frequency=RebalanceFrequency.WEEKLY,
        branch_name=branch,
        use_llm_agents=False,
        benchmark_symbols=["SPY", "VOOG" if "growth" in branch else "VOOV"],
        top_n=top_n,
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
        print(f"  VaR (95%):           {m.value_at_risk_95:+.2%}")
        print(f"  CVaR (95%):          {m.conditional_var_95:+.2%}")
        print(f"  Ulcer Index:         {m.ulcer_index:.4f}")
        print(f"\n--- Trade Analysis ---")
        print(f"  Total Trades:        {m.total_trades}")
        print(f"  Win Rate:            {m.win_rate:.1%}")
        print(f"  Profit Factor:       {m.profit_factor:.2f}")
        print(f"  Avg Win:             ${m.avg_win:,.2f}")
        print(f"  Avg Loss:            ${m.avg_loss:,.2f}")
        print(f"  Turnover Rate:       {m.turnover_rate:.2f}x")
        print(f"\n--- Positioning ---")
        print(f"  Avg Position Count:  {m.avg_position_count:.1f}")
        print(f"  Max Position Count:  {m.max_position_count}")
        print(f"  Avg Long Exposure:   {m.avg_long_exposure:.1%}")

    for b in result.benchmarks:
        print(f"\n--- Benchmark ({b.benchmark_symbol}) ---")
        print(f"  Strategy Return:     {m.total_return:+.2%}")
        print(f"  Benchmark Return:    {b.benchmark_total_return:+.2%}")
        print(f"  Alpha:               {b.alpha:+.2%}")
        print(f"  Beta:                {b.beta:.2f}")
        print(f"  Info Ratio:          {b.information_ratio:.2f}")
        print(f"  Tracking Error:      {b.tracking_error:.2%}")
        print(f"  Up Capture:          {b.up_capture_ratio:.1f}%")
        print(f"  Down Capture:        {b.down_capture_ratio:.1f}%")

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
    parser = argparse.ArgumentParser(description="Run backtests with ETF-based universes")
    parser.add_argument("start_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("end_date", help="End date (YYYY-MM-DD)")
    parser.add_argument("branches", nargs="*", default=["growth", "value"],
                        help="Branches to run (default: both)")
    parser.add_argument("--capital", type=float, default=1_000.0,
                        help="Initial capital (default: 1000)")
    parser.add_argument("--top-n", type=int, default=None,
                        help="Top N holdings by weight for all branches (default: all)")
    parser.add_argument("--growth-top-n", type=int, default=None,
                        help="Top N holdings for growth branch (overrides --top-n)")
    parser.add_argument("--value-top-n", type=int, default=None,
                        help="Top N holdings for value branch (overrides --top-n)")
    args = parser.parse_args()

    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)

    per_branch_top_n = {
        "growth": args.growth_top_n,
        "value": args.value_top_n,
    }

    results = {}
    for branch in args.branches:
        top_n = per_branch_top_n.get(branch) if per_branch_top_n.get(branch) is not None else args.top_n
        results[branch] = await run_one(branch, start, end, args.capital, top_n=top_n)

    if "growth" in results and "value" in results and results["growth"].metrics and results["value"].metrics:
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
        if results["growth"].benchmarks and results["value"].benchmarks:
            gb, vb = results["growth"].benchmarks[0], results["value"].benchmarks[0]
            print(f"  {'Alpha (SPY)':<22s} {gb.alpha:>+9.2%} {vb.alpha:>+9.2%}")
            print(f"  {'Beta (SPY)':<22s} {gb.beta:>10.2f} {vb.beta:>10.2f}")


if __name__ == "__main__":
    asyncio.run(main())

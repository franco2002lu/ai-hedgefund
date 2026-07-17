"""Run backtests using N-PORT ETF holdings (VOOG for growth, VOOV for value).

Usage:
    python scripts/run_backtest.py 2025-01-01 2025-12-31              # both branches, full universe
    python scripts/run_backtest.py 2025-01-01 2025-06-30 growth       # growth only
    python scripts/run_backtest.py 2025-01-01 2025-06-30 --top-n 30   # top 30 holdings for both
    python scripts/run_backtest.py 2025-01-01 2025-06-30 --growth-top-n 30 --value-top-n 50
    python scripts/run_backtest.py 2025-01-01 2025-06-30 --capital 10000
    python scripts/run_backtest.py 2025-01-01 2025-06-30 growth --top-n 10 --llm --save
"""

import argparse
import asyncio
import logging
from datetime import date

from dotenv import load_dotenv

load_dotenv()  # Load .env so ANTHROPIC_API_KEY is available for LLM-mode backtests

from app.modules.backtest.config import BacktestConfig, LLMBacktestConfig, RebalanceFrequency  # noqa: E402
from app.modules.backtest.engine import BacktestEngine  # noqa: E402

logging.basicConfig(level=logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


async def run_one(
    branch: str,
    start: date,
    end: date,
    capital: float,
    top_n: int | None = None,
    use_llm: bool = False,
    temperature: float | None = None,
    max_llm_calls_per_rebalance: int | None = None,
    use_llm_cache: bool = True,
    skills_bundle: str | None = None,
    save: bool = False,
):
    analyst_mode = "LLM" if use_llm else "Quantitative"
    universe_desc = f"top-{top_n}" if top_n is not None else "full"

    print(f"\n{'=' * 60}")
    print(f"  BACKTEST: {branch.upper()} BRANCH ({universe_desc} universe)")
    print(f"  Period: {start} to {end} | Weekly Rebalance")
    print(f"  Capital: ${capital:,.0f} | {analyst_mode} Analysts")
    if skills_bundle:
        print(f"  Skills bundle: {skills_bundle}")
    print(f"{'=' * 60}")

    # Build the LLMBacktestConfig with any cap override
    llm_cfg = LLMBacktestConfig(
        cache_signals=True,
        max_llm_calls_per_rebalance=max_llm_calls_per_rebalance if max_llm_calls_per_rebalance is not None else 60,
    )

    config = BacktestConfig(
        start_date=start,
        end_date=end,
        initial_capital=capital,
        rebalance_frequency=RebalanceFrequency.WEEKLY,
        branch_name=branch,
        use_llm_agents=use_llm,
        llm_config=llm_cfg,
        benchmark_symbols=["SPY", "VOOG" if "growth" in branch else "VOOV"],
        top_n=top_n,
        skills_bundle=skills_bundle,
        use_llm_response_cache=use_llm_cache,
    )

    # If --temperature is set and LLM mode is on, override the per-analyst temperatures
    if use_llm and temperature is not None:
        from copy import deepcopy

        from app.modules.equities.config import EquitiesConfig

        override = deepcopy(config.equities_config_override) if config.equities_config_override else None
        if override is None:
            override = EquitiesConfig()
        override.agents.news_analyst.temperature = temperature
        override.agents.fundamentals_analyst.temperature = temperature
        override.agents.technical_analyst.temperature = temperature
        config = config.model_copy(update={"equities_config_override": override})

    # Capture the skill-bundle fingerprint and git SHA BEFORE the run starts
    # so a mid-run edit to the skill files cannot mis-tag the saved record
    # with the post-edit hash. Both are stashed for the save block below.
    save_bundle_hash: str | None = None
    save_git_sha: str | None = None
    if save:
        import subprocess
        from pathlib import Path

        from app.modules.backtest.result_store import hash_skill_bundle

        if skills_bundle:
            fingerprint_dir = Path("data/skill_bundles") / skills_bundle
        else:
            fingerprint_dir = Path("app/modules/equities/agents/skills")
        save_bundle_hash = hash_skill_bundle(fingerprint_dir)
        try:
            save_git_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except Exception:
            save_git_sha = "unknown"

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
        print("\n--- Portfolio Performance ---")
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
        print("\n--- Trade Analysis ---")
        print(f"  Total Trades:        {m.total_trades}")
        print(f"  Win Rate:            {m.win_rate:.1%}")
        print(f"  Profit Factor:       {m.profit_factor:.2f}")
        print(f"  Avg Win:             ${m.avg_win:,.2f}")
        print(f"  Avg Loss:            ${m.avg_loss:,.2f}")
        print(f"  Turnover Rate:       {m.turnover_rate:.2f}x")
        print("\n--- Positioning ---")
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
        print("\n--- NAV Timeline ---")
        for snap in result.snapshots[::4]:
            print(
                f"  {snap.date}: NAV=${snap.nav:,.0f}"
                f"  positions={snap.position_count}"
                f"  return={snap.cumulative_return:+.2%}"
            )
        last = result.snapshots[-1]
        if last.date != result.snapshots[::4][-1].date:
            print(
                f"  {last.date}: NAV=${last.nav:,.0f}"
                f"  positions={last.position_count}"
                f"  return={last.cumulative_return:+.2%}"
            )

    if result.trades:
        print("\n--- Trades (first 20) ---")
        for t in result.trades[:20]:
            print(f"  {t.trade_date} {t.side:4s} {t.quantity:>6.0f} {t.symbol:<6s} @ ${t.price:>8.2f}")

    if save:
        from datetime import datetime

        from app.modules.backtest.result_store import BacktestRun, save_run

        # save_bundle_hash and save_git_sha were captured BEFORE engine.run() above
        # to avoid a race window where mid-run skill edits would mis-tag the run.
        assert save_bundle_hash is not None  # set above when save=True
        assert save_git_sha is not None
        short_hash = save_bundle_hash[:12]
        timestamp = datetime.utcnow()
        run_id = f"{timestamp.strftime('%Y-%m-%dT%H-%M-%S')}_{short_hash}_{branch}"

        run = BacktestRun(
            run_id=run_id,
            timestamp=timestamp,
            git_sha=save_git_sha,
            config=config,
            skill_bundle_name=skills_bundle,
            skill_bundle_hash=save_bundle_hash,
            metrics=result.metrics,
            benchmarks=result.benchmarks,
            snapshots=result.snapshots,
            trades=result.trades,
            signals=result.signals,
            llm_cache_hits=result.llm_cache_hits,
            llm_cache_misses=result.llm_cache_misses,
            effective_agents_config=result.effective_agents_config,
        )
        save_path = save_run(run)
        print(f"\nSaved run: {run_id}")
        print(f"Path:      {save_path}")

    return result


async def main():
    parser = argparse.ArgumentParser(description="Run backtests with ETF-based universes")
    parser.add_argument("start_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("end_date", help="End date (YYYY-MM-DD)")
    parser.add_argument("branches", nargs="*", default=["growth", "value"], help="Branches to run (default: both)")
    parser.add_argument("--capital", type=float, default=1_000.0, help="Initial capital (default: 1000)")
    parser.add_argument(
        "--top-n", type=int, default=None, help="Top N holdings by weight for all branches (default: all)"
    )
    parser.add_argument(
        "--growth-top-n", type=int, default=None, help="Top N holdings for growth branch (overrides --top-n)"
    )
    parser.add_argument(
        "--value-top-n", type=int, default=None, help="Top N holdings for value branch (overrides --top-n)"
    )
    parser.add_argument("--llm", action="store_true", help="Enable LLM-mode analysts (default: quantitative)")
    parser.add_argument("--temperature", type=float, default=None, help="Override analyst LLM temperature")
    parser.add_argument(
        "--max-llm-calls-per-rebalance", type=int, default=None, help="Override the per-rebalance LLM call cap"
    )
    parser.add_argument("--no-llm-cache", action="store_true", help="Disable the persistent LLM response cache")
    parser.add_argument(
        "--skills-bundle",
        type=str,
        default=None,
        help="Load skills from data/skill_bundles/<name> instead of the live directory",
    )
    parser.add_argument("--save", action="store_true", help="Persist the result to data/backtest_runs/")
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
        results[branch] = await run_one(
            branch,
            start,
            end,
            args.capital,
            top_n=top_n,
            use_llm=args.llm,
            temperature=args.temperature,
            max_llm_calls_per_rebalance=args.max_llm_calls_per_rebalance,
            use_llm_cache=not args.no_llm_cache,
            skills_bundle=args.skills_bundle,
            save=args.save,
        )

    if "growth" in results and "value" in results and results["growth"].metrics and results["value"].metrics:
        g, v = results["growth"].metrics, results["value"].metrics
        print(f"\n{'=' * 60}")
        print("  COMPARISON: GROWTH vs VALUE")
        print(f"{'=' * 60}")
        print(f"  {'Metric':<22s} {'Growth':>10s} {'Value':>10s}")
        print(f"  {'-' * 44}")
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

    # --- Strategy vs. ETF benchmark (Growth vs VOOG, Value vs VOOV) ---
    etf_map = {"growth": "VOOG", "value": "VOOV"}
    for branch, etf in etf_map.items():
        r = results.get(branch)
        if r is None or r.metrics is None:
            continue
        bench = next((b for b in r.benchmarks if b.benchmark_symbol == etf), None)
        if bench is None:
            continue
        m = r.metrics
        print(f"\n{'=' * 60}")
        print(f"  {branch.upper()} BRANCH vs {etf}")
        print(f"{'=' * 60}")
        print(f"  {'Metric':<22s} {branch.upper():>10s} {etf:>10s}")
        print(f"  {'-' * 44}")
        print(f"  {'Total Return':<22s} {m.total_return:>+9.2%} {bench.benchmark_total_return:>+9.2%}")
        print(f"  {'Ann. Return':<22s} {m.annualized_return:>+9.2%} {bench.benchmark_annualized_return:>+9.2%}")
        print(f"  {'Sharpe Ratio':<22s} {m.sharpe_ratio:>10.2f} {bench.benchmark_sharpe:>10.2f}")
        print(f"  {'Max Drawdown':<22s} {m.max_drawdown:>9.2%} {bench.benchmark_max_drawdown:>9.2%}")
        print(f"  {'-' * 44}")
        print(f"  {'Alpha':<22s} {bench.alpha:>+9.2%}")
        print(f"  {'Beta':<22s} {bench.beta:>10.2f}")
        print(f"  {'Info Ratio':<22s} {bench.information_ratio:>10.2f}")
        print(f"  {'Tracking Error':<22s} {bench.tracking_error:>9.2%}")
        print(f"  {'Up Capture':<22s} {bench.up_capture_ratio:>9.1f}%")
        print(f"  {'Down Capture':<22s} {bench.down_capture_ratio:>9.1f}%")


if __name__ == "__main__":
    asyncio.run(main())

"""Single-run drill-down for a saved BacktestRun.

Usage:
    python -m scripts.inspect_run <run_id> [options]

Default output: header, config block, metrics, benchmarks, trade summary line,
signal count breakdown, LLM cache stats, and a footer hint listing the
opt-in flags for deeper inspection.

Exit codes:
    0  success
    1  run_id not found on disk
"""
from __future__ import annotations

import argparse
import json as json_lib
import sys
from collections import Counter
from pathlib import Path

from app.modules.backtest.result_store import BacktestRun, load_run


def _format_header(run: BacktestRun) -> str:
    lines = [
        f"Run:       {run.run_id}",
        f"Timestamp: {run.timestamp.isoformat()}",
        f"Git SHA:   {run.git_sha[:12]}",
    ]
    return "\n".join(lines)


def _format_config(run: BacktestRun) -> str:
    cfg = run.config
    bundle = run.skill_bundle_name or "live"
    llm_enabled = cfg.use_llm_agents
    llm_cache_enabled = cfg.use_llm_response_cache
    return (
        "Config:\n"
        f"  branch: {cfg.branch_name}  top_n: {cfg.top_n}  "
        f"rebalance: {cfg.rebalance_frequency}\n"
        f"  dates:  {cfg.start_date} → {cfg.end_date}\n"
        f"  LLM:    {'enabled' if llm_enabled else 'disabled'} "
        f"(bundle: {bundle}, hash: {run.skill_bundle_hash[:12]})\n"
        f"  LLM cache: {'enabled' if llm_cache_enabled else 'disabled'}"
    )


def _format_metrics(run: BacktestRun) -> str:
    if run.metrics is None:
        return "Metrics: (none)"
    m = run.metrics
    return (
        "Metrics:\n"
        f"  total_return    {m.total_return:>8.4f}\n"
        f"  sharpe_ratio    {m.sharpe_ratio:>8.3f}\n"
        f"  sortino_ratio   {m.sortino_ratio:>8.3f}\n"
        f"  max_drawdown    {m.max_drawdown:>8.4f}\n"
        f"  total_trades    {m.total_trades:>8d}\n"
        f"  win_rate        {m.win_rate:>8.3f}"
    )


def _format_benchmarks(run: BacktestRun) -> str:
    if not run.benchmarks:
        return "Benchmarks: (none)"
    lines = ["Benchmarks:"]
    for bc in run.benchmarks:
        lines.append(
            f"  {bc.benchmark_symbol}:  "
            f"alpha {bc.alpha:>+.3f}  beta {bc.beta:>.3f}"
        )
    return "\n".join(lines)


def _format_trade_summary(run: BacktestRun) -> str:
    trades = run.trades
    if not trades:
        return "Trades: 0 total"
    total = len(trades)
    buys = sum(1 for t in trades if t.side == "buy")
    sells = sum(1 for t in trades if t.side == "sell")
    symbols = sorted({t.symbol for t in trades})
    if len(symbols) <= 5:
        symbols_str = ", ".join(symbols)
    else:
        top3 = [s for s, _ in Counter(t.symbol for t in trades).most_common(3)]
        symbols_str = f"{len(symbols)} distinct symbols, top 3: {', '.join(top3)}"
    return (
        f"Trades: {total} total  (buy: {buys}, sell: {sells})\n"
        f"  symbols: {symbols_str}"
    )


def _format_signal_count_breakdown(run: BacktestRun) -> str:
    signals = run.signals
    if not signals:
        return "Signals: 0 total"
    per_analyst: Counter[str] = Counter(s.analyst_type for s in signals)
    per_analyst_per_symbol: dict[str, Counter[str]] = {}
    for s in signals:
        per_analyst_per_symbol.setdefault(s.analyst_type, Counter())[s.symbol] += 1

    lines = [f"Signals: {len(signals)} total"]
    for analyst_type in sorted(per_analyst):
        sym_counts = per_analyst_per_symbol[analyst_type]
        symbols_formatted = ", ".join(
            f"{sym}: {cnt}" for sym, cnt in sym_counts.most_common()
        )
        lines.append(
            f"  {analyst_type}: {per_analyst[analyst_type]}  ({symbols_formatted})"
        )
    for missing in ("news", "fundamentals", "technical"):
        if missing not in per_analyst:
            lines.append(f"  {missing}: 0")
    return "\n".join(lines)


def _format_cache_stats(run: BacktestRun) -> str:
    hits = run.llm_cache_hits
    misses = run.llm_cache_misses
    total = hits + misses
    if total == 0:
        return "LLM cache: 0 hits, 0 misses (N/A)"
    rate = hits / total * 100
    return f"LLM cache: {hits} hits, {misses} misses ({rate:.0f}% hit rate)"


_FOOTER_HINT = (
    "\nPass --trades, --signals, or --snapshots to dump sections in full.\n"
    "Filter signals with --symbol SYM or --analyst-type TYPE.\n"
    "Pass --json for a structured dump of the full run."
)


def _format_full_trade_list(run: BacktestRun, *, symbol_filter: str | None) -> str:
    trades = run.trades
    if symbol_filter:
        trades = [t for t in trades if t.symbol == symbol_filter]
    lines = [f"──── TRADES ({len(trades)}) ────"]
    for t in trades:
        lines.append(
            f"  {t.trade_date}  {t.side:4s}  {t.quantity:>8.4f}  "
            f"{t.symbol:<6s}  @ ${t.price:>10.2f}"
        )
    return "\n".join(lines)


def _format_full_signal_list(
    run: BacktestRun,
    *,
    symbol_filter: str | None,
    analyst_type_filter: str | None,
    top_n: int | None,
) -> str:
    signals = list(run.signals)
    if symbol_filter:
        signals = [s for s in signals if s.symbol == symbol_filter]
    if analyst_type_filter:
        signals = [s for s in signals if s.analyst_type == analyst_type_filter]

    signals.sort(key=lambda s: (s.date, s.symbol, s.analyst_type))

    total_after_filter = len(signals)
    if top_n is not None and top_n > 0:
        signals = signals[:top_n]

    lines = [f"──── SIGNALS ({total_after_filter}) ────"]
    if top_n is not None and top_n < total_after_filter:
        lines.append(f"  (showing first {top_n} chronologically)")
    lines.append("")
    for s in signals:
        lines.append(
            f"  {s.date}  {s.symbol:<6s}  {s.analyst_type:<14s}  "
            f"score={s.bullish_score}  conf={s.confidence}"
        )
        lines.append(f"      {s.summary}")
        lines.append("")
    return "\n".join(lines)


def _format_full_snapshot_list(run: BacktestRun) -> str:
    lines = [f"──── SNAPSHOTS ({len(run.snapshots)}) ────"]
    for snap in run.snapshots:
        lines.append(
            f"  {snap.date}  nav={snap.nav:>12.2f}  "
            f"cash={snap.cash:>12.2f}  positions={snap.position_count}"
        )
    return "\n".join(lines)


def _emit_json(
    run: BacktestRun,
    *,
    symbol_filter: str | None,
    analyst_type_filter: str | None,
) -> None:
    """Dump the full BacktestRun as JSON, optionally filtering signals + trades."""
    data = json_lib.loads(run.model_dump_json())
    if symbol_filter:
        data["trades"] = [
            t for t in data["trades"] if t.get("symbol") == symbol_filter
        ]
        data["signals"] = [
            s for s in data["signals"] if s.get("symbol") == symbol_filter
        ]
    if analyst_type_filter:
        data["signals"] = [
            s for s in data["signals"] if s.get("analyst_type") == analyst_type_filter
        ]
    print(json_lib.dumps(data, indent=2, default=str))


def _print_default_report(run: BacktestRun) -> None:
    print(_format_header(run))
    print()
    print(_format_config(run))
    print()
    print(_format_metrics(run))
    print()
    print(_format_benchmarks(run))
    print()
    print(_format_trade_summary(run))
    print()
    print(_format_signal_count_breakdown(run))
    print()
    print(_format_cache_stats(run))
    print(_FOOTER_HINT)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect a saved BacktestRun file from data/backtest_runs/."
    )
    parser.add_argument("run_id", help="run_id of the BacktestRun to inspect")
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path("data/backtest_runs"),
        help="Directory containing saved BacktestRun JSON files",
    )
    parser.add_argument("--trades", action="store_true", help="Dump full trade list")
    parser.add_argument(
        "--signals",
        action="store_true",
        help="Dump all signals with verbatim analyst summaries",
    )
    parser.add_argument(
        "--signals-top",
        type=int,
        default=None,
        help="With --signals, dump only the first N signals chronologically",
    )
    parser.add_argument(
        "--snapshots",
        action="store_true",
        help="Dump full per-day NAV snapshot list",
    )
    parser.add_argument(
        "--symbol", default=None, help="Filter trades and signals to a symbol"
    )
    parser.add_argument(
        "--analyst-type",
        default=None,
        help="Filter signals to a specific analyst type (fundamentals, technical, news)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Dump the full BacktestRun as JSON; ignores text-section flags "
        "except --symbol and --analyst-type which filter the signals/trades arrays.",
    )
    args = parser.parse_args(argv)

    try:
        run = load_run(args.run_id, runs_dir=args.runs_dir)
    except FileNotFoundError as e:
        print(f"ERROR: run not found: {e}", file=sys.stderr)
        return 1

    if args.json:
        _emit_json(
            run,
            symbol_filter=args.symbol,
            analyst_type_filter=args.analyst_type,
        )
        return 0

    _print_default_report(run)

    if args.trades:
        print()
        print(_format_full_trade_list(run, symbol_filter=args.symbol))

    if args.signals:
        print()
        print(
            _format_full_signal_list(
                run,
                symbol_filter=args.symbol,
                analyst_type_filter=args.analyst_type,
                top_n=args.signals_top,
            )
        )

    if args.snapshots:
        print()
        print(_format_full_snapshot_list(run))

    return 0


if __name__ == "__main__":
    sys.exit(main())

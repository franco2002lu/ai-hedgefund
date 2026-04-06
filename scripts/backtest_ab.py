"""A/B comparison harness for backtests.

Usage:
    # 1. Save a baseline before making changes
    python -m scripts.backtest_ab snapshot 2024-01-01 2025-12-31 --top-n 50 -o baseline.json

    # 2. Make code changes

    # 3. Save a new snapshot under a different filename
    python -m scripts.backtest_ab snapshot 2024-01-01 2025-12-31 --top-n 50 -o current.json

    # 4. Compare the two
    python -m scripts.backtest_ab diff baseline.json current.json

The diff exits non-zero when snapshots differ, so it can gate CI checks.
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

from app.modules.backtest.config import BacktestConfig, RebalanceFrequency
from app.modules.backtest.engine import BacktestEngine

logging.basicConfig(level=logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


# ---------------------------------------------------------------------------
# snapshot subcommand
# ---------------------------------------------------------------------------

async def run_snapshot(
    start: date,
    end: date,
    branches: list[str],
    capital: float,
    top_n: int | None,
    growth_top_n: int | None,
    value_top_n: int | None,
    output_path: Path,
) -> None:
    per_branch_top_n = {"growth": growth_top_n, "value": value_top_n}
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "command": {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "branches": branches,
            "capital": capital,
            "top_n": top_n,
            "growth_top_n": growth_top_n,
            "value_top_n": value_top_n,
        },
        "results": {},
    }

    engine = BacktestEngine()
    for branch in branches:
        effective_top_n = per_branch_top_n.get(branch) if per_branch_top_n.get(branch) is not None else top_n
        config = BacktestConfig(
            start_date=start,
            end_date=end,
            initial_capital=capital,
            rebalance_frequency=RebalanceFrequency.WEEKLY,
            branch_name=branch,
            use_llm_agents=False,
            benchmark_symbols=["SPY", "VOOG" if "growth" in branch else "VOOV"],
            top_n=effective_top_n,
        )
        print(f"Running {branch} ({start} → {end}, top_n={effective_top_n})...")
        result = await engine.run(config)
        snapshot["results"][branch] = json.loads(result.model_dump_json())
        if result.metrics:
            print(
                f"  status={result.status}  trades={len(result.trades)}  "
                f"return={result.metrics.total_return:+.2%}  sharpe={result.metrics.sharpe_ratio:.2f}"
            )
        else:
            print(f"  status={result.status}  error={result.error_message}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, indent=2))
    print(f"\nSaved snapshot to: {output_path}")


# ---------------------------------------------------------------------------
# diff subcommand
# ---------------------------------------------------------------------------

# (key, label, format) — format is one of: pct, ratio, int
METRIC_ROWS: list[tuple[str, str, str]] = [
    ("total_return", "Total Return", "pct"),
    ("annualized_return", "Annualized Return", "pct"),
    ("volatility", "Volatility (ann)", "pct"),
    ("sharpe_ratio", "Sharpe Ratio", "ratio"),
    ("sortino_ratio", "Sortino Ratio", "ratio"),
    ("calmar_ratio", "Calmar Ratio", "ratio"),
    ("max_drawdown", "Max Drawdown", "pct"),
    ("max_drawdown_duration_days", "Max DD Duration (d)", "int"),
    ("win_rate", "Win Rate", "pct"),
    ("profit_factor", "Profit Factor", "ratio"),
    ("turnover_rate", "Turnover Rate", "ratio"),
    ("total_trades", "Total Trades", "int"),
    ("avg_position_count", "Avg Positions", "ratio"),
    ("max_position_count", "Max Positions", "int"),
]

BENCHMARK_ROWS: list[tuple[str, str, str]] = [
    ("benchmark_total_return", "Benchmark Return", "pct"),
    ("alpha", "Alpha", "pct"),
    ("beta", "Beta", "ratio"),
    ("information_ratio", "Info Ratio", "ratio"),
    ("up_capture_ratio", "Up Capture", "ratio"),
    ("down_capture_ratio", "Down Capture", "ratio"),
]


def _fmt(value: Any, kind: str) -> str:
    if value is None:
        return "--"
    if kind == "pct":
        return f"{value:+.2%}"
    if kind == "int":
        return f"{int(value):d}"
    return f"{value:.2f}"


# Display precision is 2 decimals everywhere — metric deltas below this are noise.
_METRIC_TOLERANCES = {
    "pct": 5e-5,    # 0.005% absolute (half of display precision)
    "ratio": 5e-3,  # 0.005 (half of display precision)
    "int": 0,       # ints are always meaningful
}


def _delta(a: Any, b: Any, kind: str, strict: bool) -> tuple[str, bool]:
    """Return (formatted delta, is_meaningful_diff)."""
    if a is None or b is None:
        return ("", a != b)
    diff = b - a
    tol = 0 if strict else _METRIC_TOLERANCES.get(kind, 0)
    is_meaningful = abs(diff) > tol
    if kind == "pct":
        return (f"{diff:+.2%}", is_meaningful)
    if kind == "int":
        return (f"{int(diff):+d}", is_meaningful)
    return (f"{diff:+.2f}", is_meaningful)


def _print_metric_table(rows: list[tuple[str, str, str]], a: dict, b: dict, strict: bool) -> bool:
    any_diff = False
    print(f"    {'Metric':<22s} {'Baseline':>14s} {'Current':>14s} {'Δ':>14s}")
    print(f"    {'-' * 68}")
    for key, label, kind in rows:
        va = a.get(key) if a else None
        vb = b.get(key) if b else None
        sa = _fmt(va, kind)
        sb = _fmt(vb, kind)
        sd, is_meaningful = _delta(va, vb, kind, strict)
        marker = "  *" if is_meaningful else ""
        print(f"    {label:<22s} {sa:>14s} {sb:>14s} {sd:>14s}{marker}")
        if is_meaningful:
            any_diff = True
    return any_diff


def _trade_key(t: dict) -> tuple:
    return (t["trade_date"], t["symbol"], t["side"])


# Tolerances for "noise vs signal" classification.
# Anything below these thresholds is treated as float-compounding noise from
# Yahoo Finance data and ignored. Override with --strict for bit-exact compare.
_QTY_TOLERANCE = 0.01     # 1/100 of a share
_PRICE_TOLERANCE = 0.01   # 1 cent


def _diff_trades(a_trades: list[dict], b_trades: list[dict], strict: bool) -> bool:
    a_by_key = {_trade_key(t): t for t in a_trades}
    b_by_key = {_trade_key(t): t for t in b_trades}
    a_keys = set(a_by_key)
    b_keys = set(b_by_key)

    only_a = sorted(a_keys - b_keys)
    only_b = sorted(b_keys - a_keys)
    common = a_keys & b_keys

    qty_tol = 0.0 if strict else _QTY_TOLERANCE
    price_tol = 1e-9 if strict else _PRICE_TOLERANCE

    identical = 0       # within tolerance
    noise = 0           # differs but below tolerance (only counted in non-strict mode)
    modified: list[tuple[dict, dict]] = []  # differs above tolerance
    for k in common:
        ta, tb = a_by_key[k], b_by_key[k]
        qty_diff = abs(ta["quantity"] - tb["quantity"])
        price_diff = abs(ta["price"] - tb["price"])
        bit_exact = qty_diff == 0.0 and price_diff < 1e-9
        within_tol = qty_diff <= qty_tol and price_diff <= price_tol
        if bit_exact:
            identical += 1
        elif within_tol:
            noise += 1
        else:
            modified.append((ta, tb))

    print(f"\n  Trades:  baseline={len(a_trades)}  current={len(b_trades)}")
    print(f"    Identical (bit-exact):    {identical}")
    if not strict:
        print(f"    Noise (within tol):       {noise}    [|qty|≤{_QTY_TOLERANCE}, |price|≤${_PRICE_TOLERANCE}]")
    print(f"    Modified (above tol):     {len(modified)}")
    print(f"    Removed (only baseline):  {len(only_a)}")
    print(f"    Added (only current):     {len(only_b)}")

    if modified:
        print(f"\n    Modified examples (first 5):")
        for ta, tb in modified[:5]:
            qty_delta = tb["quantity"] - ta["quantity"]
            price_delta = tb["price"] - ta["price"]
            print(
                f"      {ta['trade_date']} {ta['side']:4s} {ta['symbol']:<6s}  "
                f"qty {ta['quantity']:>10.6f} → {tb['quantity']:<10.6f} (Δ {qty_delta:+.6f})  "
                f"price ${ta['price']:>10.4f} → ${tb['price']:<10.4f} (Δ {price_delta:+.4f})"
            )
    if only_a:
        print(f"\n    Removed examples (first 5):")
        for k in only_a[:5]:
            t = a_by_key[k]
            print(f"      {t['trade_date']} {t['side']:4s} {t['symbol']:<6s} qty {t['quantity']:>10.6f} @ ${t['price']:>10.4f}")
    if only_b:
        print(f"\n    Added examples (first 5):")
        for k in only_b[:5]:
            t = b_by_key[k]
            print(f"      {t['trade_date']} {t['side']:4s} {t['symbol']:<6s} qty {t['quantity']:>10.6f} @ ${t['price']:>10.4f}")

    return bool(modified or only_a or only_b)


def _diff_nav(a_snaps: list[dict], b_snaps: list[dict], strict: bool) -> bool:
    a_by_date = {s["date"]: s for s in a_snaps}
    b_by_date = {s["date"]: s for s in b_snaps}
    common_dates = sorted(set(a_by_date) & set(b_by_date))

    if not common_dates:
        print("\n  NAV: no common dates")
        return bool(a_snaps) or bool(b_snaps)

    # NAV tolerance: 1 cent in non-strict mode, bit-exact in strict
    nav_tol = 1e-9 if strict else 0.01

    first_diff_date: str | None = None
    for d in common_dates:
        if abs(a_by_date[d]["nav"] - b_by_date[d]["nav"]) > nav_tol:
            first_diff_date = d
            break

    last_d = common_dates[-1]
    a_last = a_by_date[last_d]["nav"]
    b_last = b_by_date[last_d]["nav"]
    final_delta = b_last - a_last

    print(f"\n  NAV:")
    if first_diff_date is None:
        tol_label = "bit-exact" if strict else f"|Δ|≤${nav_tol:.2f}"
        print(f"    No divergence — NAVs match across all {len(common_dates)} days ({tol_label})")
    else:
        a_v = a_by_date[first_diff_date]["nav"]
        b_v = b_by_date[first_diff_date]["nav"]
        print(f"    First divergence: {first_diff_date}  ${a_v:,.2f} → ${b_v:,.2f}  (Δ ${b_v - a_v:+,.2f})")
    print(f"    Final NAV ({last_d}):  ${a_last:,.2f} → ${b_last:,.2f}  (Δ ${final_delta:+,.2f})")

    return first_diff_date is not None or abs(final_delta) > nav_tol


def run_diff(baseline_path: Path, current_path: Path, strict: bool) -> int:
    a = json.loads(baseline_path.read_text())
    b = json.loads(current_path.read_text())

    print(f"Comparing:")
    print(f"  baseline: {baseline_path}")
    print(f"  current:  {current_path}")
    print(f"  mode:     {'strict (bit-exact)' if strict else 'tolerance (ignores float-precision noise)'}")

    cmd_a = a.get("command", {})
    cmd_b = b.get("command", {})
    if cmd_a != cmd_b:
        print("\n  WARNING: command parameters differ between snapshots:")
        for key in sorted(set(cmd_a) | set(cmd_b)):
            if cmd_a.get(key) != cmd_b.get(key):
                print(f"    {key}: {cmd_a.get(key)!r} → {cmd_b.get(key)!r}")
        print("  (continuing anyway, but the comparison may not be meaningful)")

    a_results = a.get("results", {})
    b_results = b.get("results", {})
    a_branches = set(a_results)
    b_branches = set(b_results)
    only_a = a_branches - b_branches
    only_b = b_branches - a_branches
    common = sorted(a_branches & b_branches)

    if only_a:
        print(f"\n  Branches only in baseline: {sorted(only_a)}")
    if only_b:
        print(f"\n  Branches only in current:  {sorted(only_b)}")

    any_diff = bool(only_a or only_b)

    for branch in common:
        ar = a_results[branch]
        br = b_results[branch]
        print(f"\n{'=' * 70}")
        print(f"  BRANCH: {branch.upper()}")
        print(f"{'=' * 70}")

        if ar.get("status") != br.get("status"):
            print(f"  Status: {ar.get('status')} → {br.get('status')}")
            any_diff = True

        if ar.get("rebalance_count") != br.get("rebalance_count"):
            print(
                f"  Rebalances: {ar.get('rebalance_count')} → {br.get('rebalance_count')}"
            )
            any_diff = True

        print("\n  Performance Metrics:")
        if _print_metric_table(METRIC_ROWS, ar.get("metrics") or {}, br.get("metrics") or {}, strict):
            any_diff = True

        a_benches = {bm["benchmark_symbol"]: bm for bm in ar.get("benchmarks", [])}
        b_benches = {bm["benchmark_symbol"]: bm for bm in br.get("benchmarks", [])}
        for sym in sorted(set(a_benches) | set(b_benches)):
            print(f"\n  Benchmark ({sym}):")
            if _print_metric_table(BENCHMARK_ROWS, a_benches.get(sym, {}), b_benches.get(sym, {}), strict):
                any_diff = True

        if _diff_trades(ar.get("trades", []), br.get("trades", []), strict):
            any_diff = True

        if _diff_nav(ar.get("snapshots", []), br.get("snapshots", []), strict):
            any_diff = True

    print(f"\n{'=' * 70}")
    if any_diff:
        print("Result: SNAPSHOTS DIFFER")
        return 1
    print("Result: SNAPSHOTS IDENTICAL")
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest A/B comparison harness")
    sub = parser.add_subparsers(dest="cmd", required=True)

    snap = sub.add_parser("snapshot", help="Run a backtest and save the result to JSON")
    snap.add_argument("start_date", help="Start date (YYYY-MM-DD)")
    snap.add_argument("end_date", help="End date (YYYY-MM-DD)")
    snap.add_argument("branches", nargs="*", default=["growth", "value"],
                      help="Branches to run (default: growth value)")
    snap.add_argument("--capital", type=float, default=1_000.0)
    snap.add_argument("--top-n", type=int, default=None)
    snap.add_argument("--growth-top-n", type=int, default=None)
    snap.add_argument("--value-top-n", type=int, default=None)
    snap.add_argument("-o", "--output", required=True, type=Path,
                      help="Path to write the snapshot JSON")

    diff = sub.add_parser("diff", help="Diff two saved snapshots")
    diff.add_argument("baseline", type=Path)
    diff.add_argument("current", type=Path)
    diff.add_argument("--strict", action="store_true",
                      help="Bit-exact comparison (otherwise ignores sub-cent / sub-share noise)")

    return parser


def main() -> None:
    args = _build_parser().parse_args()

    if args.cmd == "snapshot":
        asyncio.run(run_snapshot(
            start=date.fromisoformat(args.start_date),
            end=date.fromisoformat(args.end_date),
            branches=args.branches,
            capital=args.capital,
            top_n=args.top_n,
            growth_top_n=args.growth_top_n,
            value_top_n=args.value_top_n,
            output_path=args.output,
        ))
        sys.exit(0)
    elif args.cmd == "diff":
        sys.exit(run_diff(args.baseline, args.current, strict=args.strict))


if __name__ == "__main__":
    main()

"""Compare two saved BacktestRun files and print a delta report.

Usage:
    python -m scripts.compare_runs <baseline_id> <treatment_id> [options]

Exit codes:
    0  success
    1  either run_id not found on disk
    2  wholly incompatible date ranges (no chance of a meaningful comparison)

Text output (default):
    - Loud banner naming the caveat (raw deltas, no noise floor — see Phase 3)
    - Compatibility warnings
    - Metric delta table
    - Signal divergence drilldown (unless --metrics-only)
    - Universe drift footer (or full list with --show-drift)

JSON output (--json):
    Curated schema — see RunComparison.to_json_dict. Does NOT duplicate the
    full BacktestRun payloads; consumers can re-load them by run_id.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.modules.backtest.comparison import (
    compare_runs,
    format_drift_section,
    format_metric_table,
    format_signal_drilldown,
)
from app.modules.backtest.result_store import load_run


def _wholly_incompatible(baseline, treatment) -> bool:
    """Check whether the two runs have any date overlap. If not, comparison is
    pointless — exit 2 rather than generating a misleading report.
    """
    b = baseline.config
    t = treatment.config
    return b.end_date < t.start_date or t.end_date < b.start_date


def _print_text_report(cmp, *, metrics_only: bool, top_n: int, show_drift: bool) -> None:
    if cmp.high_drift_warning:
        print(f"\n⚠  {cmp.high_drift_warning}\n")

    if cmp.compatibility_warnings:
        print("Compatibility warnings:")
        for w in cmp.compatibility_warnings:
            print(f"  - {w}")
        print()

    print(
        f"Baseline:  {cmp.baseline.run_id}  "
        f"(skill_bundle_hash={cmp.baseline.skill_bundle_hash[:12]})"
    )
    print(
        f"Treatment: {cmp.treatment.run_id}  "
        f"(skill_bundle_hash={cmp.treatment.skill_bundle_hash[:12]})"
    )
    print()

    print(format_metric_table(cmp))
    if not metrics_only:
        print(format_signal_drilldown(cmp, top_n=top_n))
    print(format_drift_section(cmp, show_drift=show_drift))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare two saved BacktestRun files and print a delta report."
    )
    parser.add_argument("baseline_id", help="run_id of the baseline run")
    parser.add_argument("treatment_id", help="run_id of the treatment run")
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path("data/backtest_runs"),
        help="Directory containing saved BacktestRun JSON files (default: data/backtest_runs)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="Max divergences to display in the drilldown (default: 20)",
    )
    parser.add_argument(
        "--metrics-only",
        action="store_true",
        help="Skip the signal drilldown — show only the metric table.",
    )
    parser.add_argument(
        "--min-confidence",
        type=int,
        default=0,
        help="Drop divergences where max(baseline_conf, treatment_conf) < floor (default: 0)",
    )
    parser.add_argument(
        "--include-conviction-shifts",
        action="store_true",
        help="Also show score_delta=0 rows where confidence moved.",
    )
    parser.add_argument(
        "--show-drift",
        action="store_true",
        help="Dump the full universe drift list as a dedicated section.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit curated JSON instead of text; suppresses text output.",
    )
    args = parser.parse_args(argv)

    try:
        baseline = load_run(args.baseline_id, runs_dir=args.runs_dir)
    except FileNotFoundError as e:
        print(f"ERROR: baseline run not found: {e}", file=sys.stderr)
        return 1
    try:
        treatment = load_run(args.treatment_id, runs_dir=args.runs_dir)
    except FileNotFoundError as e:
        print(f"ERROR: treatment run not found: {e}", file=sys.stderr)
        return 1

    if _wholly_incompatible(baseline, treatment):
        print(
            "ERROR: runs have no date overlap — comparison is meaningless. "
            f"baseline: {baseline.config.start_date}→{baseline.config.end_date}, "
            f"treatment: {treatment.config.start_date}→{treatment.config.end_date}",
            file=sys.stderr,
        )
        return 2

    cmp = compare_runs(
        baseline,
        treatment,
        min_confidence=args.min_confidence,
        include_conviction_shifts=args.include_conviction_shifts,
    )

    if args.json:
        print(json.dumps(cmp.to_json_dict(), indent=2))
    else:
        _print_text_report(
            cmp,
            metrics_only=args.metrics_only,
            top_n=args.top_n,
            show_drift=args.show_drift,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())

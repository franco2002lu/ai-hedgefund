"""Full experiment harness CLI: run a baseline-vs-treatment experiment with verdict labels.

Usage:
    python -m scripts.run_experiment --preset medium --branch growth \\
        --end-date 2025-12-31 \\
        --baseline-bundle baseline_v1 --treatment-bundle live \\
        [--t-correction] [--report-out path/to/report.txt] [--json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from datetime import date, datetime
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a baseline-vs-treatment experiment with statistical verdict labels.",
    )
    parser.add_argument(
        "--preset",
        required=True,
        choices=["quick", "medium", "full"],
        help="Backtest tier preset",
    )
    parser.add_argument("--branch", required=True, help="Branch name (growth or value)")
    parser.add_argument("--end-date", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--baseline-bundle",
        required=True,
        help="Skill bundle name for baseline (or 'live')",
    )
    parser.add_argument(
        "--treatment-bundle",
        required=True,
        help="Skill bundle name for treatment (or 'live')",
    )
    parser.add_argument(
        "--t-correction",
        action="store_true",
        help="Use t-distribution correction for verdict thresholds",
    )
    parser.add_argument(
        "--report-out",
        default=None,
        help="Write report to file instead of stdout",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output experiment result as JSON instead of text report",
    )
    return parser


async def run_experiment_cli(args) -> None:
    """Main experiment logic."""
    from app.modules.backtest.config import BacktestTier, config_from_preset
    from app.modules.backtest.engine import BacktestEngine
    from app.modules.backtest.experiment import (
        ExperimentRunner,
        format_experiment_report,
        save_experiment_result,
    )
    from app.modules.backtest.noise_floor_store import NoiseFloorStore
    from app.modules.backtest.result_store import BacktestRun, hash_skill_bundle
    from app.modules.equities.config import EquitiesConfig

    end_date_val = date.fromisoformat(args.end_date)
    preset = BacktestTier(args.preset)
    config = config_from_preset(preset, args.branch, end_date=end_date_val)

    equities_config = config.equities_config_override or EquitiesConfig()
    agents_config = equities_config.agents

    store = NoiseFloorStore()

    # Resolve "live" to None for the engine
    baseline_bundle = None if args.baseline_bundle == "live" else args.baseline_bundle
    treatment_bundle = None if args.treatment_bundle == "live" else args.treatment_bundle

    class CLIExperimentRunner(ExperimentRunner):
        async def _run_backtest(self, config, skills_bundle):
            run_config = config.model_copy(
                update={
                    "use_llm_response_cache": True,
                    "skills_bundle": skills_bundle,
                }
            )

            engine_instance = BacktestEngine()
            result = await engine_instance.run(run_config)

            # Determine bundle hash
            if skills_bundle and skills_bundle != "live":
                fp_dir = Path("data/skill_bundles") / skills_bundle
            else:
                fp_dir = Path("app/modules/equities/agents/skills")
            sbh = hash_skill_bundle(fp_dir)

            try:
                git_sha = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
            except Exception:
                git_sha = "unknown"

            timestamp = datetime.utcnow()
            run_id = f"{timestamp.strftime('%Y-%m-%dT%H-%M-%S')}_{sbh[:12]}_{config.branch_name}"

            return BacktestRun(
                run_id=run_id,
                timestamp=timestamp,
                git_sha=git_sha,
                config=run_config,
                skill_bundle_name=skills_bundle,
                skill_bundle_hash=sbh,
                metrics=result.metrics,
                benchmarks=result.benchmarks,
                snapshots=result.snapshots,
                trades=result.trades,
                signals=result.signals,
                llm_cache_hits=result.llm_cache_hits,
                llm_cache_misses=result.llm_cache_misses,
                effective_agents_config=result.effective_agents_config,
            )

    runner = CLIExperimentRunner(
        result_store_path=Path("data/backtest_runs"),
        noise_floor_store=store,
    )

    try:
        result = await runner.run_experiment(
            config=config,
            agents_config=agents_config,
            baseline_skills_bundle=baseline_bundle,
            treatment_skills_bundle=treatment_bundle,
            use_t_correction=args.t_correction,
        )
    finally:
        store.close()

    # Output
    output = json.dumps(result.to_json_dict(), indent=2) if args.json else format_experiment_report(result)

    if args.report_out:
        Path(args.report_out).write_text(output, encoding="utf-8")
        print(f"Report written to {args.report_out}")
    else:
        print(output)

    # Always save the experiment result JSON
    saved_path = save_experiment_result(result)
    print(f"Experiment saved: {saved_path}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(run_experiment_cli(args))


if __name__ == "__main__":
    main()

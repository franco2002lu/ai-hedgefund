"""Variance probing CLI: run N backtests with cache disabled to estimate per-metric noise.

Usage:
    python -m scripts.probe_noise --preset medium --branch growth --end-date 2025-12-31 \\
        [--runs 5] [--skills-bundle NAME] [--invalidate] [--force] [--yes]
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe LLM backtest variance to establish a per-metric noise floor.",
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
        "--runs",
        type=int,
        default=5,
        help="Number of probe runs (default: 5, min: 3)",
    )
    parser.add_argument(
        "--skills-bundle",
        default=None,
        help="Named skill bundle to probe against (default: live skills)",
    )
    parser.add_argument(
        "--invalidate",
        action="store_true",
        help="Delete the existing noise floor for this config and exit",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-probe even if a noise floor already exists",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the cost confirmation prompt",
    )
    return parser


def validate_args(args) -> None:
    """Validate arguments. Exits on hard errors, warns on soft issues."""
    if args.runs < 3:
        print(
            f"Error: --runs {args.runs} is below the minimum of 3. With fewer than 3 samples, stddev is meaningless.",
            file=sys.stderr,
        )
        sys.exit(1)
    if args.runs > 10:
        print(f"Note: --runs {args.runs} offers marginal improvement over N=5 or N=7. Cost scales linearly with N.")


async def run_probe(args) -> None:
    """Main probe logic."""
    from app.modules.backtest.config import BacktestTier, config_from_preset
    from app.modules.backtest.engine import BacktestEngine
    from app.modules.backtest.noise_floor_store import NoiseFloorStore
    from app.modules.backtest.result_store import BacktestRun, hash_skill_bundle, save_run
    from app.modules.backtest.statistics import (
        compute_noise_floor,
        estimate_experiment_cost,
        hash_experiment_config,
    )
    from app.modules.equities.config import EquitiesConfig

    end_date_val = date.fromisoformat(args.end_date)
    preset = BacktestTier(args.preset)
    config = config_from_preset(preset, args.branch, end_date=end_date_val)

    # Resolve agents config for hashing and cost estimation
    equities_config = config.equities_config_override or EquitiesConfig()
    agents_config = equities_config.agents
    config_hash = hash_experiment_config(config, agents_config)

    store = NoiseFloorStore()
    try:
        # --invalidate: delete and exit
        if args.invalidate:
            if store.invalidate(config_hash):
                print(f"Invalidated noise floor for config_hash={config_hash[:12]}...")
            else:
                print(f"No noise floor found for config_hash={config_hash[:12]}...")
            return

        # Check existing
        existing = store.get(config_hash)
        if existing and not args.force:
            print(f"Noise floor already exists for config_hash={config_hash[:12]}...")
            print(f"  N={existing.n_runs}, created {existing.created_at.isoformat()}")
            print("  Use --force to re-probe, or --invalidate to delete.")
            return

        # Cost estimate and confirmation
        cost_est = estimate_experiment_cost(config, agents_config, n_runs=args.runs)
        print(cost_est.format())
        if not args.yes:
            response = input("Proceed? [y/N] ").strip().lower()
            if response != "y":
                print("Aborted.")
                return

        # Determine skill_bundle_hash and skills_dir for the noise floor metadata
        skills_bundle = args.skills_bundle
        if skills_bundle and skills_bundle != "live":
            fingerprint_dir = Path("data/skill_bundles") / skills_bundle
        else:
            fingerprint_dir = Path("app/modules/equities/agents/skills")
        skill_bundle_hash = hash_skill_bundle(fingerprint_dir)

        # Get git sha
        try:
            git_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except Exception:
            git_sha = "unknown"

        print(f"\nRunning {args.runs} probe backtests (cache disabled)...")
        config_label = f"{args.preset} / {args.branch} / {end_date_val}"
        probe_runs = []
        for i in range(args.runs):
            print(f"  Probe run {i + 1}/{args.runs}...", flush=True)

            probe_config = config.model_copy(
                update={
                    "use_llm_response_cache": False,
                    "skills_bundle": skills_bundle,
                }
            )

            engine = BacktestEngine()
            result = await engine.run(probe_config)

            timestamp = datetime.utcnow()
            run_id = f"probe_{timestamp.strftime('%Y-%m-%dT%H-%M-%S')}_{i}"

            run = BacktestRun(
                run_id=run_id,
                timestamp=timestamp,
                git_sha=git_sha,
                config=probe_config,
                skill_bundle_name=skills_bundle,
                skill_bundle_hash=skill_bundle_hash,
                metrics=result.metrics,
                benchmarks=result.benchmarks,
                snapshots=result.snapshots,
                trades=result.trades,
                signals=result.signals,
                llm_cache_hits=result.llm_cache_hits,
                llm_cache_misses=result.llm_cache_misses,
                effective_agents_config=result.effective_agents_config,
            )
            save_run(run)
            probe_runs.append(run)
            print(f"    Saved: {run_id}")

        # Compute and store noise floor
        nf = compute_noise_floor(
            probe_runs,
            config_hash,
            skill_bundle_hash,
            config_label=config_label,
        )
        store.put(nf)

        # Print summary
        print(f"\nNoise floor stored (config_hash={config_hash[:12]}...)")
        print(f"  N={nf.n_runs} runs")
        print("  Per-metric mean \u00b1 stddev:")
        for name, mnf in sorted(nf.metrics.items()):
            print(f"    {name:<24}  {mnf.mean:>10.4f} \u00b1 {mnf.stddev:.4f}")
    finally:
        store.close()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    validate_args(args)
    asyncio.run(run_probe(args))


if __name__ == "__main__":
    main()

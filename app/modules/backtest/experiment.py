"""Phase 3 experiment harness: orchestration, result containers, and report formatting.

ExperimentRunner ties together the backtest engine, comparison module,
noise floor store, and verdict logic into a single async workflow.
format_experiment_report renders the result as a human-readable report
matching the sample output in §8.7 of the architecture spec.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from app.modules.backtest.comparison import (
    RunComparison,
    compare_runs,
    format_signal_drilldown,
)
from app.modules.backtest.noise_floor_store import NoiseFloorStore
from app.modules.backtest.result_store import BacktestRun, save_run
from app.modules.backtest.statistics import (
    NoiseFloor,
    Verdict,
    compute_verdicts,
    format_verdict_table,
    hash_experiment_config,
)

if TYPE_CHECKING:
    from app.modules.backtest.config import BacktestConfig
    from app.modules.equities.config import AgentsConfig


_STALE_THRESHOLD_DAYS = 30


@dataclass
class ExperimentResult:
    """Full result of a baseline-vs-treatment experiment."""

    baseline_run_id: str
    treatment_run_id: str
    noise_floor: NoiseFloor
    noise_floor_age_days: int
    noise_floor_stale: bool
    bundle_mismatch_warning: bool
    comparison: RunComparison
    verdicts: list[Verdict]
    t_correction_used: bool = False

    def to_json_dict(self) -> dict:
        """Return the curated JSON schema for this experiment result.

        Includes verdicts (primary payload), metric_deltas, signal_divergences,
        and compatibility_warnings from the underlying RunComparison. Runs are
        referenced by ID only. Noise floor is summarized without sample values.
        """
        cfg = self.comparison.baseline.config
        return {
            "experiment_id": f"{self.baseline_run_id}_vs_{self.treatment_run_id}",
            "generated_at": datetime.now(UTC).isoformat(),
            "config_summary": {
                "start_date": cfg.start_date.isoformat(),
                "end_date": cfg.end_date.isoformat(),
                "top_n": cfg.top_n,
                "branch_name": cfg.branch_name,
                "rebalance_frequency": str(cfg.rebalance_frequency),
            },
            "baseline_run_id": self.baseline_run_id,
            "treatment_run_id": self.treatment_run_id,
            "baseline_skill_bundle_hash": self.comparison.baseline.skill_bundle_hash,
            "treatment_skill_bundle_hash": self.comparison.treatment.skill_bundle_hash,
            "noise_floor_summary": {
                "config_hash": self.noise_floor.config_hash,
                "n_runs": self.noise_floor.n_runs,
                "age_days": self.noise_floor_age_days,
                "stale": self.noise_floor_stale,
                "skill_bundle_hash": self.noise_floor.skill_bundle_hash,
                "bundle_mismatch": self.bundle_mismatch_warning,
            },
            "verdicts": [
                {
                    "metric_name": v.metric_name,
                    "baseline": v.baseline,
                    "treatment": v.treatment,
                    "delta": v.delta,
                    "sigma": v.sigma,
                    "label": v.label,
                }
                for v in self.verdicts
            ],
            "metric_deltas": [
                {
                    "name": d.name,
                    "baseline": d.baseline,
                    "treatment": d.treatment,
                    "delta": d.delta,
                }
                for d in self.comparison.metric_deltas
            ],
            "signal_divergences": [
                {
                    "date": d.date.isoformat(),
                    "symbol": d.symbol,
                    "analyst_type": d.analyst_type,
                    "baseline_score": d.baseline_score,
                    "treatment_score": d.treatment_score,
                    "score_delta": d.score_delta,
                    "impact": d.impact,
                }
                for d in self.comparison.signal_divergences
            ],
            "compatibility_warnings": list(self.comparison.compatibility_warnings),
            "t_correction_used": self.t_correction_used,
        }


# ── Report formatter ─────────────────────────────────────────────────────


def format_experiment_report(
    result: ExperimentResult,
    top_n_signals: int = 10,
) -> str:
    """Format an ExperimentResult as a human-readable report.

    Matches the sample output in §8.7 of the architecture spec:
    header → warnings → verdict table → signal drilldown → footer.
    """
    lines: list[str] = []
    sep = "=" * 64

    # Header
    lines.append(sep)
    lines.append("  EXPERIMENT REPORT")

    nf = result.noise_floor
    cfg = result.comparison.baseline.config
    lines.append(f"  Config:           {nf.config_label or 'N/A'}")
    lines.append(f"  Period:           {cfg.start_date} to {cfg.end_date}")

    # Agent config from baseline run
    eac = result.comparison.baseline.effective_agents_config
    if eac:
        models = set()
        temps = set()
        for attr in ("news_analyst", "fundamentals_analyst", "technical_analyst"):
            models.add(getattr(eac, attr).model)
            temps.add(str(getattr(eac, attr).temperature))
        lines.append(f"  Model:            {', '.join(sorted(models))} @ temperature {', '.join(sorted(temps))}")

    b_hash = result.comparison.baseline.skill_bundle_hash[:12]
    t_hash = result.comparison.treatment.skill_bundle_hash[:12]
    b_name = result.comparison.baseline.skill_bundle_name or "live"
    t_name = result.comparison.treatment.skill_bundle_name or "live"
    lines.append(f"  Baseline skills:  {b_name} (sha: {b_hash}...)")
    lines.append(f"  Treatment skills: {t_name} (sha: {t_hash}...)")

    # Noise floor status
    age_str = f"N={nf.n_runs}, age {result.noise_floor_age_days} days"
    status = f"\u26a0 stale ({result.noise_floor_age_days} days old)" if result.noise_floor_stale else "\u2713 fresh"
    lines.append(f"  Noise floor:      {age_str}  {status}")

    if result.bundle_mismatch_warning:
        lines.append(f"  \u26a0 Noise floor probed against different bundle (sha: {nf.skill_bundle_hash[:12]}...)")

    lines.append(sep)

    # Compatibility warnings
    if result.comparison.compatibility_warnings:
        lines.append("")
        lines.append("--- Compatibility Warnings ---")
        for w in result.comparison.compatibility_warnings:
            lines.append(f"  \u26a0 {w}")

    # Verdict table
    lines.append(
        format_verdict_table(
            result.verdicts,
            n_runs=nf.n_runs,
            t_correction=result.t_correction_used,
        )
    )

    # Signal drilldown
    lines.append(format_signal_drilldown(result.comparison, top_n=top_n_signals))

    # Footer
    lines.append("")
    lines.append(sep)

    return "\n".join(lines)


# ── Experiment runner ────────────────────────────────────────────────────


class ExperimentRunner:
    """Orchestrates a baseline-vs-treatment experiment.

    The runner does NOT own the BacktestEngine or LLMResponseCache directly.
    Subclasses or callers override _run_backtest to supply the engine invocation.
    The default implementation raises NotImplementedError; the CLI scripts
    (run_experiment.py) provide the real wiring.
    """

    def __init__(
        self,
        result_store_path: Path,
        noise_floor_store: NoiseFloorStore,
    ) -> None:
        self._result_store_path = result_store_path
        self._noise_floor_store = noise_floor_store

    async def _run_backtest(
        self,
        config: BacktestConfig,
        skills_bundle: str | None,
    ) -> BacktestRun:
        """Run a single backtest and return the result as a BacktestRun.

        Override this in the CLI script to wire up the real BacktestEngine.
        """
        raise NotImplementedError("ExperimentRunner._run_backtest must be overridden by the caller")

    async def run_experiment(
        self,
        config: BacktestConfig,
        agents_config: AgentsConfig,
        baseline_skills_bundle: str | None,
        treatment_skills_bundle: str | None,
        *,
        use_t_correction: bool = False,
    ) -> ExperimentResult:
        """Run a full experiment: look up noise floor, run both backtests,
        compare, compute verdicts, return result.

        Raises RuntimeError if no noise floor exists for this config_hash.
        """
        config_hash = hash_experiment_config(config, agents_config)

        # 1. Look up noise floor
        nf = self._noise_floor_store.get(config_hash)
        if nf is None:
            raise RuntimeError(
                f"No noise floor found for config_hash={config_hash[:12]}... "
                "Run variance probing first:\n"
                f"  python -m scripts.probe_noise "
                f"--branch {config.branch_name} "
                f"--start-date {config.start_date} "
                f"--end-date {config.end_date} "
                f"--top-n {config.top_n}"
            )

        # 2. Compute staleness
        now = datetime.now()
        age = now - nf.last_updated_at
        age_days = age.days
        stale = age_days > _STALE_THRESHOLD_DAYS

        # 3. Run baseline and treatment
        baseline_run = await self._run_backtest(config, baseline_skills_bundle)
        treatment_run = await self._run_backtest(config, treatment_skills_bundle)

        # 4. Save both runs
        save_run(baseline_run, self._result_store_path)
        save_run(treatment_run, self._result_store_path)

        # 5. Compare
        comparison = compare_runs(baseline_run, treatment_run)

        # 6. Check bundle mismatch
        bundle_mismatch = nf.skill_bundle_hash != baseline_run.skill_bundle_hash

        # 7. Compute verdicts
        verdicts = compute_verdicts(
            comparison,
            nf,
            use_t_correction=use_t_correction,
        )

        return ExperimentResult(
            baseline_run_id=baseline_run.run_id,
            treatment_run_id=treatment_run.run_id,
            noise_floor=nf,
            noise_floor_age_days=age_days,
            noise_floor_stale=stale,
            bundle_mismatch_warning=bundle_mismatch,
            comparison=comparison,
            verdicts=verdicts,
            t_correction_used=use_t_correction,
        )


# ── Experiment result persistence ────────────────────────────────────────


def save_experiment_result(
    result: ExperimentResult,
    experiments_dir: Path = Path("data/experiments"),
) -> Path:
    """Persist an ExperimentResult as a write-once JSON file.

    File name: <baseline_run_id>_vs_<treatment_run_id>.json
    Creates experiments_dir if it doesn't exist.
    """
    experiments_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{result.baseline_run_id}_vs_{result.treatment_run_id}.json"
    path = experiments_dir / filename
    path.write_text(
        json.dumps(result.to_json_dict(), indent=2),
        encoding="utf-8",
    )
    return path

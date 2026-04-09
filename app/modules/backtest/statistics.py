"""Phase 3 statistics: noise floor computation, verdict labelling, cost estimation.

Pure functions only — no I/O, no argparse, no filesystem access. Operates on
already-loaded BacktestRun instances and in-memory dataclasses. The CLI scripts
(probe_noise.py, run_experiment.py) and the experiment harness (experiment.py)
call into these functions for all statistical computations.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import mean as _mean

from app.modules.backtest.config import BacktestConfig
from app.modules.equities.config import AgentsConfig

# ── Config hashing ────────────────────────────────────────────────────────


def hash_experiment_config(config: BacktestConfig, agents_config: AgentsConfig) -> str:
    """Compute a deterministic sha256 hash of the experiment-relevant config fields.

    Deliberately excludes:
      - skills_bundle: noise floor is reusable across prompt variants
      - use_llm_response_cache / llm_response_cache_path: caching is an
        implementation detail, not an experimental parameter
      - benchmark_symbols: benchmarks are post-hoc computations that don't
        affect portfolio metrics or LLM calls

    Includes per-analyst model and temperature from agents_config because
    different models have different noise characteristics.
    """
    hasher = hashlib.sha256()
    parts = [
        str(config.start_date),
        str(config.end_date),
        str(config.top_n),
        config.branch_name,
        str(config.rebalance_frequency),
        str(config.initial_capital),
        str(config.slippage_bps),
        str(config.commission_per_trade),
        agents_config.news_analyst.model,
        str(agents_config.news_analyst.temperature),
        agents_config.fundamentals_analyst.model,
        str(agents_config.fundamentals_analyst.temperature),
        agents_config.technical_analyst.model,
        str(agents_config.technical_analyst.temperature),
    ]
    for part in parts:
        hasher.update(part.encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


# ── Noise floor dataclasses ───────────────────────────────────────────────


@dataclass
class MetricNoiseFloor:
    """Statistics for a single metric across N variance-probe runs."""

    metric_name: str
    mean: float
    stddev: float
    n: int
    sample_values: list[float]


@dataclass
class NoiseFloor:
    """Aggregated noise floor across all metrics for a given experiment config."""

    config_hash: str
    config_label: str
    skill_bundle_hash: str
    n_runs: int
    created_at: datetime
    last_updated_at: datetime
    metrics: dict[str, MetricNoiseFloor]
    sample_run_ids: list[str]


# Metrics that get verdicts. Other metrics appear in the comparison table
# but are not labelled. This list drives both compute_noise_floor (which
# metrics to extract from probe runs) and compute_verdicts (which deltas
# get sigma labels).
_VERDICT_METRICS = (
    "total_return",
    "annualized_return",
    "sharpe_ratio",
    "sortino_ratio",
    "max_drawdown",
    "win_rate",
)

# Benchmark metrics that get verdicts (prefixed by benchmark symbol at runtime).
_VERDICT_BENCHMARK_METRICS = ("alpha",)


# ── Noise floor computation ──────────────────────────────────────────────


def compute_metric_stats(values: list[float], name: str) -> MetricNoiseFloor:
    """Compute mean and sample stddev for a single metric across probe runs.

    Uses N-1 denominator (sample stddev) for N >= 2, and 0.0 for N == 1.
    Raises ValueError if values is empty.
    """
    if not values:
        raise ValueError(f"compute_metric_stats requires at least one value for '{name}'")
    n = len(values)
    mu = _mean(values)
    if n < 2:
        sd = 0.0
    else:
        variance = sum((x - mu) ** 2 for x in values) / (n - 1)
        sd = math.sqrt(variance)
    return MetricNoiseFloor(
        metric_name=name,
        mean=mu,
        stddev=sd,
        n=n,
        sample_values=list(values),
    )


def compute_noise_floor(
    probe_runs: list,
    config_hash: str,
    skill_bundle_hash: str,
    config_label: str = "",
) -> NoiseFloor:
    """Aggregate metrics across N probe runs into a NoiseFloor.

    Extracts each curated metric from every run's PerformanceMetrics and
    computes per-metric mean + stddev. Also extracts benchmark-level alpha
    for any benchmarks present in all runs.

    Raises ValueError if probe_runs is empty or any run has metrics=None.
    """
    if not probe_runs:
        raise ValueError("compute_noise_floor requires at least one probe run")
    for run in probe_runs:
        if run.metrics is None:
            raise ValueError(
                f"Run {run.run_id} has metrics=None — cannot compute noise floor from a run with no performance metrics"
            )

    now = datetime.now()
    metrics: dict[str, MetricNoiseFloor] = {}

    # Core performance metrics
    for metric_name in _VERDICT_METRICS:
        values = [float(getattr(run.metrics, metric_name)) for run in probe_runs]
        metrics[metric_name] = compute_metric_stats(values, metric_name)

    # Benchmark metrics (alpha) — only for benchmarks present in all runs
    all_benchmark_symbols: set[str] = set()
    for run in probe_runs:
        all_benchmark_symbols.update(bc.benchmark_symbol for bc in run.benchmarks)
    for symbol in sorted(all_benchmark_symbols):
        runs_with_symbol = [run for run in probe_runs if any(bc.benchmark_symbol == symbol for bc in run.benchmarks)]
        if len(runs_with_symbol) != len(probe_runs):
            continue
        for bm_metric in _VERDICT_BENCHMARK_METRICS:
            full_name = f"{symbol}.{bm_metric}"
            values = []
            for run in probe_runs:
                bc = next(b for b in run.benchmarks if b.benchmark_symbol == symbol)
                values.append(float(getattr(bc, bm_metric)))
            metrics[full_name] = compute_metric_stats(values, full_name)

    return NoiseFloor(
        config_hash=config_hash,
        config_label=config_label,
        skill_bundle_hash=skill_bundle_hash,
        n_runs=len(probe_runs),
        created_at=now,
        last_updated_at=now,
        metrics=metrics,
        sample_run_ids=[run.run_id for run in probe_runs],
    )


# ── Verdict computation ──────────────────────────────────────────────────


@dataclass
class Verdict:
    """A single metric's delta with noise-floor context and a human-readable label."""

    metric_name: str
    baseline: float
    treatment: float
    delta: float
    sigma: float | None
    label: str  # "LIKELY SIGNAL" | "POSSIBLE SIGNAL" | "WITHIN NOISE"


# Two-tailed t critical values. Keys are degrees of freedom (N-1).
# alpha=0.05 for LIKELY threshold, alpha≈0.3173 (1σ equivalent) for POSSIBLE.
# Beyond df=29, t ≈ z and we fall back to fixed 2.0/1.0 thresholds.
_T_CRIT_005: dict[int, float] = {
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
}
_T_CRIT_032: dict[int, float] = {
    2: 1.386,
    3: 1.250,
    4: 1.190,
    5: 1.156,
    6: 1.134,
    7: 1.119,
    8: 1.108,
    9: 1.100,
    10: 1.093,
    11: 1.088,
    12: 1.083,
    13: 1.079,
    14: 1.076,
    15: 1.074,
    16: 1.071,
    17: 1.069,
    18: 1.067,
    19: 1.066,
    20: 1.064,
    21: 1.063,
    22: 1.061,
    23: 1.060,
    24: 1.059,
    25: 1.058,
    26: 1.058,
    27: 1.057,
    28: 1.056,
    29: 1.055,
}

_LIKELY_THRESHOLD = 2.0
_POSSIBLE_THRESHOLD = 1.0


def _get_thresholds(n_runs: int, use_t_correction: bool) -> tuple[float, float]:
    """Return (likely_threshold, possible_threshold) sigma cutoffs."""
    if not use_t_correction:
        return _LIKELY_THRESHOLD, _POSSIBLE_THRESHOLD
    df = n_runs - 1
    if df > 29:
        return _LIKELY_THRESHOLD, _POSSIBLE_THRESHOLD
    likely = _T_CRIT_005.get(df, _LIKELY_THRESHOLD)
    possible = _T_CRIT_032.get(df, _POSSIBLE_THRESHOLD)
    return likely, possible


def compute_verdicts(
    cmp,  # RunComparison
    nf: NoiseFloor,
    *,
    use_t_correction: bool = False,
) -> list[Verdict]:
    """Compute per-metric verdicts by comparing observed deltas against the noise floor.

    For each metric delta in the comparison that also has a noise floor entry,
    compute sigma = |delta| / stddev and assign a label based on the threshold.

    Raises ValueError if any verdict metric has stddev == 0 and the delta is
    non-zero (probe likely hit cache and didn't actually vary).
    """
    likely_thresh, possible_thresh = _get_thresholds(nf.n_runs, use_t_correction)

    verdicts: list[Verdict] = []
    for md in cmp.metric_deltas:
        if md.name not in nf.metrics:
            continue
        mnf = nf.metrics[md.name]
        if mnf.stddev == 0.0:
            if md.delta != 0.0:
                raise ValueError(
                    f"ZERO NOISE — metric '{md.name}' has stddev=0 across {mnf.n} "
                    "probe runs but the baseline/treatment delta is non-zero. "
                    "The probe likely hit the LLM response cache instead of making "
                    "fresh API calls. Re-run probe_noise with use_llm_response_cache=False."
                )
            sigma = 0.0
        else:
            sigma = abs(md.delta) / mnf.stddev

        if sigma > likely_thresh:
            label = "LIKELY SIGNAL"
        elif sigma > possible_thresh:
            label = "POSSIBLE SIGNAL"
        else:
            label = "WITHIN NOISE"

        verdicts.append(
            Verdict(
                metric_name=md.name,
                baseline=md.baseline,
                treatment=md.treatment,
                delta=md.delta,
                sigma=sigma,
                label=label,
            )
        )
    return verdicts


def format_verdict_table(
    verdicts: list[Verdict],
    n_runs: int,
    *,
    t_correction: bool = False,
) -> str:
    """Format verdicts as a human-readable text table."""
    lines: list[str] = []
    lines.append("")
    lines.append("--- Metric Verdicts ---")
    if t_correction:
        lines.append(f"  Thresholds: t-corrected (df={n_runs - 1}, N={n_runs})")
    else:
        lines.append("  Thresholds: 1\u03c3/2\u03c3 (fixed)")
    lines.append("")
    lines.append(f"  {'Metric':<24}  {'Baseline':>10}  {'Treatment':>10}  {'Delta':>10}  {'Sigma':>8}  Verdict")
    lines.append("  " + "\u2500" * 88)

    for v in verdicts:
        sigma_str = f"{v.sigma:+.1f}\u03c3" if v.sigma is not None else "N/A"
        lines.append(
            f"  {v.metric_name:<24}  {v.baseline:>10.4f}  {v.treatment:>10.4f}  "
            f"{v.delta:>+10.4f}  {sigma_str:>8}  {v.label}"
        )

    if n_runs < 5:
        lines.append("")
        lines.append(f"  \u26a0 Noise floor based on N={n_runs} runs \u2014 verdicts have wide confidence intervals.")

    return "\n".join(lines)


# ── Cost estimation ──────────────────────────────────────────────────────

# Per-call cost estimates by model. Based on §10 cost analysis:
# ~3K input tokens × $/1M rate + ~200 output tokens × $/1M rate.
_COST_PER_CALL_BY_MODEL: dict[str, float] = {
    "claude-sonnet-4-6": 0.012,
    "claude-haiku-4-5-20251001": 0.002,
    "claude-opus-4-6": 0.06,
}
_DEFAULT_COST_PER_CALL = 0.012

_ANALYST_TYPES = ("news_analyst", "fundamentals_analyst", "technical_analyst")


def _count_rebalance_days(
    start_date: date,
    end_date: date,
    frequency: str,
) -> int:
    """Count the number of rebalance days in a date range.

    Approximates BacktestEngine's rebalance schedule. Counts the first
    trading day on or after each period boundary within the date range.
    """
    if frequency == "daily":
        count = 0
        d = start_date
        while d <= end_date:
            if d.weekday() < 5:
                count += 1
            d += timedelta(days=1)
        return count

    period_days = {"weekly": 7, "biweekly": 14, "monthly": 30}
    step = period_days.get(frequency, 7)
    count = 0
    d = start_date
    while d <= end_date:
        if d.weekday() < 5:
            count += 1
        d += timedelta(days=step)
    return max(count, 1)


@dataclass
class CostEstimate:
    """Estimated cost for a probe or experiment run."""

    n_runs: int
    top_n: int
    rebalance_count: int
    calls_per_run: int
    total_calls: int
    total_cost: float
    per_analyst_costs: list[dict]

    def format(self) -> str:
        lines = [
            f"Estimated cost: ~${self.total_cost:.0f} ({self.n_runs} runs \u00d7 {self.calls_per_run:,} calls/run)",
        ]
        for ac in self.per_analyst_costs:
            model_note = ""
            if ac.get("unknown_model"):
                model_note = f" (unknown model \u2014 using default ${_DEFAULT_COST_PER_CALL}/call)"
            lines.append(f"  {ac['analyst']:<24} {ac['model']:<30} \u00d7 ${ac['cost_per_call']:.3f}/call{model_note}")
        return "\n".join(lines)


def estimate_experiment_cost(
    config: BacktestConfig,
    agents_config: AgentsConfig,
    n_runs: int,
) -> CostEstimate:
    """Estimate the dollar cost of running N backtests.

    Uses per-analyst model to look up cost_per_call, then multiplies by
    top_n × rebalance_count × n_runs.
    """
    top_n = config.top_n or 200  # conservative default if top_n is None
    rebalance_count = _count_rebalance_days(
        config.start_date,
        config.end_date,
        str(config.rebalance_frequency),
    )

    per_analyst: list[dict] = []
    cost_per_stock_per_rebalance = 0.0
    for analyst_name in _ANALYST_TYPES:
        analyst_cfg = getattr(agents_config, analyst_name)
        cost = _COST_PER_CALL_BY_MODEL.get(analyst_cfg.model, _DEFAULT_COST_PER_CALL)
        unknown = analyst_cfg.model not in _COST_PER_CALL_BY_MODEL
        per_analyst.append(
            {
                "analyst": analyst_name,
                "model": analyst_cfg.model,
                "cost_per_call": cost,
                "unknown_model": unknown,
            }
        )
        cost_per_stock_per_rebalance += cost

    calls_per_run = top_n * rebalance_count * len(_ANALYST_TYPES)
    total_calls = calls_per_run * n_runs
    total_cost = top_n * rebalance_count * cost_per_stock_per_rebalance * n_runs

    return CostEstimate(
        n_runs=n_runs,
        top_n=top_n,
        rebalance_count=rebalance_count,
        calls_per_run=calls_per_run,
        total_calls=total_calls,
        total_cost=total_cost,
        per_analyst_costs=per_analyst,
    )

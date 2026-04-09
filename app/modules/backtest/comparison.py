"""Phase 2 run comparison: pure logic for diffing two saved BacktestRun objects.

This module contains only @dataclass containers and pure functions. It has no
I/O, no argparse, no filesystem access — every function operates on already-
loaded BacktestRun instances. The scripts/ wrappers (compare_runs.py,
inspect_run.py) are responsible for loading, flag parsing, and output dispatch.

The design deliberately does NOT include noise-floor verdicts or statistical
significance testing; those are Phase 3. The metric table formatter prints a
loud banner making this limitation explicit so readers don't mistake raw
deltas for tested effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Literal

from app.modules.backtest.result_store import BacktestRun

if TYPE_CHECKING:
    from app.modules.backtest.result_store import StockSignalRecord


@dataclass
class MetricDelta:
    """A single metric's raw delta between baseline and treatment.

    `delta = treatment - baseline`. No significance labelling — this is purely
    a numeric difference. Phase 3 will add verdict fields on top.
    """

    name: str
    baseline: float
    treatment: float
    delta: float


@dataclass
class SignalDivergence:
    """One (date, symbol, analyst_type) cell where baseline and treatment disagree.

    `score_delta = treatment_score - baseline_score`. The sort key is `impact`,
    defined as `|score_delta| * max(baseline_confidence, treatment_confidence)`
    for score-divergent rows, or `|conf_delta| * score` for conviction-only shifts
    (which appear in a separate list, not the main divergences).

    `baseline_summary` and `treatment_summary` are the verbatim analyst summaries
    from each run so the drilldown can show what the prompts actually said.
    """

    date: date
    symbol: str
    analyst_type: str
    baseline_score: int
    treatment_score: int
    score_delta: int
    baseline_confidence: int
    treatment_confidence: int
    impact: int
    baseline_summary: str
    treatment_summary: str


@dataclass
class UniverseDriftCell:
    """A (date, symbol, analyst_type) cell present in one run but not the other.

    `present_in` is "baseline" or "treatment". Drift cells are excluded from
    SignalDivergence computation because they're structurally missing, not
    semantically disagreeing — counting them as divergences would attribute
    universe-coverage changes to prompt effects.
    """

    date: date
    symbol: str
    analyst_type: str
    present_in: Literal["baseline", "treatment"]


@dataclass
class RunComparison:
    """Result of comparing two BacktestRun instances.

    Contains in-memory references to both full runs (formatters need them) plus
    the computed deltas, divergences, drift, and warnings. Use `to_json_dict()`
    to produce the curated Phase 2 JSON schema (which does NOT duplicate the
    full runs — consumers can re-load them by run_id if needed).
    """

    baseline: BacktestRun
    treatment: BacktestRun
    metric_deltas: list[MetricDelta] = field(default_factory=list)
    signal_divergences: list[SignalDivergence] = field(default_factory=list)
    conviction_shifts: list[SignalDivergence] = field(default_factory=list)
    compatibility_warnings: list[str] = field(default_factory=list)
    universe_drift_cells: list[UniverseDriftCell] = field(default_factory=list)
    high_drift_warning: str | None = None

    def to_json_dict(self) -> dict:
        """Return the curated Phase 2 JSON schema for this comparison.

        Does NOT include the full BacktestRun objects — consumers who need them
        can reload via result_store.load_run using baseline_run_id /
        treatment_run_id.
        """
        return _run_comparison_to_json_dict(self)


# ── Task 4: Compatibility warnings ──────────────────────────────────────────

_ANALYST_ATTRS = ("news_analyst", "fundamentals_analyst", "technical_analyst")


def _compatibility_warnings(baseline: BacktestRun, treatment: BacktestRun) -> list[str]:
    """Compare the compatibility-relevant fields of two runs and return warnings.

    A run is fully compatible with another when all these fields match:
      - start_date, end_date, top_n, rebalance_frequency, branch_name
      - llm_config.max_llm_calls_per_rebalance
      - per-analyst model and temperature (via effective_agents_config)

    Mismatches produce human-readable warnings — they are not errors. The
    comparison still runs with mismatched configs; the warnings just tell the
    reader the metric deltas may reflect non-prompt differences.

    Separately:
      - git_sha mismatch emits a "non-prompt code may have changed" warning.
      - Identical skill_bundle_hash emits a "nothing to attribute" warning.
      - Missing effective_agents_config on either side emits a single
        "cannot verify model/temperature" warning.
    """
    warnings: list[str] = []
    b_cfg = baseline.config
    t_cfg = treatment.config

    if b_cfg.start_date != t_cfg.start_date:
        warnings.append(
            f"config.start_date differs: baseline={b_cfg.start_date}, treatment={t_cfg.start_date}"
        )
    if b_cfg.end_date != t_cfg.end_date:
        warnings.append(
            f"config.end_date differs: baseline={b_cfg.end_date}, treatment={t_cfg.end_date}"
        )
    if b_cfg.top_n != t_cfg.top_n:
        warnings.append(
            f"config.top_n differs: baseline={b_cfg.top_n}, treatment={t_cfg.top_n}"
        )
    if b_cfg.rebalance_frequency != t_cfg.rebalance_frequency:
        warnings.append(
            f"config.rebalance_frequency differs: "
            f"baseline={b_cfg.rebalance_frequency}, treatment={t_cfg.rebalance_frequency}"
        )
    if b_cfg.branch_name != t_cfg.branch_name:
        warnings.append(
            f"config.branch_name differs: baseline={b_cfg.branch_name}, treatment={t_cfg.branch_name}"
        )
    if b_cfg.llm_config.max_llm_calls_per_rebalance != t_cfg.llm_config.max_llm_calls_per_rebalance:
        warnings.append(
            f"config.llm_config.max_llm_calls_per_rebalance differs: "
            f"baseline={b_cfg.llm_config.max_llm_calls_per_rebalance}, "
            f"treatment={t_cfg.llm_config.max_llm_calls_per_rebalance}"
        )

    if baseline.git_sha != treatment.git_sha:
        warnings.append(
            f"git_sha differs (baseline={baseline.git_sha[:12]}, "
            f"treatment={treatment.git_sha[:12]}) — non-prompt code may have changed"
        )

    if baseline.skill_bundle_hash == treatment.skill_bundle_hash:
        warnings.append(
            "skill_bundle_hash identical between runs — nothing to attribute "
            "(both runs used the same prompts)"
        )

    # Per-analyst model/temperature check requires effective_agents_config on both sides.
    if baseline.effective_agents_config is None or treatment.effective_agents_config is None:
        warnings.append(
            "effective_agents_config missing on baseline or treatment — "
            "cannot verify model/temperature compatibility (legacy run)"
        )
    else:
        for attr in _ANALYST_ATTRS:
            b_a = getattr(baseline.effective_agents_config, attr)
            t_a = getattr(treatment.effective_agents_config, attr)
            if b_a.model != t_a.model:
                warnings.append(
                    f"{attr}.model differs: baseline={b_a.model}, treatment={t_a.model}"
                )
            if b_a.temperature != t_a.temperature:
                warnings.append(
                    f"{attr}.temperature differs: baseline={b_a.temperature}, "
                    f"treatment={t_a.temperature}"
                )

    return warnings


# ── Task 5: Metric delta computation ────────────────────────────────────────

# Ordered list of PerformanceMetrics fields to include in the metric table.
# Groups: returns -> risk -> trading activity -> exposure. Skip `warnings` (list field).
_METRIC_DISPLAY_ORDER = (
    "total_return",
    "annualized_return",
    "volatility",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "max_drawdown",
    "max_drawdown_duration_days",
    "value_at_risk_95",
    "conditional_var_95",
    "ulcer_index",
    "total_trades",
    "win_rate",
    "profit_factor",
    "avg_win",
    "avg_loss",
    "turnover_rate",
    "avg_position_count",
    "max_position_count",
    "avg_long_exposure",
)

# BenchmarkComparison fields to include (prefixed by benchmark symbol in the output).
_BENCHMARK_FIELDS = (
    "benchmark_total_return",
    "benchmark_annualized_return",
    "benchmark_sharpe",
    "benchmark_max_drawdown",
    "alpha",
    "beta",
    "information_ratio",
    "tracking_error",
    "up_capture_ratio",
    "down_capture_ratio",
)


def _metric_deltas(baseline: BacktestRun, treatment: BacktestRun) -> list[MetricDelta]:
    """Return a list of MetricDelta objects for every comparable metric.

    Covers PerformanceMetrics (skipped entirely if either run has metrics=None)
    and per-benchmark BenchmarkComparison fields (only for benchmarks present in
    both runs, joined by symbol).
    """
    deltas: list[MetricDelta] = []

    if baseline.metrics is not None and treatment.metrics is not None:
        for name in _METRIC_DISPLAY_ORDER:
            b_val = float(getattr(baseline.metrics, name))
            t_val = float(getattr(treatment.metrics, name))
            deltas.append(
                MetricDelta(
                    name=name,
                    baseline=b_val,
                    treatment=t_val,
                    delta=t_val - b_val,
                )
            )

    baseline_benchmarks = {bc.benchmark_symbol: bc for bc in baseline.benchmarks}
    treatment_benchmarks = {bc.benchmark_symbol: bc for bc in treatment.benchmarks}
    shared_symbols = sorted(set(baseline_benchmarks) & set(treatment_benchmarks))
    for symbol in shared_symbols:
        b_bc = baseline_benchmarks[symbol]
        t_bc = treatment_benchmarks[symbol]
        for field_name in _BENCHMARK_FIELDS:
            b_val = float(getattr(b_bc, field_name))
            t_val = float(getattr(t_bc, field_name))
            deltas.append(
                MetricDelta(
                    name=f"{symbol}.{field_name}",
                    baseline=b_val,
                    treatment=t_val,
                    delta=t_val - b_val,
                )
            )

    return deltas


# ── Task 6: Signal divergence computation ────────────────────────────────────


def _index_signals_by_key(
    run: BacktestRun,
) -> dict[tuple[date, str, str], StockSignalRecord]:
    """Return a dict keyed by (date, symbol, analyst_type) for fast join."""
    return {(s.date, s.symbol, s.analyst_type): s for s in run.signals}


def _signal_divergences(
    baseline: BacktestRun,
    treatment: BacktestRun,
    *,
    min_confidence: int = 0,
) -> list[SignalDivergence]:
    """Return SignalDivergence rows for every (date, symbol, analyst_type) cell
    where both runs have a signal AND the bullish_score differs AND the max of
    the two confidences meets `min_confidence`.

    Sorted by impact descending.
    """
    b_by_key = _index_signals_by_key(baseline)
    t_by_key = _index_signals_by_key(treatment)
    shared_keys = set(b_by_key) & set(t_by_key)

    divergences: list[SignalDivergence] = []
    for key in shared_keys:
        b_sig = b_by_key[key]
        t_sig = t_by_key[key]
        score_delta = t_sig.bullish_score - b_sig.bullish_score
        if score_delta == 0:
            continue
        max_conf = max(b_sig.confidence, t_sig.confidence)
        if max_conf < min_confidence:
            continue
        impact = abs(score_delta) * max_conf
        divergences.append(
            SignalDivergence(
                date=key[0],
                symbol=key[1],
                analyst_type=key[2],
                baseline_score=b_sig.bullish_score,
                treatment_score=t_sig.bullish_score,
                score_delta=score_delta,
                baseline_confidence=b_sig.confidence,
                treatment_confidence=t_sig.confidence,
                impact=impact,
                baseline_summary=b_sig.summary,
                treatment_summary=t_sig.summary,
            )
        )

    divergences.sort(key=lambda d: (-d.impact, d.date, d.symbol, d.analyst_type))
    return divergences


# ── Task 7: Conviction shifts ───────────────────────────────────────────────


def _conviction_shifts(
    baseline: BacktestRun,
    treatment: BacktestRun,
) -> list[SignalDivergence]:
    """Return SignalDivergence rows where score_delta == 0 but confidence moved.

    Impact for conviction shifts is defined as |conf_delta| * score, where score
    is the (identical) bullish_score shared by both runs. Sorted by impact
    descending. Used only when include_conviction_shifts=True.
    """
    b_by_key = _index_signals_by_key(baseline)
    t_by_key = _index_signals_by_key(treatment)
    shared_keys = set(b_by_key) & set(t_by_key)

    shifts: list[SignalDivergence] = []
    for key in shared_keys:
        b_sig = b_by_key[key]
        t_sig = t_by_key[key]
        if t_sig.bullish_score != b_sig.bullish_score:
            continue
        conf_delta = t_sig.confidence - b_sig.confidence
        if conf_delta == 0:
            continue
        impact = abs(conf_delta) * b_sig.bullish_score
        shifts.append(
            SignalDivergence(
                date=key[0],
                symbol=key[1],
                analyst_type=key[2],
                baseline_score=b_sig.bullish_score,
                treatment_score=t_sig.bullish_score,
                score_delta=0,
                baseline_confidence=b_sig.confidence,
                treatment_confidence=t_sig.confidence,
                impact=impact,
                baseline_summary=b_sig.summary,
                treatment_summary=t_sig.summary,
            )
        )

    shifts.sort(key=lambda d: (-d.impact, d.date, d.symbol, d.analyst_type))
    return shifts


# ── Task 8: Universe drift computation ──────────────────────────────────────

_HIGH_DRIFT_THRESHOLD = 0.10  # strictly > 10% of total signals fires the warning


def _universe_drift_cells(
    baseline: BacktestRun,
    treatment: BacktestRun,
) -> list[UniverseDriftCell]:
    """Return UniverseDriftCell objects for every (date, symbol, analyst_type)
    present in exactly one of the two runs.

    Sorted by (date, symbol, analyst_type, present_in) for deterministic output.
    """
    b_keys = set(_index_signals_by_key(baseline))
    t_keys = set(_index_signals_by_key(treatment))
    baseline_only = b_keys - t_keys
    treatment_only = t_keys - b_keys

    cells: list[UniverseDriftCell] = []
    for key in baseline_only:
        cells.append(
            UniverseDriftCell(
                date=key[0],
                symbol=key[1],
                analyst_type=key[2],
                present_in="baseline",
            )
        )
    for key in treatment_only:
        cells.append(
            UniverseDriftCell(
                date=key[0],
                symbol=key[1],
                analyst_type=key[2],
                present_in="treatment",
            )
        )
    cells.sort(key=lambda c: (c.date, c.symbol, c.analyst_type, c.present_in))
    return cells


def _high_drift_warning(
    drift_cells: list[UniverseDriftCell],
    total_unique_signals: int,
) -> str | None:
    """Return a loud warning string when drift exceeds _HIGH_DRIFT_THRESHOLD of
    total unique signals. Total signals is the size of the union of both runs'
    signal keys (shared + baseline_only + treatment_only).
    """
    if total_unique_signals == 0:
        return None
    drift_pct = len(drift_cells) / total_unique_signals
    if drift_pct <= _HIGH_DRIFT_THRESHOLD:
        return None
    return (
        f"HIGH DRIFT — {len(drift_cells)} cells "
        f"({drift_pct * 100:.1f}% of total signals) present in only one run. "
        "Metric deltas may reflect universe change, not prompt change. "
        "Inspect with `compare_runs --show-drift`."
    )


def _total_unique_signal_keys(baseline: BacktestRun, treatment: BacktestRun) -> int:
    b_keys = set(_index_signals_by_key(baseline))
    t_keys = set(_index_signals_by_key(treatment))
    return len(b_keys | t_keys)


# ── compare_runs ─────────────────────────────────────────────────────────────


def compare_runs(
    baseline: BacktestRun,
    treatment: BacktestRun,
    *,
    min_confidence: int = 0,
    include_conviction_shifts: bool = False,
) -> RunComparison:
    """Compare two BacktestRun instances and return a RunComparison.

    Pure function: no I/O, no mutation of the inputs. Run this on already-loaded
    runs; use result_store.load_run() to hydrate runs from disk first.

    Args:
        baseline: reference run to diff against
        treatment: run being evaluated
        min_confidence: drop divergences where max(baseline_conf, treatment_conf)
            is below this threshold. Default 0 (no filter).
        include_conviction_shifts: when True, populate RunComparison.conviction_shifts
            with rows where score_delta == 0 but confidence moved, ranked by
            |conf_delta| * score.
    """
    drift_cells = _universe_drift_cells(baseline, treatment)
    total_signals = _total_unique_signal_keys(baseline, treatment)
    return RunComparison(
        baseline=baseline,
        treatment=treatment,
        compatibility_warnings=_compatibility_warnings(baseline, treatment),
        metric_deltas=_metric_deltas(baseline, treatment),
        signal_divergences=_signal_divergences(
            baseline, treatment, min_confidence=min_confidence
        ),
        conviction_shifts=(
            _conviction_shifts(baseline, treatment) if include_conviction_shifts else []
        ),
        universe_drift_cells=drift_cells,
        high_drift_warning=_high_drift_warning(drift_cells, total_signals),
    )


# ── Task 9: format_metric_table ─────────────────────────────────────────────

_METRIC_BANNER = (
    "═══════════════════════════════════════════════════════════════════\n"
    "  RAW METRIC DELTAS — NO NOISE FLOOR, NO SIGNIFICANCE TESTING\n"
    "  A small delta may be indistinguishable from run-to-run noise.\n"
    "  Phase 3 (not yet shipped) will add per-metric verdict labels.\n"
    "═══════════════════════════════════════════════════════════════════"
)


def _format_metric_row(delta: MetricDelta) -> str:
    """Format a single MetricDelta as a fixed-width row.

    Uses metric-appropriate precision: 4 decimals for return-like numbers and
    exposure fractions, 3 for ratios, 0 for integer counts.
    """
    integer_metrics = {
        "max_drawdown_duration_days",
        "total_trades",
        "max_position_count",
    }
    ratio_metrics = {
        "sharpe_ratio",
        "sortino_ratio",
        "calmar_ratio",
        "profit_factor",
        "avg_position_count",
        "turnover_rate",
    }
    if delta.name in integer_metrics:
        fmt = "{:>8.0f}"
    elif delta.name in ratio_metrics or delta.name.endswith(".beta"):
        fmt = "{:>8.3f}"
    else:
        fmt = "{:>8.4f}"

    baseline_str = fmt.format(delta.baseline)
    treatment_str = fmt.format(delta.treatment)
    delta_str = fmt.format(delta.delta)
    return f"  {delta.name:<32}  {baseline_str}   {treatment_str}   {delta_str}"


def format_metric_table(cmp: RunComparison) -> str:
    """Format cmp.metric_deltas as a human-readable text block with the loud
    'raw deltas, no noise floor' banner at the top and a ΔRAW column label.
    """
    lines = [_METRIC_BANNER, ""]
    lines.append(
        f"  {'Metric':<32}  {'Baseline':>8}   {'Treatment':>8}   {'ΔRAW':>8}"
    )
    lines.append("  " + "─" * 68)

    if not cmp.metric_deltas:
        lines.append("  (no metrics to compare — one or both runs had metrics=None)")
        return "\n".join(lines)

    for delta in cmp.metric_deltas:
        lines.append(_format_metric_row(delta))
    return "\n".join(lines)


# ── Task 10: format_signal_drilldown ────────────────────────────────────────


def _format_divergence_block(div: SignalDivergence, index: int) -> str:
    """Format a single SignalDivergence as a multi-line block."""
    return (
        f"  [{index}] {div.date} {div.symbol} {div.analyst_type}  "
        f"(impact={div.impact})\n"
        f"      Score: {div.baseline_score} → {div.treatment_score} "
        f"(Δ{div.score_delta:+d})    "
        f"Confidence: {div.baseline_confidence} → {div.treatment_confidence}\n"
        f"      Baseline: {div.baseline_summary}\n"
        f"      Treatment: {div.treatment_summary}"
    )


def format_signal_drilldown(cmp: RunComparison, top_n: int = 20) -> str:
    """Format the signal divergence drilldown with full verbatim summaries.

    Shows the first `top_n` score-divergent rows sorted by impact descending.
    When cmp.conviction_shifts is non-empty, renders a separate subsection for
    them (also truncated to top_n).
    """
    lines = ["", "──── SIGNAL DRILLDOWN ────────────────────────────────────────────", ""]

    if not cmp.signal_divergences and not cmp.conviction_shifts:
        lines.append("  (no signal divergences between the two runs)")
        return "\n".join(lines)

    if cmp.signal_divergences:
        total = len(cmp.signal_divergences)
        shown = min(top_n, total)
        if total > top_n:
            lines.append(f"  Showing top {shown} of {total} divergences (ranked by impact):")
        else:
            lines.append(f"  {total} divergence(s) (ranked by impact):")
        lines.append("")
        for i, div in enumerate(cmp.signal_divergences[:top_n], start=1):
            lines.append(_format_divergence_block(div, i))
            lines.append("")
    else:
        lines.append("  (no score-divergent signals)")
        lines.append("")

    if cmp.conviction_shifts:
        total = len(cmp.conviction_shifts)
        shown = min(top_n, total)
        lines.append("──── CONVICTION SHIFTS (score unchanged, confidence moved) ────")
        lines.append("")
        if total > top_n:
            lines.append(f"  Showing top {shown} of {total} shifts (ranked by |Δconf| × score):")
        else:
            lines.append(f"  {total} conviction shift(s) (ranked by |Δconf| × score):")
        lines.append("")
        for i, div in enumerate(cmp.conviction_shifts[:top_n], start=1):
            lines.append(_format_divergence_block(div, i))
            lines.append("")

    return "\n".join(lines)


# ── Task 11: format_drift_section + to_json_dict ────────────────────────────


def format_drift_section(cmp: RunComparison, *, show_drift: bool = False) -> str:
    """Format the universe drift section.

    Default (show_drift=False): a one-line footer with count and percent. The
    percent is computed against the union of both runs' signal keys (same
    denominator as the high-drift threshold).

    show_drift=True: appends the full drift list, grouped by present_in side.
    """
    lines = ["", "──── UNIVERSE DRIFT ──────────────────────────────────────────────", ""]

    count = len(cmp.universe_drift_cells)
    total_signals = _total_unique_signal_keys(cmp.baseline, cmp.treatment)
    pct = (count / total_signals * 100) if total_signals > 0 else 0.0

    if count == 0:
        lines.append("  No drift — all signal keys present in both runs.")
        return "\n".join(lines)

    lines.append(f"  {count} drift cell(s) ({pct:.1f}% of total signals)")

    if not show_drift:
        lines.append("  (pass --show-drift to see the full list)")
        return "\n".join(lines)

    lines.append("")
    baseline_only = [c for c in cmp.universe_drift_cells if c.present_in == "baseline"]
    treatment_only = [c for c in cmp.universe_drift_cells if c.present_in == "treatment"]

    if baseline_only:
        lines.append(f"  Baseline-only ({len(baseline_only)}):")
        for cell in baseline_only:
            lines.append(f"    {cell.date} {cell.symbol} {cell.analyst_type}")
        lines.append("")
    if treatment_only:
        lines.append(f"  Treatment-only ({len(treatment_only)}):")
        for cell in treatment_only:
            lines.append(f"    {cell.date} {cell.symbol} {cell.analyst_type}")

    return "\n".join(lines)


def _run_comparison_to_json_dict(cmp: RunComparison) -> dict:
    """Implementation of RunComparison.to_json_dict — extracted so the method
    body stays declarative and a future refactor can move this anywhere.
    """
    total_signals = _total_unique_signal_keys(cmp.baseline, cmp.treatment)
    drift_pct = (
        (len(cmp.universe_drift_cells) / total_signals * 100) if total_signals > 0 else 0.0
    )
    return {
        "baseline_run_id": cmp.baseline.run_id,
        "treatment_run_id": cmp.treatment.run_id,
        "baseline_skill_bundle_hash": cmp.baseline.skill_bundle_hash,
        "treatment_skill_bundle_hash": cmp.treatment.skill_bundle_hash,
        "generated_at": datetime.now(UTC).isoformat(),
        "compatibility_warnings": list(cmp.compatibility_warnings),
        "metric_deltas": [
            {
                "name": d.name,
                "baseline": d.baseline,
                "treatment": d.treatment,
                "delta": d.delta,
            }
            for d in cmp.metric_deltas
        ],
        "signal_divergences": [
            {
                "date": d.date.isoformat(),
                "symbol": d.symbol,
                "analyst_type": d.analyst_type,
                "baseline_score": d.baseline_score,
                "treatment_score": d.treatment_score,
                "score_delta": d.score_delta,
                "baseline_confidence": d.baseline_confidence,
                "treatment_confidence": d.treatment_confidence,
                "impact": d.impact,
                "baseline_summary": d.baseline_summary,
                "treatment_summary": d.treatment_summary,
            }
            for d in cmp.signal_divergences
        ],
        "conviction_shifts": [
            {
                "date": d.date.isoformat(),
                "symbol": d.symbol,
                "analyst_type": d.analyst_type,
                "baseline_score": d.baseline_score,
                "treatment_score": d.treatment_score,
                "score_delta": d.score_delta,
                "baseline_confidence": d.baseline_confidence,
                "treatment_confidence": d.treatment_confidence,
                "impact": d.impact,
                "baseline_summary": d.baseline_summary,
                "treatment_summary": d.treatment_summary,
            }
            for d in cmp.conviction_shifts
        ],
        "universe_drift": {
            "count": len(cmp.universe_drift_cells),
            "percent_of_total_signals": round(drift_pct, 2),
            "cells": [
                {
                    "date": c.date.isoformat(),
                    "symbol": c.symbol,
                    "analyst_type": c.analyst_type,
                    "present_in": c.present_in,
                }
                for c in cmp.universe_drift_cells
            ],
        },
    }

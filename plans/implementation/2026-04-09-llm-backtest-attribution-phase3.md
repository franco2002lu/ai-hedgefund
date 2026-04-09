# LLM-Mode Backtest Attribution — Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After this phase, running `python -m scripts.run_experiment --preset medium --baseline-bundle X --treatment-bundle Y` produces a one-shot report telling you which metric shifts are real signal versus LLM noise, with per-metric sigma values and verdict labels (LIKELY SIGNAL / POSSIBLE SIGNAL / WITHIN NOISE).

**Architecture:** Add three new modules under `app/modules/backtest/`: `statistics.py` (pure functions for noise floor computation and verdict labelling), `noise_floor_store.py` (SQLite-backed store for cached noise floor estimates), and `experiment.py` (orchestration harness that ties together the backtest engine, comparison module, noise floor, and verdict logic). Extend `config.py` with `BacktestTier` presets and a `config_from_preset` helper. Add two CLI scripts: `scripts/probe_noise.py` (variance probing) and `scripts/run_experiment.py` (full experiment harness). All new code sits inside the existing `backtest` module — no new top-level packages.

**Tech Stack:** Python 3.12, SQLite (stdlib `sqlite3`), `dataclasses` (stdlib), Pydantic 2 (for `BacktestConfig` extension only), `math`/`statistics` (stdlib), pytest, asyncio, existing `result_store`/`comparison`/`config` from Phase 1/2.

**Spec:** See `plans/architecture/LLM-BACKTEST-ATTRIBUTION.md` §8 for the Phase 3 component specification, §9 for the full data flow, §10 for cost analysis, §11 for error handling, §12 (Phase 3 section) for the testing strategy. Phase 1 shipped in commit `30b431f`, Phase 2 shipped in commit `7977001`.

**Out of scope for this phase:** Multi-variant tournaments, continuous experimentation infrastructure, bootstrap confidence intervals, auto-cleanup of stale cache entries, cache sharing across machines. These are §13 future work.

**Key design decisions** (resolved in brainstorming before writing this plan):

1. **Probe count:** N=5 default, `--runs` flag with min=3, warn above 10, no hard cap. A footnote is printed in the verdict table when N < 5: `⚠ Noise floor based on N=M runs — verdicts have wide confidence intervals.`
2. **Verdict thresholds:** Fixed 1σ/2σ cutoffs by default (WITHIN NOISE ≤ 1.0σ, POSSIBLE SIGNAL 1.0–2.0σ, LIKELY SIGNAL > 2.0σ). Opt-in `--t-correction` flag on `run_experiment.py` widens bands using a hardcoded t-critical-value lookup table (no scipy dependency). Report header shows which mode was used.
3. **`hash_experiment_config` fields:** Include `start_date`, `end_date`, `top_n`, `branch_name`, `rebalance_frequency`, `initial_capital`, `slippage_bps`, `commission_per_trade`, per-analyst model, per-analyst temperature. Exclude `skills_bundle`, `use_llm_response_cache`, `llm_response_cache_path`, `benchmark_symbols`.
4. **Staleness policy:** Warn-and-proceed for both >30-day noise floors and bundle-hash mismatches. No hard blocks.
5. **Cost estimation:** Per-analyst model lookup table (`_COST_PER_CALL_BY_MODEL`) with a `_DEFAULT_COST_PER_CALL` fallback for unknown models. Output shows per-analyst model + cost breakdown before prompting for confirmation.
6. **Experiment result schema:** Curated JSON (not full dataclass dump). Verdicts as primary payload, metric_deltas and signal_divergences carried from `RunComparison`, runs referenced by ID, no noise floor sample values in the JSON.

---

## File Structure Overview

### New files

| Path | Responsibility |
|---|---|
| `app/modules/backtest/statistics.py` | `@dataclass` containers (`MetricNoiseFloor`, `NoiseFloor`, `Verdict`) + pure functions (`compute_metric_stats`, `compute_noise_floor`, `compute_verdicts`, `format_verdict_table`, `hash_experiment_config`, `estimate_experiment_cost`). No I/O, no argparse, no filesystem. |
| `app/modules/backtest/noise_floor_store.py` | SQLite-backed store for noise floor estimates. `NoiseFloorStore` class with `get/put/invalidate/list_all/close` methods. Single table keyed by `config_hash`. |
| `app/modules/backtest/experiment.py` | `ExperimentResult` dataclass + `ExperimentRunner` class (async orchestration) + `format_experiment_report` formatter + `save_experiment_result` persistence helper. |
| `scripts/probe_noise.py` | CLI for variance probing: build config from preset, estimate and display cost, run N backtests with cache disabled, compute and store noise floor. |
| `scripts/run_experiment.py` | CLI for full experiment harness: load noise floor, run baseline + treatment backtests, compute verdicts, print report, save result. |
| `tests/unit/backtest/test_statistics.py` | Unit tests for all `statistics.py` functions. |
| `tests/unit/backtest/test_noise_floor_store.py` | Unit tests for `NoiseFloorStore` CRUD operations. |
| `tests/unit/backtest/test_experiment.py` | Unit tests for `ExperimentRunner` orchestration and formatters (mocked engine, no real LLM calls). |
| `tests/unit/backtest/test_probe_noise_script.py` | Unit tests for `scripts/probe_noise.py` CLI: argument parsing, cost estimation display, confirmation prompt. |
| `tests/unit/backtest/test_run_experiment_script.py` | Unit tests for `scripts/run_experiment.py` CLI: argument parsing, report output, JSON persistence. |

### Modified files

| Path | Change |
|---|---|
| `app/modules/backtest/config.py` | Add `BacktestTier` enum, `TIER_PRESETS` dict, `config_from_preset` factory function. |
| `app/modules/backtest/comparison.py` | Update the `_METRIC_BANNER` text to remove the "Phase 3 (not yet shipped)" language and replace it with a reference to `run_experiment` for verdict-labelled output. |
| `plans/architecture/LLM-BACKTEST-ATTRIBUTION.md` | Add §8.8 Phase 3 Implementation Notes subsection documenting deltas from spec. |

---

## Task 1: Extend `config.py` with `BacktestTier`, `TIER_PRESETS`, and `config_from_preset`

**Files:**
- Modify: `app/modules/backtest/config.py`
- Test: `tests/unit/backtest/test_config.py`

### Step 1.1: Write the failing tests

- [ ] **Add to `tests/unit/backtest/test_config.py`**:

```python
"""Tests for BacktestTier, TIER_PRESETS, and config_from_preset."""
from datetime import date

import pytest

from app.modules.backtest.config import (
    BacktestTier,
    TIER_PRESETS,
    RebalanceFrequency,
    config_from_preset,
)


class TestBacktestTier:
    def test_quick_tier_exists(self) -> None:
        assert BacktestTier.QUICK == "quick"

    def test_medium_tier_exists(self) -> None:
        assert BacktestTier.MEDIUM == "medium"

    def test_full_tier_exists(self) -> None:
        assert BacktestTier.FULL == "full"


class TestTierPresets:
    def test_quick_preset_values(self) -> None:
        p = TIER_PRESETS[BacktestTier.QUICK]
        assert p["top_n"] == 20
        assert p["duration_days"] == 180
        assert p["rebalance_frequency"] == RebalanceFrequency.WEEKLY

    def test_medium_preset_values(self) -> None:
        p = TIER_PRESETS[BacktestTier.MEDIUM]
        assert p["top_n"] == 50
        assert p["duration_days"] == 365
        assert p["rebalance_frequency"] == RebalanceFrequency.WEEKLY

    def test_full_preset_values(self) -> None:
        p = TIER_PRESETS[BacktestTier.FULL]
        assert p["top_n"] == 100
        assert p["duration_days"] == 730
        assert p["rebalance_frequency"] == RebalanceFrequency.WEEKLY


class TestConfigFromPreset:
    def test_medium_preset_with_explicit_end_date(self) -> None:
        cfg = config_from_preset(
            BacktestTier.MEDIUM,
            branch_name="growth",
            end_date=date(2025, 12, 31),
        )
        assert cfg.branch_name == "growth"
        assert cfg.top_n == 50
        assert cfg.end_date == date(2025, 12, 31)
        # 365 days before end_date
        assert cfg.start_date == date(2024, 12, 31)
        assert cfg.rebalance_frequency == RebalanceFrequency.WEEKLY

    def test_quick_preset_computes_start_from_end(self) -> None:
        cfg = config_from_preset(
            BacktestTier.QUICK,
            branch_name="value",
            end_date=date(2025, 6, 30),
        )
        assert cfg.start_date == date(2025, 1, 1)  # 180 days before 2025-06-30
        assert cfg.top_n == 20

    def test_end_date_defaults_to_today_when_none(self) -> None:
        cfg = config_from_preset(BacktestTier.QUICK, branch_name="growth")
        assert cfg.end_date == date.today()

    def test_overrides_take_precedence(self) -> None:
        cfg = config_from_preset(
            BacktestTier.QUICK,
            branch_name="growth",
            end_date=date(2025, 12, 31),
            initial_capital=500_000.0,
            slippage_bps=5.0,
        )
        assert cfg.initial_capital == 500_000.0
        assert cfg.slippage_bps == 5.0
        # Preset values still apply for non-overridden fields
        assert cfg.top_n == 20

    def test_use_llm_agents_forced_true(self) -> None:
        cfg = config_from_preset(
            BacktestTier.MEDIUM,
            branch_name="growth",
            end_date=date(2025, 12, 31),
        )
        assert cfg.use_llm_agents is True
```

### Step 1.2: Run tests to verify failure

```bash
.venv/bin/pytest tests/unit/backtest/test_config.py -q -x 2>&1 | head -20
```

### Step 1.3: Implement the tier presets and factory

- [ ] **Add to `app/modules/backtest/config.py`** (after the existing imports, before `BacktestConfig`):

```python
class BacktestTier(StrEnum):
    QUICK = "quick"     # top 20, 6 months, weekly
    MEDIUM = "medium"   # top 50, 1 year, weekly
    FULL = "full"       # top 100, 2 years, weekly


TIER_PRESETS: dict[BacktestTier, dict] = {
    BacktestTier.QUICK:  {"top_n": 20,  "duration_days": 180, "rebalance_frequency": RebalanceFrequency.WEEKLY},
    BacktestTier.MEDIUM: {"top_n": 50,  "duration_days": 365, "rebalance_frequency": RebalanceFrequency.WEEKLY},
    BacktestTier.FULL:   {"top_n": 100, "duration_days": 730, "rebalance_frequency": RebalanceFrequency.WEEKLY},
}
```

- [ ] **Add after `BacktestConfig`** (needs access to `BacktestConfig`, `TIER_PRESETS`, `timedelta`):

```python
from datetime import timedelta


def config_from_preset(
    preset: BacktestTier,
    branch_name: str,
    end_date: date | None = None,
    **overrides,
) -> BacktestConfig:
    """Build a BacktestConfig from a tier preset.

    start_date is computed as end_date - duration_days from the preset.
    end_date defaults to today if not provided. use_llm_agents is forced
    True (tier presets exist only for LLM-mode experiments).

    Any additional keyword arguments are passed through to BacktestConfig
    as overrides (e.g., initial_capital, slippage_bps).
    """
    tier = TIER_PRESETS[preset]
    if end_date is None:
        end_date = date.today()
    start_date = end_date - timedelta(days=tier["duration_days"])
    return BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        top_n=tier["top_n"],
        rebalance_frequency=tier["rebalance_frequency"],
        branch_name=branch_name,
        use_llm_agents=True,
        **overrides,
    )
```

Note: the `timedelta` import should be added to the existing `from datetime import date` line at the top of the file.

### Step 1.4: Run tests to verify they pass

```bash
.venv/bin/pytest tests/unit/backtest/test_config.py -q
```

### Step 1.5: Run linter

```bash
.venv/bin/ruff check app/modules/backtest/config.py tests/unit/backtest/test_config.py
.venv/bin/ruff format app/modules/backtest/config.py tests/unit/backtest/test_config.py
```

### Step 1.6: Commit

```bash
git add app/modules/backtest/config.py tests/unit/backtest/test_config.py
git commit -m "$(cat <<'EOF'
Add BacktestTier presets and config_from_preset factory

Phase 3 tier presets (quick/medium/full) anchor on an explicit end_date
so config_hash stays stable across reruns. config_from_preset forces
use_llm_agents=True since tiers only apply to LLM-mode experiments.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Create `statistics.py` — `hash_experiment_config`

**Files:**
- Create: `app/modules/backtest/statistics.py`
- Test: `tests/unit/backtest/test_statistics.py`

This task establishes the module and implements only the config hashing function. Subsequent tasks add the remaining functions incrementally.

### Step 2.1: Write the failing test

- [ ] **Create `tests/unit/backtest/test_statistics.py`**:

```python
"""Unit tests for statistics.py — noise floor computation and verdict logic."""
from __future__ import annotations

from datetime import date

import pytest

from app.modules.backtest.statistics import hash_experiment_config
from tests.unit.backtest.conftest import _make_backtest_config, _make_agents_config


class TestHashExperimentConfig:
    def test_stable_across_calls(self) -> None:
        cfg = _make_backtest_config(use_llm_agents=True)
        agents = _make_agents_config()
        h1 = hash_experiment_config(cfg, agents)
        h2 = hash_experiment_config(cfg, agents)
        assert h1 == h2

    def test_different_start_date_different_hash(self) -> None:
        agents = _make_agents_config()
        cfg1 = _make_backtest_config(start_date=date(2024, 1, 2), use_llm_agents=True)
        cfg2 = _make_backtest_config(start_date=date(2024, 2, 1), use_llm_agents=True)
        assert hash_experiment_config(cfg1, agents) != hash_experiment_config(cfg2, agents)

    def test_different_skills_bundle_same_hash(self) -> None:
        """skills_bundle is excluded from the hash — noise floor is prompt-independent."""
        agents = _make_agents_config()
        cfg1 = _make_backtest_config(skills_bundle="baseline_v1", use_llm_agents=True)
        cfg2 = _make_backtest_config(skills_bundle="treatment_v2", use_llm_agents=True)
        assert hash_experiment_config(cfg1, agents) == hash_experiment_config(cfg2, agents)

    def test_different_llm_cache_setting_same_hash(self) -> None:
        """use_llm_response_cache is excluded from the hash."""
        agents = _make_agents_config()
        cfg1 = _make_backtest_config(use_llm_response_cache=True, use_llm_agents=True)
        cfg2 = _make_backtest_config(use_llm_response_cache=False, use_llm_agents=True)
        assert hash_experiment_config(cfg1, agents) == hash_experiment_config(cfg2, agents)

    def test_different_benchmark_symbols_same_hash(self) -> None:
        """benchmark_symbols is excluded — benchmarks are post-hoc computations."""
        agents = _make_agents_config()
        cfg1 = _make_backtest_config(benchmark_symbols=["SPY"], use_llm_agents=True)
        cfg2 = _make_backtest_config(benchmark_symbols=["QQQ", "SPY"], use_llm_agents=True)
        assert hash_experiment_config(cfg1, agents) == hash_experiment_config(cfg2, agents)

    def test_different_initial_capital_different_hash(self) -> None:
        agents = _make_agents_config()
        cfg1 = _make_backtest_config(initial_capital=1_000_000.0, use_llm_agents=True)
        cfg2 = _make_backtest_config(initial_capital=10_000.0, use_llm_agents=True)
        assert hash_experiment_config(cfg1, agents) != hash_experiment_config(cfg2, agents)

    def test_different_slippage_different_hash(self) -> None:
        agents = _make_agents_config()
        cfg1 = _make_backtest_config(slippage_bps=10.0, use_llm_agents=True)
        cfg2 = _make_backtest_config(slippage_bps=5.0, use_llm_agents=True)
        assert hash_experiment_config(cfg1, agents) != hash_experiment_config(cfg2, agents)

    def test_different_model_different_hash(self) -> None:
        from app.modules.equities.config import AgentsConfig, AnalystLLMConfig

        cfg = _make_backtest_config(use_llm_agents=True)
        agents1 = _make_agents_config(model="claude-sonnet-4-6")
        agents2 = AgentsConfig(
            news_analyst=AnalystLLMConfig(model="claude-opus-4-6", temperature=0.3),
            fundamentals_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
            technical_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
        )
        assert hash_experiment_config(cfg, agents1) != hash_experiment_config(cfg, agents2)

    def test_different_temperature_different_hash(self) -> None:
        cfg = _make_backtest_config(use_llm_agents=True)
        agents1 = _make_agents_config(temperature=0.3)
        agents2 = _make_agents_config(temperature=0.0)
        assert hash_experiment_config(cfg, agents1) != hash_experiment_config(cfg, agents2)

    def test_returns_hex_string(self) -> None:
        cfg = _make_backtest_config(use_llm_agents=True)
        agents = _make_agents_config()
        h = hash_experiment_config(cfg, agents)
        assert isinstance(h, str)
        assert len(h) == 64  # sha256 hex digest
        int(h, 16)  # valid hex
```

### Step 2.2: Run the test to verify failure

```bash
.venv/bin/pytest tests/unit/backtest/test_statistics.py::TestHashExperimentConfig -q -x 2>&1 | head -10
```

### Step 2.3: Create `statistics.py` with `hash_experiment_config`

- [ ] **Create `app/modules/backtest/statistics.py`**:

```python
"""Phase 3 statistics: noise floor computation, verdict labelling, cost estimation.

Pure functions only — no I/O, no argparse, no filesystem access. Operates on
already-loaded BacktestRun instances and in-memory dataclasses. The CLI scripts
(probe_noise.py, run_experiment.py) and the experiment harness (experiment.py)
call into these functions for all statistical computations.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.backtest.config import BacktestConfig
    from app.modules.equities.config import AgentsConfig


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
    # Config fields that affect noise magnitude
    parts = [
        str(config.start_date),
        str(config.end_date),
        str(config.top_n),
        config.branch_name,
        str(config.rebalance_frequency),
        str(config.initial_capital),
        str(config.slippage_bps),
        str(config.commission_per_trade),
        # Per-analyst model and temperature
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
```

### Step 2.4: Run the tests

```bash
.venv/bin/pytest tests/unit/backtest/test_statistics.py::TestHashExperimentConfig -q
```

### Step 2.5: Run linter

```bash
.venv/bin/ruff check app/modules/backtest/statistics.py tests/unit/backtest/test_statistics.py
.venv/bin/ruff format app/modules/backtest/statistics.py tests/unit/backtest/test_statistics.py
```

### Step 2.6: Commit

```bash
git add app/modules/backtest/statistics.py tests/unit/backtest/test_statistics.py
git commit -m "$(cat <<'EOF'
Add statistics.py with hash_experiment_config

Deterministic sha256 hash of experiment-relevant config fields. Excludes
skills_bundle (noise floor is prompt-independent), cache settings, and
benchmark_symbols. Includes per-analyst model/temperature since different
models have different noise characteristics.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add `compute_metric_stats` and `compute_noise_floor` to `statistics.py`

**Files:**
- Modify: `app/modules/backtest/statistics.py`
- Test: `tests/unit/backtest/test_statistics.py`

### Step 3.1: Write the failing tests

- [ ] **Add to `tests/unit/backtest/test_statistics.py`**:

```python
import math
from datetime import datetime

from app.modules.backtest.statistics import (
    MetricNoiseFloor,
    NoiseFloor,
    compute_metric_stats,
    compute_noise_floor,
)
from tests.unit.backtest.conftest import (
    _make_backtest_run,
    _make_performance_metrics,
)


class TestComputeMetricStats:
    def test_basic_mean_and_stddev(self) -> None:
        result = compute_metric_stats([10.0, 20.0, 30.0, 40.0, 50.0], "total_return")
        assert result.metric_name == "total_return"
        assert result.mean == 30.0
        assert result.n == 5
        assert result.sample_values == [10.0, 20.0, 30.0, 40.0, 50.0]
        # Sample stddev of [10,20,30,40,50] = sqrt(250) ≈ 15.81
        assert abs(result.stddev - math.sqrt(250.0)) < 0.01

    def test_single_element_stddev_is_zero(self) -> None:
        result = compute_metric_stats([42.0], "sharpe_ratio")
        assert result.mean == 42.0
        assert result.stddev == 0.0
        assert result.n == 1

    def test_two_elements(self) -> None:
        result = compute_metric_stats([10.0, 20.0], "volatility")
        assert result.n == 2
        assert result.mean == 15.0
        # Sample stddev of [10,20] with ddof=1 = sqrt(50) ≈ 7.07
        assert abs(result.stddev - math.sqrt(50.0)) < 0.01

    def test_identical_values_stddev_zero(self) -> None:
        result = compute_metric_stats([5.0, 5.0, 5.0], "max_drawdown")
        assert result.stddev == 0.0

    def test_empty_list_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            compute_metric_stats([], "total_return")


class TestComputeNoiseFloor:
    def _make_runs_with_varying_returns(self, returns: list[float]) -> list:
        """Create N BacktestRun objects with different total_return values."""
        runs = []
        for i, ret in enumerate(returns):
            runs.append(
                _make_backtest_run(
                    run_id=f"probe_{i}",
                    metrics=_make_performance_metrics(total_return=ret),
                )
            )
        return runs

    def test_basic_noise_floor(self) -> None:
        runs = self._make_runs_with_varying_returns([0.10, 0.12, 0.11, 0.13, 0.09])
        nf = compute_noise_floor(
            probe_runs=runs,
            config_hash="abc123",
            skill_bundle_hash="def456",
        )
        assert nf.config_hash == "abc123"
        assert nf.skill_bundle_hash == "def456"
        assert nf.n_runs == 5
        assert len(nf.sample_run_ids) == 5
        assert "total_return" in nf.metrics
        tr = nf.metrics["total_return"]
        assert tr.n == 5
        assert abs(tr.mean - 0.11) < 0.001

    def test_noise_floor_includes_curated_metrics(self) -> None:
        runs = self._make_runs_with_varying_returns([0.10, 0.12, 0.11, 0.13, 0.09])
        nf = compute_noise_floor(runs, "h", "s")
        # All curated verdict metrics should be present
        for name in ("total_return", "annualized_return", "sharpe_ratio",
                     "sortino_ratio", "max_drawdown", "win_rate"):
            assert name in nf.metrics, f"missing {name}"

    def test_noise_floor_timestamps_set(self) -> None:
        runs = self._make_runs_with_varying_returns([0.10, 0.12])
        nf = compute_noise_floor(runs, "h", "s")
        assert isinstance(nf.created_at, datetime)
        assert isinstance(nf.last_updated_at, datetime)
        assert nf.created_at == nf.last_updated_at

    def test_empty_runs_raises(self) -> None:
        with pytest.raises(ValueError, match="at least"):
            compute_noise_floor([], "h", "s")

    def test_runs_with_none_metrics_raises(self) -> None:
        runs = [_make_backtest_run(run_id="probe_0", metrics=None)]
        with pytest.raises(ValueError, match="metrics"):
            compute_noise_floor(runs, "h", "s")
```

### Step 3.2: Run the tests to verify failure

```bash
.venv/bin/pytest tests/unit/backtest/test_statistics.py -q -x -k "MetricStats or NoiseFloor" 2>&1 | head -10
```

### Step 3.3: Implement `compute_metric_stats` and `compute_noise_floor`

- [ ] **Add to `app/modules/backtest/statistics.py`**:

The `MetricNoiseFloor` and `NoiseFloor` dataclasses:

```python
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
```

The curated list of metrics that get noise floor entries (and later, verdicts):

```python
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
```

The computation functions:

```python
import math
from statistics import mean as _mean


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
                f"Run {run.run_id} has metrics=None — cannot compute noise floor "
                "from a run with no performance metrics"
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
        # Check that every run has this benchmark
        runs_with_symbol = [
            run for run in probe_runs
            if any(bc.benchmark_symbol == symbol for bc in run.benchmarks)
        ]
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
```

Note: the `from datetime import datetime` import already exists from the Task 2 `TYPE_CHECKING` block; move it to the runtime imports since `compute_noise_floor` now uses it at runtime.

### Step 3.4: Run the tests

```bash
.venv/bin/pytest tests/unit/backtest/test_statistics.py -q -x -k "MetricStats or NoiseFloor"
```

### Step 3.5: Run linter

```bash
.venv/bin/ruff check app/modules/backtest/statistics.py tests/unit/backtest/test_statistics.py
.venv/bin/ruff format app/modules/backtest/statistics.py tests/unit/backtest/test_statistics.py
```

### Step 3.6: Commit

```bash
git add app/modules/backtest/statistics.py tests/unit/backtest/test_statistics.py
git commit -m "$(cat <<'EOF'
Add compute_metric_stats and compute_noise_floor

Aggregates per-metric mean + sample stddev across N probe runs. Extracts
curated verdict metrics (total_return, sharpe, sortino, max_drawdown,
win_rate, alpha) and benchmark-level alpha for shared benchmarks.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add `compute_verdicts` and `format_verdict_table` to `statistics.py`

**Files:**
- Modify: `app/modules/backtest/statistics.py`
- Test: `tests/unit/backtest/test_statistics.py`

### Step 4.1: Write the failing tests

- [ ] **Add to `tests/unit/backtest/test_statistics.py`**:

```python
from app.modules.backtest.statistics import (
    Verdict,
    compute_verdicts,
    format_verdict_table,
)
from app.modules.backtest.comparison import compare_runs


class TestComputeVerdicts:
    def _make_noise_floor_with_stddev(self, stddev: float, mean: float = 0.10) -> NoiseFloor:
        """Build a NoiseFloor where every verdict metric has the same mean/stddev."""
        metrics = {}
        for name in ("total_return", "annualized_return", "sharpe_ratio",
                     "sortino_ratio", "max_drawdown", "win_rate"):
            metrics[name] = MetricNoiseFloor(
                metric_name=name, mean=mean, stddev=stddev, n=5,
                sample_values=[mean] * 5,
            )
        # Add a benchmark metric
        metrics["SPY.alpha"] = MetricNoiseFloor(
            metric_name="SPY.alpha", mean=0.02, stddev=stddev, n=5,
            sample_values=[0.02] * 5,
        )
        return NoiseFloor(
            config_hash="test", config_label="test", skill_bundle_hash="abc",
            n_runs=5, created_at=datetime.now(), last_updated_at=datetime.now(),
            metrics=metrics, sample_run_ids=[f"probe_{i}" for i in range(5)],
        )

    def test_within_noise_verdict(self) -> None:
        """Delta < 1 stddev → WITHIN NOISE."""
        baseline = _make_backtest_run(
            run_id="base",
            metrics=_make_performance_metrics(total_return=0.10),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="treat",
            metrics=_make_performance_metrics(total_return=0.105),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        nf = self._make_noise_floor_with_stddev(stddev=0.01)
        verdicts = compute_verdicts(cmp, nf)
        tr_verdict = next(v for v in verdicts if v.metric_name == "total_return")
        assert tr_verdict.label == "WITHIN NOISE"
        assert abs(tr_verdict.sigma - 0.5) < 0.01

    def test_possible_signal_verdict(self) -> None:
        """1 < sigma <= 2 → POSSIBLE SIGNAL."""
        baseline = _make_backtest_run(
            run_id="base",
            metrics=_make_performance_metrics(total_return=0.10),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="treat",
            metrics=_make_performance_metrics(total_return=0.115),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        nf = self._make_noise_floor_with_stddev(stddev=0.01)
        verdicts = compute_verdicts(cmp, nf)
        tr_verdict = next(v for v in verdicts if v.metric_name == "total_return")
        assert tr_verdict.label == "POSSIBLE SIGNAL"

    def test_likely_signal_verdict(self) -> None:
        """sigma > 2 → LIKELY SIGNAL."""
        baseline = _make_backtest_run(
            run_id="base",
            metrics=_make_performance_metrics(total_return=0.10),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="treat",
            metrics=_make_performance_metrics(total_return=0.13),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        nf = self._make_noise_floor_with_stddev(stddev=0.01)
        verdicts = compute_verdicts(cmp, nf)
        tr_verdict = next(v for v in verdicts if v.metric_name == "total_return")
        assert tr_verdict.label == "LIKELY SIGNAL"
        assert abs(tr_verdict.sigma - 3.0) < 0.01

    def test_zero_stddev_raises(self) -> None:
        """stddev == 0 → ValueError (cache likely hit, probe didn't vary)."""
        baseline = _make_backtest_run(run_id="base", skill_bundle_hash="a" * 64)
        treatment = _make_backtest_run(run_id="treat", skill_bundle_hash="b" * 64)
        cmp = compare_runs(baseline, treatment)
        nf = self._make_noise_floor_with_stddev(stddev=0.0)
        with pytest.raises(ValueError, match="ZERO NOISE"):
            compute_verdicts(cmp, nf)

    def test_delta_zero_within_noise(self) -> None:
        """delta == 0 → WITHIN NOISE with sigma == 0."""
        baseline = _make_backtest_run(
            run_id="base",
            metrics=_make_performance_metrics(total_return=0.10),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="treat",
            metrics=_make_performance_metrics(total_return=0.10),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        nf = self._make_noise_floor_with_stddev(stddev=0.01)
        verdicts = compute_verdicts(cmp, nf)
        tr_verdict = next(v for v in verdicts if v.metric_name == "total_return")
        assert tr_verdict.label == "WITHIN NOISE"
        assert tr_verdict.sigma == 0.0

    def test_t_correction_widens_thresholds(self) -> None:
        """With t-correction at N=5, the LIKELY threshold moves from 2.0 to ~2.78."""
        baseline = _make_backtest_run(
            run_id="base",
            metrics=_make_performance_metrics(total_return=0.10),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="treat",
            metrics=_make_performance_metrics(total_return=0.125),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        nf = self._make_noise_floor_with_stddev(stddev=0.01)
        # sigma = 2.5 → LIKELY with fixed thresholds
        verdicts_fixed = compute_verdicts(cmp, nf, use_t_correction=False)
        tr_fixed = next(v for v in verdicts_fixed if v.metric_name == "total_return")
        assert tr_fixed.label == "LIKELY SIGNAL"
        # sigma = 2.5 → POSSIBLE with t-correction (threshold at ~2.78 for N=5)
        verdicts_t = compute_verdicts(cmp, nf, use_t_correction=True)
        tr_t = next(v for v in verdicts_t if v.metric_name == "total_return")
        assert tr_t.label == "POSSIBLE SIGNAL"

    def test_metric_without_noise_floor_gets_no_verdict(self) -> None:
        """Metrics not in the noise floor are silently skipped."""
        baseline = _make_backtest_run(run_id="base", skill_bundle_hash="a" * 64)
        treatment = _make_backtest_run(run_id="treat", skill_bundle_hash="b" * 64)
        cmp = compare_runs(baseline, treatment)
        # Noise floor with only total_return
        nf = NoiseFloor(
            config_hash="test", config_label="test", skill_bundle_hash="abc",
            n_runs=5, created_at=datetime.now(), last_updated_at=datetime.now(),
            metrics={
                "total_return": MetricNoiseFloor(
                    metric_name="total_return", mean=0.10, stddev=0.01, n=5,
                    sample_values=[0.10] * 5,
                ),
            },
            sample_run_ids=[],
        )
        verdicts = compute_verdicts(cmp, nf)
        names = [v.metric_name for v in verdicts]
        assert "total_return" in names
        assert "annualized_return" not in names


class TestFormatVerdictTable:
    def test_basic_format(self) -> None:
        verdicts = [
            Verdict(
                metric_name="total_return",
                baseline=0.10, treatment=0.12, delta=0.02,
                sigma=2.5, label="LIKELY SIGNAL",
            ),
            Verdict(
                metric_name="sharpe_ratio",
                baseline=1.2, treatment=1.25, delta=0.05,
                sigma=0.8, label="WITHIN NOISE",
            ),
        ]
        output = format_verdict_table(verdicts, n_runs=5)
        assert "LIKELY SIGNAL" in output
        assert "WITHIN NOISE" in output
        assert "total_return" in output
        assert "sharpe_ratio" in output

    def test_low_sample_footnote_when_n_below_5(self) -> None:
        verdicts = [
            Verdict(
                metric_name="total_return",
                baseline=0.10, treatment=0.12, delta=0.02,
                sigma=2.5, label="LIKELY SIGNAL",
            ),
        ]
        output = format_verdict_table(verdicts, n_runs=3)
        assert "wide confidence intervals" in output.lower() or "N=3" in output

    def test_no_footnote_when_n_is_5(self) -> None:
        verdicts = [
            Verdict(
                metric_name="total_return",
                baseline=0.10, treatment=0.12, delta=0.02,
                sigma=2.5, label="LIKELY SIGNAL",
            ),
        ]
        output = format_verdict_table(verdicts, n_runs=5)
        assert "wide confidence intervals" not in output.lower()

    def test_t_correction_note_in_header(self) -> None:
        verdicts = [
            Verdict(
                metric_name="total_return",
                baseline=0.10, treatment=0.12, delta=0.02,
                sigma=2.5, label="LIKELY SIGNAL",
            ),
        ]
        output = format_verdict_table(verdicts, n_runs=5, t_correction=True)
        assert "t-corrected" in output.lower() or "t-correct" in output.lower()
```

### Step 4.2: Run the tests to verify failure

```bash
.venv/bin/pytest tests/unit/backtest/test_statistics.py -q -x -k "Verdict" 2>&1 | head -10
```

### Step 4.3: Implement `Verdict`, `compute_verdicts`, and `format_verdict_table`

- [ ] **Add to `app/modules/backtest/statistics.py`**:

The `Verdict` dataclass:

```python
@dataclass
class Verdict:
    """A single metric's delta with noise-floor context and a human-readable label."""

    metric_name: str
    baseline: float
    treatment: float
    delta: float
    sigma: float | None  # None if stddev is unavailable
    label: str  # "LIKELY SIGNAL" | "POSSIBLE SIGNAL" | "WITHIN NOISE"
```

The t-critical-value lookup table (hardcoded for df=2..29 at alpha=0.05 and alpha≈0.32):

```python
# Two-tailed t critical values. Keys are degrees of freedom (N-1).
# alpha=0.05 for LIKELY threshold, alpha≈0.3173 (1σ equivalent) for POSSIBLE.
# Beyond df=29, t ≈ z and we fall back to fixed 2.0/1.0 thresholds.
_T_CRIT_005: dict[int, float] = {
    2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160,
    14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
    20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045,
}
_T_CRIT_032: dict[int, float] = {
    2: 1.386, 3: 1.250, 4: 1.190, 5: 1.156, 6: 1.134, 7: 1.119,
    8: 1.108, 9: 1.100, 10: 1.093, 11: 1.088, 12: 1.083, 13: 1.079,
    14: 1.076, 15: 1.074, 16: 1.071, 17: 1.069, 18: 1.067, 19: 1.066,
    20: 1.064, 21: 1.063, 22: 1.061, 23: 1.060, 24: 1.059, 25: 1.058,
    26: 1.058, 27: 1.057, 28: 1.056, 29: 1.055,
}

# Fixed z thresholds (default, no t-correction)
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
```

The `compute_verdicts` function:

```python
def compute_verdicts(
    cmp,  # RunComparison
    nf: NoiseFloor,
    *,
    use_t_correction: bool = False,
) -> list[Verdict]:
    """Compute per-metric verdicts by comparing observed deltas against the noise floor.

    For each metric delta in the comparison that also has a noise floor entry,
    compute sigma = |delta| / stddev and assign a label based on the threshold.

    Raises ValueError if any verdict metric has stddev == 0 (probe likely hit
    cache and didn't actually vary).
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
```

The `format_verdict_table` function:

```python
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
    lines.append(
        f"  {'Metric':<24}  {'Baseline':>10}  {'Treatment':>10}  "
        f"{'Delta':>10}  {'Sigma':>8}  Verdict"
    )
    lines.append("  " + "\u2500" * 88)

    for v in verdicts:
        sigma_str = f"{v.sigma:+.1f}\u03c3" if v.sigma is not None else "N/A"
        lines.append(
            f"  {v.metric_name:<24}  {v.baseline:>10.4f}  {v.treatment:>10.4f}  "
            f"{v.delta:>+10.4f}  {sigma_str:>8}  {v.label}"
        )

    if n_runs < 5:
        lines.append("")
        lines.append(
            f"  \u26a0 Noise floor based on N={n_runs} runs "
            "\u2014 verdicts have wide confidence intervals."
        )

    return "\n".join(lines)
```

### Step 4.4: Run tests to verify they pass

```bash
.venv/bin/pytest tests/unit/backtest/test_statistics.py -q -x -k "Verdict"
```

### Step 4.5: Run linter

```bash
.venv/bin/ruff check app/modules/backtest/statistics.py tests/unit/backtest/test_statistics.py
.venv/bin/ruff format app/modules/backtest/statistics.py tests/unit/backtest/test_statistics.py
```

### Step 4.6: Commit

```bash
git add app/modules/backtest/statistics.py tests/unit/backtest/test_statistics.py
git commit -m "$(cat <<'EOF'
Add compute_verdicts and format_verdict_table with t-correction option

Per-metric verdicts (LIKELY SIGNAL / POSSIBLE SIGNAL / WITHIN NOISE)
using sigma thresholds. Default fixed 1σ/2σ cutoffs; opt-in t-correction
via hardcoded lookup table for N=3..30 (no scipy dependency). Low-sample
footnote when N < 5.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Add `estimate_experiment_cost` to `statistics.py`

**Files:**
- Modify: `app/modules/backtest/statistics.py`
- Test: `tests/unit/backtest/test_statistics.py`

### Step 5.1: Write the failing tests

- [ ] **Add to `tests/unit/backtest/test_statistics.py`**:

```python
from app.modules.backtest.statistics import estimate_experiment_cost, CostEstimate


class TestEstimateExperimentCost:
    def test_basic_cost_with_uniform_models(self) -> None:
        agents = _make_agents_config(model="claude-sonnet-4-6")
        cfg = _make_backtest_config(
            start_date=date(2025, 1, 2),
            end_date=date(2025, 6, 30),
            top_n=20,
            use_llm_agents=True,
        )
        est = estimate_experiment_cost(cfg, agents, n_runs=5)
        assert est.n_runs == 5
        assert est.top_n == 20
        assert est.total_calls > 0
        assert est.total_cost > 0
        assert len(est.per_analyst_costs) == 3
        # All analysts same model → same per-call cost
        costs = [c["cost_per_call"] for c in est.per_analyst_costs]
        assert len(set(costs)) == 1

    def test_mixed_model_cost(self) -> None:
        from app.modules.equities.config import AgentsConfig, AnalystLLMConfig

        agents = AgentsConfig(
            news_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
            fundamentals_analyst=AnalystLLMConfig(model="claude-opus-4-6", temperature=0.3),
            technical_analyst=AnalystLLMConfig(model="claude-haiku-4-5-20251001", temperature=0.3),
        )
        cfg = _make_backtest_config(
            start_date=date(2025, 1, 2),
            end_date=date(2025, 6, 30),
            top_n=20,
            use_llm_agents=True,
        )
        est = estimate_experiment_cost(cfg, agents, n_runs=5)
        # Different models → different per-call costs
        costs = {c["analyst"]: c["cost_per_call"] for c in est.per_analyst_costs}
        assert costs["fundamentals_analyst"] > costs["news_analyst"]
        assert costs["news_analyst"] > costs["technical_analyst"]

    def test_unknown_model_uses_default(self) -> None:
        from app.modules.equities.config import AgentsConfig, AnalystLLMConfig

        agents = AgentsConfig(
            news_analyst=AnalystLLMConfig(model="claude-future-9000", temperature=0.3),
            fundamentals_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
            technical_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
        )
        cfg = _make_backtest_config(
            start_date=date(2025, 1, 2),
            end_date=date(2025, 6, 30),
            top_n=20,
            use_llm_agents=True,
        )
        est = estimate_experiment_cost(cfg, agents, n_runs=5)
        news_cost = next(c for c in est.per_analyst_costs if c["analyst"] == "news_analyst")
        assert news_cost["unknown_model"] is True

    def test_format_output(self) -> None:
        agents = _make_agents_config(model="claude-sonnet-4-6")
        cfg = _make_backtest_config(
            start_date=date(2025, 1, 2),
            end_date=date(2025, 6, 30),
            top_n=20,
            use_llm_agents=True,
        )
        est = estimate_experiment_cost(cfg, agents, n_runs=5)
        text = est.format()
        assert "Estimated cost" in text
        assert "news_analyst" in text
        assert "fundamentals_analyst" in text
        assert "technical_analyst" in text
```

### Step 5.2: Run the tests to verify failure

```bash
.venv/bin/pytest tests/unit/backtest/test_statistics.py -q -x -k "Cost" 2>&1 | head -10
```

### Step 5.3: Implement cost estimation

- [ ] **Add to `app/modules/backtest/statistics.py`**:

```python
# Per-call cost estimates by model. Based on §10 cost analysis:
# ~3K input tokens × $/1M rate + ~200 output tokens × $/1M rate.
# Anthropic prompt caching reduces effective input cost within a 5-min window;
# these are the blended estimates.
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

    Mirrors BacktestEngine's rebalance schedule logic. Counts the first
    trading day on or after each period boundary within the date range.
    """
    from datetime import timedelta

    if frequency == "daily":
        # Every weekday
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
            f"Estimated cost: ~${self.total_cost:.0f} "
            f"({self.n_runs} runs \u00d7 {self.calls_per_run:,} calls/run)",
        ]
        for ac in self.per_analyst_costs:
            model_note = ""
            if ac.get("unknown_model"):
                model_note = f" (unknown model \u2014 using default ${_DEFAULT_COST_PER_CALL}/call)"
            lines.append(
                f"  {ac['analyst']:<24} {ac['model']:<30} "
                f"\u00d7 ${ac['cost_per_call']:.3f}/call{model_note}"
            )
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
        config.start_date, config.end_date, str(config.rebalance_frequency),
    )

    per_analyst: list[dict] = []
    cost_per_stock_per_rebalance = 0.0
    for analyst_name in _ANALYST_TYPES:
        analyst_cfg = getattr(agents_config, analyst_name)
        cost = _COST_PER_CALL_BY_MODEL.get(analyst_cfg.model, _DEFAULT_COST_PER_CALL)
        unknown = analyst_cfg.model not in _COST_PER_CALL_BY_MODEL
        per_analyst.append({
            "analyst": analyst_name,
            "model": analyst_cfg.model,
            "cost_per_call": cost,
            "unknown_model": unknown,
        })
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
```

Note: add `from datetime import date` to the runtime imports if not already present (it may only be in `TYPE_CHECKING`).

### Step 5.4: Run tests to verify they pass

```bash
.venv/bin/pytest tests/unit/backtest/test_statistics.py -q -x -k "Cost"
```

### Step 5.5: Run linter

```bash
.venv/bin/ruff check app/modules/backtest/statistics.py tests/unit/backtest/test_statistics.py
.venv/bin/ruff format app/modules/backtest/statistics.py tests/unit/backtest/test_statistics.py
```

### Step 5.6: Commit

```bash
git add app/modules/backtest/statistics.py tests/unit/backtest/test_statistics.py
git commit -m "$(cat <<'EOF'
Add per-analyst cost estimation for probe and experiment runs

Estimates dollar cost using per-model lookup table (Sonnet $0.012,
Opus $0.06, Haiku $0.002). Shows per-analyst model + cost breakdown.
Falls back to default $0.012 for unknown models with a warning note.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Create `noise_floor_store.py`

**Files:**
- Create: `app/modules/backtest/noise_floor_store.py`
- Test: `tests/unit/backtest/test_noise_floor_store.py`

### Step 6.1: Write the failing tests

- [ ] **Create `tests/unit/backtest/test_noise_floor_store.py`**:

```python
"""Unit tests for NoiseFloorStore — SQLite-backed noise floor persistence."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.modules.backtest.noise_floor_store import NoiseFloorStore
from app.modules.backtest.statistics import MetricNoiseFloor, NoiseFloor


def _make_noise_floor(config_hash: str = "abc123", **overrides) -> NoiseFloor:
    defaults = dict(
        config_hash=config_hash,
        config_label="medium / growth / 2025-12-31",
        skill_bundle_hash="def456" * 10 + "def4",
        n_runs=5,
        created_at=datetime(2026, 4, 9, 12, 0, 0),
        last_updated_at=datetime(2026, 4, 9, 12, 0, 0),
        metrics={
            "total_return": MetricNoiseFloor(
                metric_name="total_return",
                mean=0.10,
                stddev=0.015,
                n=5,
                sample_values=[0.09, 0.10, 0.11, 0.10, 0.10],
            ),
            "sharpe_ratio": MetricNoiseFloor(
                metric_name="sharpe_ratio",
                mean=1.2,
                stddev=0.08,
                n=5,
                sample_values=[1.15, 1.20, 1.25, 1.18, 1.22],
            ),
        },
        sample_run_ids=["probe_0", "probe_1", "probe_2", "probe_3", "probe_4"],
    )
    defaults.update(overrides)
    return NoiseFloor(**defaults)


class TestNoiseFloorStore:
    def test_put_and_get_round_trip(self, tmp_path: Path) -> None:
        store = NoiseFloorStore(tmp_path / "nf.db")
        try:
            nf = _make_noise_floor()
            store.put(nf)
            loaded = store.get("abc123")
            assert loaded is not None
            assert loaded.config_hash == "abc123"
            assert loaded.n_runs == 5
            assert "total_return" in loaded.metrics
            assert loaded.metrics["total_return"].mean == 0.10
            assert loaded.metrics["total_return"].stddev == 0.015
            assert loaded.sample_run_ids == nf.sample_run_ids
        finally:
            store.close()

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        store = NoiseFloorStore(tmp_path / "nf.db")
        try:
            assert store.get("nonexistent") is None
        finally:
            store.close()

    def test_put_overwrites_existing(self, tmp_path: Path) -> None:
        store = NoiseFloorStore(tmp_path / "nf.db")
        try:
            store.put(_make_noise_floor(n_runs=5))
            store.put(_make_noise_floor(n_runs=10))
            loaded = store.get("abc123")
            assert loaded is not None
            assert loaded.n_runs == 10
        finally:
            store.close()

    def test_invalidate_existing_returns_true(self, tmp_path: Path) -> None:
        store = NoiseFloorStore(tmp_path / "nf.db")
        try:
            store.put(_make_noise_floor())
            assert store.invalidate("abc123") is True
            assert store.get("abc123") is None
        finally:
            store.close()

    def test_invalidate_missing_returns_false(self, tmp_path: Path) -> None:
        store = NoiseFloorStore(tmp_path / "nf.db")
        try:
            assert store.invalidate("nonexistent") is False
        finally:
            store.close()

    def test_list_all(self, tmp_path: Path) -> None:
        store = NoiseFloorStore(tmp_path / "nf.db")
        try:
            store.put(_make_noise_floor("hash_a"))
            store.put(_make_noise_floor("hash_b"))
            all_nfs = store.list_all()
            assert len(all_nfs) == 2
            hashes = {nf.config_hash for nf in all_nfs}
            assert hashes == {"hash_a", "hash_b"}
        finally:
            store.close()

    def test_list_all_empty(self, tmp_path: Path) -> None:
        store = NoiseFloorStore(tmp_path / "nf.db")
        try:
            assert store.list_all() == []
        finally:
            store.close()

    def test_reopens_existing_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "nf.db"
        store1 = NoiseFloorStore(db_path)
        store1.put(_make_noise_floor())
        store1.close()

        store2 = NoiseFloorStore(db_path)
        try:
            loaded = store2.get("abc123")
            assert loaded is not None
            assert loaded.n_runs == 5
        finally:
            store2.close()
```

### Step 6.2: Run the tests to verify failure

```bash
.venv/bin/pytest tests/unit/backtest/test_noise_floor_store.py -q -x 2>&1 | head -10
```

### Step 6.3: Create `noise_floor_store.py`

- [ ] **Create `app/modules/backtest/noise_floor_store.py`**:

```python
"""SQLite-backed store for noise floor estimates.

Keyed by config_hash (from hash_experiment_config). Each entry is a
serialized NoiseFloor dataclass. The store uses INSERT OR REPLACE for
upsert semantics — putting a noise floor with an existing config_hash
overwrites the previous entry.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.modules.backtest.statistics import MetricNoiseFloor, NoiseFloor

_SCHEMA = """
CREATE TABLE IF NOT EXISTS noise_floors (
    config_hash TEXT PRIMARY KEY,
    config_label TEXT NOT NULL,
    skill_bundle_hash TEXT NOT NULL,
    n_runs INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    last_updated_at TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    sample_run_ids_json TEXT NOT NULL
);
"""


class NoiseFloorStore:
    """Persistent store for noise floor estimates, backed by SQLite."""

    def __init__(self, db_path: Path = Path("data/noise_floor_cache.db")) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def get(self, config_hash: str) -> NoiseFloor | None:
        """Retrieve a noise floor by config_hash. Returns None if not found."""
        row = self._conn.execute(
            "SELECT * FROM noise_floors WHERE config_hash = ?",
            (config_hash,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_noise_floor(row)

    def put(self, noise_floor: NoiseFloor) -> None:
        """Insert or replace a noise floor entry."""
        metrics_json = json.dumps({
            name: {
                "metric_name": mnf.metric_name,
                "mean": mnf.mean,
                "stddev": mnf.stddev,
                "n": mnf.n,
                "sample_values": mnf.sample_values,
            }
            for name, mnf in noise_floor.metrics.items()
        })
        sample_run_ids_json = json.dumps(noise_floor.sample_run_ids)
        self._conn.execute(
            """INSERT OR REPLACE INTO noise_floors
            (config_hash, config_label, skill_bundle_hash, n_runs,
             created_at, last_updated_at, metrics_json, sample_run_ids_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                noise_floor.config_hash,
                noise_floor.config_label,
                noise_floor.skill_bundle_hash,
                noise_floor.n_runs,
                noise_floor.created_at.isoformat(),
                noise_floor.last_updated_at.isoformat(),
                metrics_json,
                sample_run_ids_json,
            ),
        )
        self._conn.commit()

    def invalidate(self, config_hash: str) -> bool:
        """Delete a noise floor entry. Returns True if it existed."""
        cursor = self._conn.execute(
            "DELETE FROM noise_floors WHERE config_hash = ?",
            (config_hash,),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def list_all(self) -> list[NoiseFloor]:
        """Return all stored noise floors, ordered by last_updated_at desc."""
        rows = self._conn.execute(
            "SELECT * FROM noise_floors ORDER BY last_updated_at DESC"
        ).fetchall()
        return [self._row_to_noise_floor(row) for row in rows]

    def close(self) -> None:
        """Close the SQLite connection."""
        self._conn.close()

    @staticmethod
    def _row_to_noise_floor(row: tuple) -> NoiseFloor:
        from datetime import datetime

        (config_hash, config_label, skill_bundle_hash, n_runs,
         created_at, last_updated_at, metrics_json, sample_run_ids_json) = row
        metrics_raw = json.loads(metrics_json)
        metrics = {
            name: MetricNoiseFloor(**data)
            for name, data in metrics_raw.items()
        }
        return NoiseFloor(
            config_hash=config_hash,
            config_label=config_label,
            skill_bundle_hash=skill_bundle_hash,
            n_runs=n_runs,
            created_at=datetime.fromisoformat(created_at),
            last_updated_at=datetime.fromisoformat(last_updated_at),
            metrics=metrics,
            sample_run_ids=json.loads(sample_run_ids_json),
        )
```

### Step 6.4: Run the tests

```bash
.venv/bin/pytest tests/unit/backtest/test_noise_floor_store.py -q
```

### Step 6.5: Run linter

```bash
.venv/bin/ruff check app/modules/backtest/noise_floor_store.py tests/unit/backtest/test_noise_floor_store.py
.venv/bin/ruff format app/modules/backtest/noise_floor_store.py tests/unit/backtest/test_noise_floor_store.py
```

### Step 6.6: Commit

```bash
git add app/modules/backtest/noise_floor_store.py tests/unit/backtest/test_noise_floor_store.py
git commit -m "$(cat <<'EOF'
Add NoiseFloorStore — SQLite-backed noise floor persistence

Single-table store keyed by config_hash with INSERT OR REPLACE upsert
semantics. Serializes MetricNoiseFloor dicts as JSON. Supports get, put,
invalidate, and list_all operations.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Create `experiment.py` — `ExperimentResult` dataclass and `format_experiment_report`

**Files:**
- Create: `app/modules/backtest/experiment.py`
- Test: `tests/unit/backtest/test_experiment.py`

This task creates the module with the result container and formatter. Task 8 adds `ExperimentRunner` (async orchestration). Task 9 adds `save_experiment_result`.

### Step 7.1: Write the failing tests

- [ ] **Create `tests/unit/backtest/test_experiment.py`**:

```python
"""Unit tests for experiment.py — experiment result containers and formatters."""
from __future__ import annotations

from datetime import datetime

import pytest

from app.modules.backtest.comparison import compare_runs
from app.modules.backtest.experiment import ExperimentResult, format_experiment_report
from app.modules.backtest.statistics import MetricNoiseFloor, NoiseFloor, Verdict
from tests.unit.backtest.conftest import (
    _make_backtest_run,
    _make_performance_metrics,
)


def _make_test_noise_floor(**overrides) -> NoiseFloor:
    defaults = dict(
        config_hash="test_hash",
        config_label="medium / growth / 2025-12-31",
        skill_bundle_hash="abc" * 20 + "abcd",
        n_runs=5,
        created_at=datetime(2026, 4, 5, 12, 0, 0),
        last_updated_at=datetime(2026, 4, 5, 12, 0, 0),
        metrics={
            "total_return": MetricNoiseFloor(
                metric_name="total_return", mean=0.10, stddev=0.015, n=5,
                sample_values=[0.09, 0.10, 0.11, 0.10, 0.10],
            ),
        },
        sample_run_ids=["probe_0", "probe_1", "probe_2", "probe_3", "probe_4"],
    )
    defaults.update(overrides)
    return NoiseFloor(**defaults)


def _make_test_experiment_result(**overrides) -> ExperimentResult:
    baseline = _make_backtest_run(run_id="baseline_run", skill_bundle_hash="a" * 64)
    treatment = _make_backtest_run(run_id="treatment_run", skill_bundle_hash="b" * 64)
    cmp = compare_runs(baseline, treatment)
    nf = _make_test_noise_floor()
    defaults = dict(
        baseline_run_id="baseline_run",
        treatment_run_id="treatment_run",
        noise_floor=nf,
        noise_floor_age_days=5,
        noise_floor_stale=False,
        bundle_mismatch_warning=False,
        comparison=cmp,
        verdicts=[
            Verdict(
                metric_name="total_return",
                baseline=0.10, treatment=0.12, delta=0.02,
                sigma=1.3, label="POSSIBLE SIGNAL",
            ),
        ],
        t_correction_used=False,
    )
    defaults.update(overrides)
    return ExperimentResult(**defaults)


class TestExperimentResult:
    def test_dataclass_fields(self) -> None:
        result = _make_test_experiment_result()
        assert result.baseline_run_id == "baseline_run"
        assert result.treatment_run_id == "treatment_run"
        assert result.noise_floor_age_days == 5
        assert result.noise_floor_stale is False
        assert result.bundle_mismatch_warning is False
        assert len(result.verdicts) == 1


class TestFormatExperimentReport:
    def test_contains_header(self) -> None:
        result = _make_test_experiment_result()
        report = format_experiment_report(result)
        assert "EXPERIMENT REPORT" in report

    def test_contains_verdict(self) -> None:
        result = _make_test_experiment_result()
        report = format_experiment_report(result)
        assert "POSSIBLE SIGNAL" in report

    def test_stale_warning_shown(self) -> None:
        result = _make_test_experiment_result(
            noise_floor_stale=True,
            noise_floor_age_days=47,
        )
        report = format_experiment_report(result)
        assert "stale" in report.lower()

    def test_bundle_mismatch_warning_shown(self) -> None:
        result = _make_test_experiment_result(bundle_mismatch_warning=True)
        report = format_experiment_report(result)
        assert "different bundle" in report.lower() or "mismatch" in report.lower()

    def test_fresh_noise_floor_marker(self) -> None:
        result = _make_test_experiment_result(noise_floor_stale=False)
        report = format_experiment_report(result)
        assert "fresh" in report.lower() or "\u2713" in report

    def test_signal_drilldown_included(self) -> None:
        result = _make_test_experiment_result()
        report = format_experiment_report(result, top_n_signals=5)
        assert "SIGNAL DRILLDOWN" in report or "divergen" in report.lower()

    def test_t_correction_noted(self) -> None:
        result = _make_test_experiment_result(t_correction_used=True)
        report = format_experiment_report(result)
        assert "t-correct" in report.lower()
```

### Step 7.2: Run the tests to verify failure

```bash
.venv/bin/pytest tests/unit/backtest/test_experiment.py -q -x 2>&1 | head -10
```

### Step 7.3: Create `experiment.py` with `ExperimentResult` and `format_experiment_report`

- [ ] **Create `app/modules/backtest/experiment.py`**:

```python
"""Phase 3 experiment harness: orchestration, result containers, and report formatting.

ExperimentRunner ties together the backtest engine, comparison module,
noise floor store, and verdict logic into a single async workflow.
format_experiment_report renders the result as a human-readable report
matching the sample output in §8.7 of the architecture spec.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.modules.backtest.comparison import (
    RunComparison,
    format_metric_table,
    format_signal_drilldown,
)
from app.modules.backtest.statistics import (
    NoiseFloor,
    Verdict,
    format_verdict_table,
)

if TYPE_CHECKING:
    pass


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
    lines.append(
        f"  Period:           {cfg.start_date} to {cfg.end_date}"
    )

    # Agent config from baseline run
    eac = result.comparison.baseline.effective_agents_config
    if eac:
        models = set()
        for attr in ("news_analyst", "fundamentals_analyst", "technical_analyst"):
            models.add(getattr(eac, attr).model)
        temps = set()
        for attr in ("news_analyst", "fundamentals_analyst", "technical_analyst"):
            temps.add(str(getattr(eac, attr).temperature))
        lines.append(
            f"  Model:            {', '.join(sorted(models))} "
            f"@ temperature {', '.join(sorted(temps))}"
        )

    b_hash = result.comparison.baseline.skill_bundle_hash[:12]
    t_hash = result.comparison.treatment.skill_bundle_hash[:12]
    b_name = result.comparison.baseline.skill_bundle_name or "live"
    t_name = result.comparison.treatment.skill_bundle_name or "live"
    lines.append(f"  Baseline skills:  {b_name} (sha: {b_hash}...)")
    lines.append(f"  Treatment skills: {t_name} (sha: {t_hash}...)")

    # Noise floor status
    age_str = f"N={nf.n_runs}, age {result.noise_floor_age_days} days"
    if result.noise_floor_stale:
        status = f"\u26a0 stale ({result.noise_floor_age_days} days old)"
    else:
        status = "\u2713 fresh"
    lines.append(f"  Noise floor:      {age_str}  {status}")

    if result.bundle_mismatch_warning:
        lines.append(
            f"  \u26a0 Noise floor probed against different bundle "
            f"(sha: {nf.skill_bundle_hash[:12]}...)"
        )

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
```

### Step 7.4: Run the tests

```bash
.venv/bin/pytest tests/unit/backtest/test_experiment.py -q
```

### Step 7.5: Run linter

```bash
.venv/bin/ruff check app/modules/backtest/experiment.py tests/unit/backtest/test_experiment.py
.venv/bin/ruff format app/modules/backtest/experiment.py tests/unit/backtest/test_experiment.py
```

### Step 7.6: Commit

```bash
git add app/modules/backtest/experiment.py tests/unit/backtest/test_experiment.py
git commit -m "$(cat <<'EOF'
Add ExperimentResult dataclass and format_experiment_report

Report format: header (config, models, skill hashes, noise floor status)
→ warnings → verdict table → signal drilldown → footer. Matches the
sample output in §8.7 of the architecture spec.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Add `ExperimentRunner` to `experiment.py`

**Files:**
- Modify: `app/modules/backtest/experiment.py`
- Test: `tests/unit/backtest/test_experiment.py`

### Step 8.1: Write the failing tests

- [ ] **Add to `tests/unit/backtest/test_experiment.py`**:

```python
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date, datetime, timedelta

from app.modules.backtest.config import BacktestConfig
from app.modules.backtest.experiment import ExperimentRunner
from app.modules.backtest.noise_floor_store import NoiseFloorStore
from app.modules.backtest.models import BacktestResult, PerformanceMetrics
from app.modules.backtest.result_store import BacktestRun
from tests.unit.backtest.conftest import (
    _make_backtest_config,
    _make_backtest_run,
    _make_agents_config,
)


class TestExperimentRunner:
    def _make_store_with_floor(self, tmp_path, config_hash="test_hash", age_days=5):
        store = NoiseFloorStore(tmp_path / "nf.db")
        nf = _make_test_noise_floor(
            config_hash=config_hash,
            created_at=datetime.now() - timedelta(days=age_days),
            last_updated_at=datetime.now() - timedelta(days=age_days),
        )
        store.put(nf)
        return store

    @pytest.mark.asyncio
    async def test_missing_noise_floor_raises_with_command(self, tmp_path) -> None:
        store = NoiseFloorStore(tmp_path / "nf.db")
        runner = ExperimentRunner(
            result_store_path=tmp_path / "runs",
            noise_floor_store=store,
        )
        cfg = _make_backtest_config(use_llm_agents=True)
        agents = _make_agents_config()
        with pytest.raises(RuntimeError, match="probe_noise"):
            await runner.run_experiment(
                config=cfg,
                agents_config=agents,
                baseline_skills_bundle="baseline_v1",
                treatment_skills_bundle="live",
            )
        store.close()

    @pytest.mark.asyncio
    async def test_stale_noise_floor_warns_but_proceeds(self, tmp_path) -> None:
        """A noise floor older than 30 days should set stale=True but not error."""
        store = self._make_store_with_floor(tmp_path, age_days=45)

        mock_run = _make_backtest_run(skill_bundle_hash="a" * 64)
        mock_engine = AsyncMock()
        mock_engine.return_value = mock_run

        runner = ExperimentRunner(
            result_store_path=tmp_path / "runs",
            noise_floor_store=store,
        )
        cfg = _make_backtest_config(use_llm_agents=True)
        agents = _make_agents_config()

        with patch.object(runner, "_run_backtest", mock_engine):
            with patch(
                "app.modules.backtest.experiment.hash_experiment_config",
                return_value="test_hash",
            ):
                result = await runner.run_experiment(
                    config=cfg,
                    agents_config=agents,
                    baseline_skills_bundle="baseline_v1",
                    treatment_skills_bundle="live",
                )
        assert result.noise_floor_stale is True
        assert result.noise_floor_age_days >= 44
        store.close()

    @pytest.mark.asyncio
    async def test_bundle_mismatch_warning(self, tmp_path) -> None:
        """Noise floor probed with a different bundle → bundle_mismatch_warning=True."""
        store = self._make_store_with_floor(tmp_path)

        # Baseline has a different skill_bundle_hash than the noise floor
        mock_run = _make_backtest_run(
            skill_bundle_hash="different" * 8,
        )
        mock_engine = AsyncMock()
        mock_engine.return_value = mock_run

        runner = ExperimentRunner(
            result_store_path=tmp_path / "runs",
            noise_floor_store=store,
        )
        cfg = _make_backtest_config(use_llm_agents=True)
        agents = _make_agents_config()

        with patch.object(runner, "_run_backtest", mock_engine):
            with patch(
                "app.modules.backtest.experiment.hash_experiment_config",
                return_value="test_hash",
            ):
                result = await runner.run_experiment(
                    config=cfg,
                    agents_config=agents,
                    baseline_skills_bundle="baseline_v1",
                    treatment_skills_bundle="live",
                )
        assert result.bundle_mismatch_warning is True
        store.close()
```

### Step 8.2: Run the tests to verify failure

```bash
.venv/bin/pytest tests/unit/backtest/test_experiment.py::TestExperimentRunner -q -x 2>&1 | head -10
```

### Step 8.3: Implement `ExperimentRunner`

- [ ] **Add to `app/modules/backtest/experiment.py`**:

```python
from datetime import datetime, timedelta

from app.modules.backtest.comparison import compare_runs
from app.modules.backtest.noise_floor_store import NoiseFloorStore
from app.modules.backtest.result_store import BacktestRun, save_run, load_run
from app.modules.backtest.statistics import (
    compute_verdicts,
    hash_experiment_config,
)

_STALE_THRESHOLD_DAYS = 30


class ExperimentRunner:
    """Orchestrates a baseline-vs-treatment experiment.

    The runner does NOT own the BacktestEngine or LLMResponseCache directly.
    Subclasses or callers override _run_backtest to supply the engine invocation.
    The default implementation is a placeholder that raises NotImplementedError;
    the CLI scripts (run_experiment.py) provide the real wiring.
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
        config: "BacktestConfig",
        skills_bundle: str | None,
    ) -> BacktestRun:
        """Run a single backtest and return the result as a BacktestRun.

        Override this in the CLI script to wire up the real BacktestEngine.
        """
        raise NotImplementedError(
            "ExperimentRunner._run_backtest must be overridden by the caller"
        )

    async def run_experiment(
        self,
        config: "BacktestConfig",
        agents_config: "AgentsConfig",
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
        bundle_mismatch = (
            nf.skill_bundle_hash != baseline_run.skill_bundle_hash
        )

        # 7. Compute verdicts
        verdicts = compute_verdicts(
            comparison, nf, use_t_correction=use_t_correction,
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
```

Add the necessary TYPE_CHECKING imports at the top of the file:

```python
if TYPE_CHECKING:
    from app.modules.backtest.config import BacktestConfig
    from app.modules.equities.config import AgentsConfig
```

### Step 8.4: Run the tests

```bash
.venv/bin/pytest tests/unit/backtest/test_experiment.py -q
```

### Step 8.5: Run linter

```bash
.venv/bin/ruff check app/modules/backtest/experiment.py tests/unit/backtest/test_experiment.py
.venv/bin/ruff format app/modules/backtest/experiment.py tests/unit/backtest/test_experiment.py
```

### Step 8.6: Commit

```bash
git add app/modules/backtest/experiment.py tests/unit/backtest/test_experiment.py
git commit -m "$(cat <<'EOF'
Add ExperimentRunner async orchestration

Orchestrates noise floor lookup → baseline backtest → treatment backtest
→ compare → compute verdicts. Hard error with copy-pastable command when
noise floor is missing. Warns on stale (>30 day) and bundle-mismatched
noise floors.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Add `save_experiment_result` and `ExperimentResult.to_json_dict`

**Files:**
- Modify: `app/modules/backtest/experiment.py`
- Test: `tests/unit/backtest/test_experiment.py`

### Step 9.1: Write the failing tests

- [ ] **Add to `tests/unit/backtest/test_experiment.py`**:

```python
import json
from pathlib import Path

from app.modules.backtest.experiment import save_experiment_result


class TestExperimentResultToJsonDict:
    def test_curated_schema_keys(self) -> None:
        result = _make_test_experiment_result()
        d = result.to_json_dict()
        expected_keys = {
            "experiment_id", "generated_at", "config_summary",
            "baseline_run_id", "treatment_run_id",
            "baseline_skill_bundle_hash", "treatment_skill_bundle_hash",
            "noise_floor_summary", "verdicts", "metric_deltas",
            "signal_divergences", "compatibility_warnings",
            "t_correction_used",
        }
        assert set(d.keys()) == expected_keys

    def test_verdicts_serialized(self) -> None:
        result = _make_test_experiment_result()
        d = result.to_json_dict()
        assert len(d["verdicts"]) == 1
        v = d["verdicts"][0]
        assert v["metric_name"] == "total_return"
        assert v["label"] == "POSSIBLE SIGNAL"

    def test_noise_floor_summary_no_sample_values(self) -> None:
        """Noise floor sample values should NOT be in the JSON."""
        result = _make_test_experiment_result()
        d = result.to_json_dict()
        nf_summary = d["noise_floor_summary"]
        assert "sample_values" not in json.dumps(nf_summary)
        assert "config_hash" in nf_summary
        assert "n_runs" in nf_summary

    def test_run_ids_not_full_runs(self) -> None:
        """JSON should contain run IDs, not full BacktestRun objects."""
        result = _make_test_experiment_result()
        d = result.to_json_dict()
        assert isinstance(d["baseline_run_id"], str)
        assert isinstance(d["treatment_run_id"], str)


class TestSaveExperimentResult:
    def test_saves_json_file(self, tmp_path: Path) -> None:
        result = _make_test_experiment_result()
        path = save_experiment_result(result, experiments_dir=tmp_path)
        assert path.exists()
        assert path.suffix == ".json"
        data = json.loads(path.read_text())
        assert data["baseline_run_id"] == "baseline_run"

    def test_creates_dir_if_missing(self, tmp_path: Path) -> None:
        experiments_dir = tmp_path / "nested" / "experiments"
        result = _make_test_experiment_result()
        path = save_experiment_result(result, experiments_dir=experiments_dir)
        assert path.exists()
```

### Step 9.2: Run the tests to verify failure

```bash
.venv/bin/pytest tests/unit/backtest/test_experiment.py -q -x -k "ToJson or Save" 2>&1 | head -10
```

### Step 9.3: Implement `to_json_dict` and `save_experiment_result`

- [ ] **Add `to_json_dict` method to `ExperimentResult`**:

```python
    def to_json_dict(self) -> dict:
        """Return the curated JSON schema for this experiment result.

        Includes verdicts (primary payload), metric_deltas, signal_divergences,
        and compatibility_warnings from the underlying RunComparison. Runs are
        referenced by ID only. Noise floor is summarized without sample values.
        """
        from datetime import UTC, datetime as dt

        cfg = self.comparison.baseline.config
        return {
            "experiment_id": f"{self.baseline_run_id}_vs_{self.treatment_run_id}",
            "generated_at": dt.now(UTC).isoformat(),
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
```

- [ ] **Add `save_experiment_result` function**:

```python
import json


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
```

### Step 9.4: Run the tests

```bash
.venv/bin/pytest tests/unit/backtest/test_experiment.py -q
```

### Step 9.5: Run linter

```bash
.venv/bin/ruff check app/modules/backtest/experiment.py tests/unit/backtest/test_experiment.py
.venv/bin/ruff format app/modules/backtest/experiment.py tests/unit/backtest/test_experiment.py
```

### Step 9.6: Commit

```bash
git add app/modules/backtest/experiment.py tests/unit/backtest/test_experiment.py
git commit -m "$(cat <<'EOF'
Add ExperimentResult.to_json_dict and save_experiment_result

Curated JSON schema: verdicts as primary payload, metric_deltas and
signal_divergences from RunComparison, runs by ID only, noise floor
summarized without sample values. Write-once JSON persistence to
data/experiments/.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Create `scripts/probe_noise.py`

**Files:**
- Create: `scripts/probe_noise.py`
- Test: `tests/unit/backtest/test_probe_noise_script.py`

### Step 10.1: Write the failing tests

- [ ] **Create `tests/unit/backtest/test_probe_noise_script.py`**:

```python
"""Unit tests for scripts/probe_noise.py — CLI argument parsing and flow control."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest


class TestProbeNoiseArgParsing:
    def _parse(self, args: list[str]):
        """Import and run the argument parser."""
        with patch.object(sys, "argv", ["probe_noise"] + args):
            from scripts.probe_noise import build_parser

            return build_parser().parse_args(args)

    def test_preset_and_branch_required(self) -> None:
        with pytest.raises(SystemExit):
            self._parse([])

    def test_minimal_args(self) -> None:
        args = self._parse(["--preset", "medium", "--branch", "growth",
                            "--end-date", "2025-12-31"])
        assert args.preset == "medium"
        assert args.branch == "growth"
        assert args.end_date == "2025-12-31"

    def test_runs_default_is_5(self) -> None:
        args = self._parse(["--preset", "quick", "--branch", "growth",
                            "--end-date", "2025-12-31"])
        assert args.runs == 5

    def test_runs_custom(self) -> None:
        args = self._parse(["--preset", "quick", "--branch", "growth",
                            "--end-date", "2025-12-31", "--runs", "7"])
        assert args.runs == 7

    def test_invalidate_flag(self) -> None:
        args = self._parse(["--preset", "quick", "--branch", "growth",
                            "--end-date", "2025-12-31", "--invalidate"])
        assert args.invalidate is True

    def test_force_flag(self) -> None:
        args = self._parse(["--preset", "quick", "--branch", "growth",
                            "--end-date", "2025-12-31", "--force"])
        assert args.force is True

    def test_yes_flag(self) -> None:
        args = self._parse(["--preset", "quick", "--branch", "growth",
                            "--end-date", "2025-12-31", "--yes"])
        assert args.yes is True

    def test_skills_bundle_flag(self) -> None:
        args = self._parse(["--preset", "quick", "--branch", "growth",
                            "--end-date", "2025-12-31", "--skills-bundle", "baseline_v1"])
        assert args.skills_bundle == "baseline_v1"


class TestProbeNoiseValidation:
    def test_runs_below_minimum_exits(self) -> None:
        """--runs 2 should exit with an error (min is 3)."""
        from scripts.probe_noise import validate_args

        args = MagicMock(runs=2)
        with pytest.raises(SystemExit):
            validate_args(args)

    def test_runs_above_10_warns(self, capsys) -> None:
        """--runs 12 should print a warning but not error."""
        from scripts.probe_noise import validate_args

        args = MagicMock(runs=12)
        validate_args(args)
        captured = capsys.readouterr()
        assert "marginal improvement" in captured.out.lower() or "diminishing" in captured.out.lower()

    def test_runs_5_no_warning(self, capsys) -> None:
        from scripts.probe_noise import validate_args

        args = MagicMock(runs=5)
        validate_args(args)
        captured = capsys.readouterr()
        assert "marginal" not in captured.out.lower()
```

### Step 10.2: Run the tests to verify failure

```bash
.venv/bin/pytest tests/unit/backtest/test_probe_noise_script.py -q -x 2>&1 | head -10
```

### Step 10.3: Create `scripts/probe_noise.py`

- [ ] **Create `scripts/probe_noise.py`**:

```python
"""Variance probing CLI: run N backtests with cache disabled to estimate per-metric noise.

Usage:
    python -m scripts.probe_noise --preset medium --branch growth --end-date 2025-12-31 \
        [--runs 5] [--skills-bundle NAME] [--invalidate] [--force] [--yes]

Behavior:
1. Build BacktestConfig from preset + flags. Compute config_hash.
2. If --invalidate: call NoiseFloorStore.invalidate and exit.
3. If entry already exists and --force is not set: print summary and exit.
4. Print expected cost and prompt for confirmation (--yes skips).
5. Run N backtests with use_llm_response_cache=False.
6. Compute noise floor and store via NoiseFloorStore.put.
7. Print per-metric mean ± stddev summary.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe LLM backtest variance to establish a per-metric noise floor.",
    )
    parser.add_argument("--preset", required=True, choices=["quick", "medium", "full"],
                        help="Backtest tier preset")
    parser.add_argument("--branch", required=True, help="Branch name (growth or value)")
    parser.add_argument("--end-date", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--runs", type=int, default=5,
                        help="Number of probe runs (default: 5, min: 3)")
    parser.add_argument("--skills-bundle", default=None,
                        help="Named skill bundle to probe against (default: live skills)")
    parser.add_argument("--invalidate", action="store_true",
                        help="Delete the existing noise floor for this config and exit")
    parser.add_argument("--force", action="store_true",
                        help="Re-probe even if a noise floor already exists")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the cost confirmation prompt")
    return parser


def validate_args(args) -> None:
    """Validate arguments. Exits on hard errors, warns on soft issues."""
    if args.runs < 3:
        print(f"Error: --runs {args.runs} is below the minimum of 3. "
              "With fewer than 3 samples, stddev is meaningless.", file=sys.stderr)
        sys.exit(1)
    if args.runs > 10:
        print(f"Note: --runs {args.runs} offers marginal improvement over N=5 or N=7. "
              "Cost scales linearly with N.")


async def run_probe(args) -> None:
    """Main probe logic. Separated from main() for testability."""
    from app.modules.backtest.config import BacktestTier, config_from_preset
    from app.modules.backtest.noise_floor_store import NoiseFloorStore
    from app.modules.backtest.result_store import hash_skill_bundle, save_run
    from app.modules.backtest.statistics import (
        compute_noise_floor,
        estimate_experiment_cost,
        hash_experiment_config,
    )
    from app.modules.equities.config import EquitiesConfig

    end_date = date.fromisoformat(args.end_date)
    preset = BacktestTier(args.preset)
    config = config_from_preset(preset, args.branch, end_date=end_date)

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

        # Run probe backtests
        config_label = f"{args.preset} / {args.branch} / {end_date}"
        skills_bundle = args.skills_bundle

        # Determine skill_bundle_hash for the noise floor metadata
        from app.modules.backtest.context import resolve_skills_bundle

        skills_dir = resolve_skills_bundle(skills_bundle)
        skill_bundle_hash = hash_skill_bundle(skills_dir)

        print(f"\nRunning {args.runs} probe backtests (cache disabled)...")
        probe_runs = []
        for i in range(args.runs):
            print(f"  Probe run {i + 1}/{args.runs}...", flush=True)

            # Override config for probe: disable cache, set skills bundle
            probe_config = config.model_copy(update={
                "use_llm_response_cache": False,
                "skills_bundle": skills_bundle,
            })

            # Import and run the backtest engine
            from app.modules.backtest.context import BacktestContext
            from app.modules.backtest.engine import BacktestEngine

            ctx = await BacktestContext.create(probe_config)
            engine = BacktestEngine(ctx)
            result = await engine.run()

            # Convert BacktestResult to BacktestRun for storage
            from datetime import datetime

            run_id = f"probe_{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}_{i}"
            run = result.to_backtest_run(
                run_id=run_id,
                skill_bundle_name=skills_bundle,
                skill_bundle_hash=skill_bundle_hash,
            )
            save_run(run)
            probe_runs.append(run)
            print(f"    Saved: {run_id}")

        # Compute and store noise floor
        nf = compute_noise_floor(
            probe_runs, config_hash, skill_bundle_hash, config_label=config_label,
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
```

**Important implementation note:** The `result.to_backtest_run(...)` call above assumes `BacktestResult` has a `to_backtest_run` helper. If Phase 1/2 didn't add this, a small adapter function is needed to construct a `BacktestRun` from a `BacktestResult` (similar to the existing logic in `scripts/run_backtest.py`'s save path). The implementer should check the actual `run_backtest.py` save path and replicate or extract that logic. This is flagged as a potential delta in the Self-Review section.

### Step 10.4: Run the tests

```bash
.venv/bin/pytest tests/unit/backtest/test_probe_noise_script.py -q
```

### Step 10.5: Run linter

```bash
.venv/bin/ruff check scripts/probe_noise.py tests/unit/backtest/test_probe_noise_script.py
.venv/bin/ruff format scripts/probe_noise.py tests/unit/backtest/test_probe_noise_script.py
```

### Step 10.6: Commit

```bash
git add scripts/probe_noise.py tests/unit/backtest/test_probe_noise_script.py
git commit -m "$(cat <<'EOF'
Add probe_noise.py CLI for variance probing

Runs N backtests with LLM response cache disabled to estimate per-metric
noise floor. Shows per-analyst cost breakdown before prompting for
confirmation. Validates --runs (min 3, warn >10). Supports --invalidate,
--force, --yes flags.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Create `scripts/run_experiment.py`

**Files:**
- Create: `scripts/run_experiment.py`
- Test: `tests/unit/backtest/test_run_experiment_script.py`

### Step 11.1: Write the failing tests

- [ ] **Create `tests/unit/backtest/test_run_experiment_script.py`**:

```python
"""Unit tests for scripts/run_experiment.py — CLI argument parsing and report output."""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest


class TestRunExperimentArgParsing:
    def _parse(self, args: list[str]):
        with patch.object(sys, "argv", ["run_experiment"] + args):
            from scripts.run_experiment import build_parser

            return build_parser().parse_args(args)

    def test_required_args(self) -> None:
        with pytest.raises(SystemExit):
            self._parse([])

    def test_minimal_args(self) -> None:
        args = self._parse([
            "--preset", "medium", "--branch", "growth",
            "--end-date", "2025-12-31",
            "--baseline-bundle", "baseline_v1",
            "--treatment-bundle", "live",
        ])
        assert args.preset == "medium"
        assert args.branch == "growth"
        assert args.baseline_bundle == "baseline_v1"
        assert args.treatment_bundle == "live"

    def test_t_correction_flag(self) -> None:
        args = self._parse([
            "--preset", "quick", "--branch", "growth",
            "--end-date", "2025-12-31",
            "--baseline-bundle", "a", "--treatment-bundle", "b",
            "--t-correction",
        ])
        assert args.t_correction is True

    def test_t_correction_default_false(self) -> None:
        args = self._parse([
            "--preset", "quick", "--branch", "growth",
            "--end-date", "2025-12-31",
            "--baseline-bundle", "a", "--treatment-bundle", "b",
        ])
        assert args.t_correction is False

    def test_report_out_flag(self) -> None:
        args = self._parse([
            "--preset", "quick", "--branch", "growth",
            "--end-date", "2025-12-31",
            "--baseline-bundle", "a", "--treatment-bundle", "b",
            "--report-out", "/tmp/report.txt",
        ])
        assert args.report_out == "/tmp/report.txt"

    def test_json_flag(self) -> None:
        args = self._parse([
            "--preset", "quick", "--branch", "growth",
            "--end-date", "2025-12-31",
            "--baseline-bundle", "a", "--treatment-bundle", "b",
            "--json",
        ])
        assert args.json is True
```

### Step 11.2: Run the tests to verify failure

```bash
.venv/bin/pytest tests/unit/backtest/test_run_experiment_script.py -q -x 2>&1 | head -10
```

### Step 11.3: Create `scripts/run_experiment.py`

- [ ] **Create `scripts/run_experiment.py`**:

```python
"""Full experiment harness CLI: run a baseline-vs-treatment experiment with verdict labels.

Usage:
    python -m scripts.run_experiment --preset medium --branch growth \
        --end-date 2025-12-31 \
        --baseline-bundle baseline_v1 --treatment-bundle live \
        [--t-correction] [--report-out path/to/report.txt] [--json]

Flow:
1. Build BacktestConfig from preset.
2. Instantiate ExperimentRunner with NoiseFloorStore.
3. Run experiment (looks up noise floor, runs backtests, computes verdicts).
4. Print or save the formatted report.
5. Save experiment result JSON to data/experiments/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a baseline-vs-treatment experiment with statistical verdict labels.",
    )
    parser.add_argument("--preset", required=True, choices=["quick", "medium", "full"],
                        help="Backtest tier preset")
    parser.add_argument("--branch", required=True, help="Branch name (growth or value)")
    parser.add_argument("--end-date", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--baseline-bundle", required=True,
                        help="Skill bundle name for baseline (or 'live')")
    parser.add_argument("--treatment-bundle", required=True,
                        help="Skill bundle name for treatment (or 'live')")
    parser.add_argument("--t-correction", action="store_true",
                        help="Use t-distribution correction for verdict thresholds")
    parser.add_argument("--report-out", default=None,
                        help="Write report to file instead of stdout")
    parser.add_argument("--json", action="store_true",
                        help="Output experiment result as JSON instead of text report")
    return parser


async def run_experiment_cli(args) -> None:
    """Main experiment logic."""
    from app.modules.backtest.config import BacktestTier, config_from_preset
    from app.modules.backtest.experiment import (
        ExperimentRunner,
        format_experiment_report,
        save_experiment_result,
    )
    from app.modules.backtest.noise_floor_store import NoiseFloorStore
    from app.modules.equities.config import EquitiesConfig

    end_date = date.fromisoformat(args.end_date)
    preset = BacktestTier(args.preset)
    config = config_from_preset(preset, args.branch, end_date=end_date)

    equities_config = config.equities_config_override or EquitiesConfig()
    agents_config = equities_config.agents

    store = NoiseFloorStore()

    # Resolve "live" to None for the engine (means use current skills directory)
    baseline_bundle = None if args.baseline_bundle == "live" else args.baseline_bundle
    treatment_bundle = None if args.treatment_bundle == "live" else args.treatment_bundle

    # Subclass ExperimentRunner to wire in the real backtest engine
    class CLIExperimentRunner(ExperimentRunner):
        async def _run_backtest(self, config, skills_bundle):
            from datetime import datetime

            from app.modules.backtest.context import BacktestContext, resolve_skills_bundle
            from app.modules.backtest.engine import BacktestEngine
            from app.modules.backtest.result_store import BacktestRun, hash_skill_bundle, save_run

            run_config = config.model_copy(update={
                "use_llm_response_cache": True,
                "skills_bundle": skills_bundle,
            })
            ctx = await BacktestContext.create(run_config)
            engine = BacktestEngine(ctx)
            result = await engine.run()

            # Build and return a BacktestRun
            skills_dir = resolve_skills_bundle(skills_bundle)
            sbh = hash_skill_bundle(skills_dir)
            import subprocess

            git_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, check=False,
            ).stdout.strip() or "unknown"

            timestamp = datetime.now()
            bundle_label = skills_bundle or "live"
            run_id = (
                f"{timestamp.strftime('%Y-%m-%dT%H-%M-%S')}"
                f"_{sbh[:12]}_{config.branch_name}"
            )
            run = BacktestRun(
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
            return run

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
    if args.json:
        output = json.dumps(result.to_json_dict(), indent=2)
    else:
        output = format_experiment_report(result)

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
```

### Step 11.4: Run the tests

```bash
.venv/bin/pytest tests/unit/backtest/test_run_experiment_script.py -q
```

### Step 11.5: Run linter

```bash
.venv/bin/ruff check scripts/run_experiment.py tests/unit/backtest/test_run_experiment_script.py
.venv/bin/ruff format scripts/run_experiment.py tests/unit/backtest/test_run_experiment_script.py
```

### Step 11.6: Commit

```bash
git add scripts/run_experiment.py tests/unit/backtest/test_run_experiment_script.py
git commit -m "$(cat <<'EOF'
Add run_experiment.py CLI for full experiment harness

Runs baseline + treatment backtests with cached LLM responses, looks up
noise floor, computes verdicts, prints formatted report. Supports
--t-correction, --report-out, --json flags.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Update `comparison.py` metric banner

**Files:**
- Modify: `app/modules/backtest/comparison.py`
- Test: `tests/unit/backtest/test_comparison.py`

### Step 12.1: Update the banner text

- [ ] **In `app/modules/backtest/comparison.py`**, replace the `_METRIC_BANNER` constant:

```python
_METRIC_BANNER = (
    "═══════════════════════════════════════════════════════════════════\n"
    "  RAW METRIC DELTAS — NO NOISE FLOOR, NO SIGNIFICANCE TESTING\n"
    "  A small delta may be indistinguishable from run-to-run noise.\n"
    "  Use `run_experiment` for per-metric verdict labels (LIKELY /\n"
    "  POSSIBLE / WITHIN NOISE) backed by a noise floor estimate.\n"
    "═══════════════════════════════════════════════════════════════════"
)
```

### Step 12.2: Update the test snapshot

- [ ] **In `tests/unit/backtest/test_comparison.py`**, update any test that asserts on the banner text to match the new wording. Search for `"Phase 3 (not yet shipped)"` and replace with `"run_experiment"` in the assertion.

### Step 12.3: Run comparison tests

```bash
.venv/bin/pytest tests/unit/backtest/test_comparison.py -q
```

### Step 12.4: Run linter

```bash
.venv/bin/ruff check app/modules/backtest/comparison.py
```

### Step 12.5: Commit

```bash
git add app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py
git commit -m "$(cat <<'EOF'
Update metric table banner to reference run_experiment

Phase 3 has shipped — replace the 'not yet shipped' language with a
pointer to the run_experiment CLI for verdict-labelled output.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Update architecture spec and CLAUDE.md

**Files:**
- Modify: `plans/architecture/LLM-BACKTEST-ATTRIBUTION.md`
- Modify: `CLAUDE.md`

### Step 13.1: Add Phase 3 implementation notes to the architecture spec

- [ ] **Add §8.8 after §8.7 in `plans/architecture/LLM-BACKTEST-ATTRIBUTION.md`**:

```markdown
### 8.8. Phase 3 Implementation Notes

Phase 3 was implemented per the implementation plan at `plans/implementation/2026-04-09-llm-backtest-attribution-phase3.md`. Deltas from the original spec:

- **Verdict thresholds support opt-in t-distribution correction** via `--t-correction` flag on `run_experiment.py`. Default remains fixed 1σ/2σ per the spec. The t-critical values are hardcoded for df=2..29 (no scipy dependency); beyond df=29, t ≈ z and the fixed thresholds apply. This was added because N=5 probe runs don't satisfy normality assumptions, and the t-correction is the conservative alternative.
- **`hash_experiment_config` takes an `AgentsConfig` parameter** in addition to `BacktestConfig`, because per-analyst model and temperature are on `AgentsConfig`, not `BacktestConfig`. The spec listed model/temperature as included fields but didn't specify how to access them. `initial_capital` was added to the hash (spec was ambiguous); `benchmark_symbols` was excluded (post-hoc computation, doesn't affect noise).
- **Cost estimation uses a per-analyst model lookup table** (`_COST_PER_CALL_BY_MODEL`) instead of a single flat rate. The probe_noise cost confirmation shows per-analyst model + cost breakdown. Unknown models fall back to `_DEFAULT_COST_PER_CALL = 0.012` with a warning note.
- **Low-sample footnote on verdict table** when N < 5 (e.g., `--runs 3`). The `--runs` flag has guardrails: min=3 (hard error), warn above 10 (diminishing returns). No "LOW CONFIDENCE" verdict label — a single footnote is cleaner.
- **`ExperimentRunner._run_backtest` is abstract** (raises `NotImplementedError`). The CLI scripts subclass it with real engine wiring. This avoids the runner importing heavy backtest infrastructure at module load time and makes unit testing trivial (mock the method).
```

### Step 13.2: Add Phase 3 CLI usage to `CLAUDE.md`

- [ ] **Add under the existing `# LLM-mode backtesting` section in `CLAUDE.md`**:

```markdown
# Phase 3 — variance probing and experiment harness
python -m scripts.probe_noise --preset medium --branch growth --end-date 2025-12-31 --runs 5 --yes
python -m scripts.run_experiment --preset medium --branch growth --end-date 2025-12-31 \
    --baseline-bundle baseline_v1 --treatment-bundle live
python -m scripts.run_experiment --preset quick --branch growth --end-date 2025-06-30 \
    --baseline-bundle baseline_v1 --treatment-bundle live --t-correction
```

### Step 13.3: Commit

```bash
git add plans/architecture/LLM-BACKTEST-ATTRIBUTION.md CLAUDE.md
git commit -m "$(cat <<'EOF'
Add Phase 3 implementation notes and CLI usage docs

Document deltas from spec: t-correction option, per-analyst cost
estimation, hash_experiment_config signature change, low-sample footnote,
abstract _run_backtest pattern.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Update project memory

**Files:**
- Modify: `~/.claude/projects/-Users-franco-lu-Desktop-ai-hedgefund-final/memory/llm_backtest_attribution_phase_ordering.md`

### Step 14.1: Update the memory entry

- [ ] Update the memory file to reflect Phase 3's planned status:

```markdown
---
name: LLM backtest attribution — project status
description: Phase 1 and 2 shipped, Phase 3 plan written and ready for implementation
type: project
---

LLM-mode backtest attribution project status (spec: `plans/architecture/LLM-BACKTEST-ATTRIBUTION.md`):

- **Phase 1** (reproducible LLM-mode backtests): SHIPPED in commit `30b431f` on 2026-04-07.
- **Phase 2** (comparison primitives + drill-down): SHIPPED in commit `7977001` on 2026-04-09.
- **Phase 3** (variance probing + experiment harness + statistical reporting): Implementation plan written at `plans/implementation/2026-04-09-llm-backtest-attribution-phase3.md`. Ready for implementation.

**Why:** Each phase informs the next. Phase 1's real signal data shaped Phase 2's drill-down design. Phase 2's raw delta output informed Phase 3's noise-floor thresholds and verdict design.

**How to apply:** Phase 3 plan is ready. When asked to implement, use the plan directly. Key design decisions: N=5 probe default, fixed 1σ/2σ verdicts with opt-in t-correction, per-analyst cost estimation, curated experiment JSON schema.
```

### Step 14.2: Commit (on feature branch only — do not commit memory on main)

This step is skipped if working on main. Memory file is updated but not staged for the main branch commit.

---

## Self-Review

Before handing off, verify:

**Spec coverage (§8 Phase 3):**
- §8.1 `statistics.py` dataclasses + `hash_experiment_config` → Task 2 ✓
- §8.1 `compute_metric_stats` + `compute_noise_floor` → Task 3 ✓
- §8.1 `compute_verdicts` + `format_verdict_table` → Task 4 ✓
- §8.1 verdict thresholds (LIKELY > 2σ, POSSIBLE 1–2σ, WITHIN NOISE ≤ 1σ) → Task 4 ✓
- §8.1 curated verdict metrics (total_return, annualized_return, sharpe, sortino, max_drawdown, win_rate, alpha) → Task 3 ✓
- §8.1 ZERO NOISE error when stddev == 0 → Task 4 ✓
- §8.2 `noise_floor_store.py` (SQLite, get/put/invalidate/list_all) → Task 6 ✓
- §8.3 `BacktestTier`, `TIER_PRESETS`, `config_from_preset` → Task 1 ✓
- §8.4 `ExperimentResult` + `ExperimentRunner` + `format_experiment_report` + `save_experiment_result` → Tasks 7, 8, 9 ✓
- §8.4 Missing noise floor → hard error with copy-pastable command → Task 8 ✓
- §8.4 Stale noise floor → warning, proceed → Task 8 ✓
- §8.4 Bundle mismatch → warning, proceed → Task 8 ✓
- §8.5 `scripts/probe_noise.py` (preset, --runs, --invalidate, --force, --yes, cost confirmation) → Task 10 ✓
- §8.6 `scripts/run_experiment.py` (preset, baseline/treatment bundles, --report-out) → Task 11 ✓
- §8.7 Sample report format (header, verdicts, drilldown, footer) → Task 7 ✓
- §12 Phase 3 testing strategy (~25 unit tests + mocked ExperimentRunner) → Tasks 2–11 ✓

**Design decisions from brainstorming locked in as code:**
- Probe count: N=5 default, min=3, warn >10, footnote when N<5 → Tasks 4, 10 ✓
- Verdict thresholds: fixed 1σ/2σ default + opt-in t-correction → Task 4 ✓
- `hash_experiment_config` fields: includes initial_capital/slippage/commission + per-analyst model/temp; excludes skills_bundle/cache/benchmarks → Task 2 ✓
- Staleness: warn-and-proceed for >30 days and bundle mismatch → Task 8 ✓
- Cost estimation: per-analyst model lookup table with fallback → Task 5 ✓
- Experiment JSON: curated schema, verdicts primary, runs by ID → Task 9 ✓

**Potential deltas to watch during implementation:**
- `BacktestResult.to_backtest_run()` — Task 10 assumes this helper exists. If it doesn't, extract the `BacktestRun` construction logic from `scripts/run_backtest.py`'s save path into a shared helper. Either add the method to `BacktestResult` or create a standalone `build_backtest_run(result, run_id, ...)` function.
- `resolve_skills_bundle` import path — Task 10 imports this from `app.modules.backtest.context`. Verify the actual function location in the codebase; Phase 1 may have placed it elsewhere.
- `BacktestConfig.model_copy(update={...})` — used in Tasks 10 and 11 to create modified configs. This is a Pydantic 2 method. Verify it exists and handles nested model fields correctly (especially `llm_config`).

**Placeholder scan:**
- No "TBD", "TODO", "implement later", or vague requirements.
- All test code is complete and runnable.
- All implementation code is complete.
- All commit messages use the HEREDOC + Co-Authored-By pattern.

**Type consistency:**
- `hash_experiment_config(config: BacktestConfig, agents_config: AgentsConfig) -> str` consistent across Tasks 2, 8, 10, 11.
- `compute_verdicts(cmp: RunComparison, nf: NoiseFloor, *, use_t_correction: bool = False) -> list[Verdict]` consistent across Tasks 4, 8.
- `format_verdict_table(verdicts, n_runs, *, t_correction=False) -> str` consistent across Tasks 4, 7.
- `estimate_experiment_cost(config, agents_config, n_runs) -> CostEstimate` consistent across Tasks 5, 10.
- `NoiseFloor` dataclass consistent across Tasks 3, 4, 6, 7, 8.
- `ExperimentResult` dataclass consistent across Tasks 7, 8, 9, 11.

**Tooling:**
- All `pytest` commands use `.venv/bin/pytest` per project convention.
- All `ruff` commands use `.venv/bin/ruff` per project convention.
- All commits use the HEREDOC pattern with `Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>` trailer.

**Commit message style:** Matches existing repo convention (imperative, no conventional-commits prefix, HEREDOC body, Co-Authored-By trailer).

**Ordering:** Tasks are linearized by dependency — Task 1 (config presets) is standalone, Tasks 2–5 build `statistics.py` incrementally, Task 6 (store) depends only on Task 3's dataclasses, Tasks 7–9 build `experiment.py` incrementally, Tasks 10–11 build CLI scripts using all prior modules, Task 12 updates Phase 2's banner, Tasks 13–14 finalize docs. Each task is self-contained and testable on completion.

---

## Out-of-Scope Reminders

These are **explicitly not part of this plan**. They are §13 future work:

- Auto-cleanup of stale LLM response cache entries.
- Cache sharing across machines (S3, NFS).
- Multi-variant tournaments (comparing K > 2 prompt variants).
- Continuous nightly experimentation infrastructure (cron wrapper around `run_experiment`).
- Bootstrap confidence intervals (replacing sigma thresholds with bootstrapped CIs).
- Skill versioning metadata in skill files (HTML-comment headers).

# LLM-Mode Backtest Attribution — Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After this phase, running `python -m scripts.compare_runs <baseline_id> <treatment_id>` produces a human-readable report showing exactly which performance metrics shifted (with raw deltas), which specific analyst signals diverged most strongly between the two prompt versions, and any universe drift between the runs. Running `python -m scripts.inspect_run <run_id>` produces a structured single-run summary that mirrors the print format from `scripts/run_backtest.py`.

**Architecture:** Add a new `app/modules/backtest/comparison.py` module containing `@dataclass` containers (`MetricDelta`, `SignalDivergence`, `UniverseDriftCell`, `RunComparison`) plus pure functions (`compare_runs`, `format_metric_table`, `format_signal_drilldown`, `format_drift_section`) and a `RunComparison.to_json_dict()` method that emits a curated JSON schema (not the full runs). Add two CLI scripts — `scripts/compare_runs.py` and `scripts/inspect_run.py` — that load saved `BacktestRun` JSONs via the existing `result_store.load_run` helper. Capture the effective per-analyst LLM config (`AgentsConfig`) into `BacktestRun` so the compatibility check can detect model/temperature drift between runs; this is the one plumbing change that leaks out of the comparison module into the engine and result store. Everything else is pure logic operating on already-persisted data.

**Tech Stack:** Python 3.12, `dataclasses` (stdlib), Pydantic 2 (for `BacktestRun` field addition only), pytest, existing `result_store`/`models`/`config` from Phase 1.

**Spec:** See `plans/architecture/LLM-BACKTEST-ATTRIBUTION.md` §7 for the Phase 2 component specification. Phase 1 shipped in commit `30b431f` ("Add reproducible LLM-mode backtests") and a real saved run is available at `data/backtest_runs/2026-04-08T16-05-14_281aa9f99eb5_growth.json` for fixture reference.

**Out of scope for this phase:** Phase 3 noise-floor and statistical verdict machinery (`noise_floor_store.py`, `statistics.py`, `experiment.py`, `scripts/probe_noise.py`, `scripts/run_experiment.py`, `BacktestTier` presets). This plan produces **raw delta reporting** only. A loud banner in the metric-table formatter names Phase 3 explicitly so future readers know where the verdict labels will eventually come from.

**Key design decisions** (resolved in brainstorming before writing this plan):

1. **Signal divergence ranking:** `impact = |score_delta| × max(baseline_confidence, treatment_confidence)`. A `--min-confidence N` CLI flag (default 0) filters the drilldown by a confidence floor. Rows where `score_delta == 0` are excluded from the default drilldown; a `--include-conviction-shifts` CLI flag merges them back in, ranked by `|conf_delta| × score`.
2. **Universe drift:** cells (keyed by `(date, symbol, analyst_type)`) that appear in one run but not the other are tracked as `UniverseDriftCell` objects and **excluded** from the divergence list. The default text output prints a one-line footer (count + percent of total signals). When drift exceeds **10% of total signals** a loud warning is emitted at the top of the report. A `--show-drift` CLI flag dumps the full drift list as a dedicated section. JSON output always includes the full drift list regardless of `--show-drift`.
3. **Metric verdict absence:** the metric table is rendered under a loud banner reading `RAW METRIC DELTAS — NO NOISE FLOOR, NO SIGNIFICANCE TESTING`. The delta column is labelled `ΔRAW` instead of `Delta`. No percent changes are shown. The banner is not replicated in JSON output (the consumer opted out of that in brainstorming).
4. **`inspect_run` scope:** default output is header + metrics + benchmarks + trade summary line + signal count breakdown + cache stats + hint footer. Trades and signals are opt-in via `--trades` and `--signals` flags (because real runs can have hundreds of each). Filters: `--symbol SYM` (applies to trades and signals), `--analyst-type TYPE` (signals only). `--signals-top N` dumps the first N signals only. `--snapshots` dumps per-day NAV snapshots.
5. **Output format:** text is the default. `--json` is mutually exclusive with text output. `compare_runs --json` emits a **curated schema** (no full `BacktestRun` duplication). `inspect_run --json` dumps the full `BacktestRun` Pydantic model.
6. **Compatibility check fields:** `start_date`, `end_date`, `top_n`, `rebalance_frequency`, `branch_name`, `llm_config.max_llm_calls_per_rebalance`, `git_sha` (separate "non-prompt code may have changed" warning), plus per-analyst `model` and `temperature` read from the new `effective_agents_config` field. Identical `skill_bundle_hash` between runs emits a loud "nothing to attribute" warning.

---

## File Structure Overview

### New files

| Path | Responsibility |
|---|---|
| `app/modules/backtest/comparison.py` | `@dataclass` containers (`MetricDelta`, `SignalDivergence`, `UniverseDriftCell`, `RunComparison`) + pure `compare_runs()` + formatters (`format_metric_table`, `format_signal_drilldown`, `format_drift_section`) + `RunComparison.to_json_dict()`. No I/O, no argparse, no filesystem. |
| `scripts/compare_runs.py` | CLI wrapper over `comparison.compare_runs`. Loads runs via `result_store.load_run`, applies CLI flags, dispatches to text or JSON rendering. Exit codes: 0 success, 1 run not found, 2 wholly incompatible date ranges. |
| `scripts/inspect_run.py` | CLI single-run drill-down. Loads a run via `result_store.load_run`, renders header + optional sections per flags. Exit codes: 0 success, 1 run not found. |
| `tests/unit/backtest/test_comparison.py` | Unit tests for all `comparison.py` functions and formatters. Uses hand-crafted `BacktestRun` fixtures from the conftest builder. |
| `tests/unit/backtest/test_compare_runs_script.py` | Unit tests for `scripts/compare_runs.py` CLI: argument parsing, exit codes, text vs JSON dispatch. |
| `tests/unit/backtest/test_inspect_run_script.py` | Unit tests for `scripts/inspect_run.py` CLI: default output, each flag combination, JSON output. |

### Modified files

| Path | Change |
|---|---|
| `app/modules/backtest/result_store.py` | Add `effective_agents_config: AgentsConfig \| None = None` field to `BacktestRun` (optional for backward compatibility with existing saved runs). |
| `app/modules/backtest/models.py` | Add `effective_agents_config: AgentsConfig \| None = None` field to `BacktestResult` so the engine can flow it through to the CLI save path. |
| `app/modules/backtest/engine.py` | Add `_collect_agents_config_from_context` static helper (mirrors `_collect_signals_from_context` pattern); wire it into the `BacktestResult(...)` construction. |
| `app/modules/backtest/context.py` | Stash `effective_config.agents` on `ctx.effective_agents_config` during setup so the engine collector can read it off the context. |
| `scripts/run_backtest.py` | Pass `effective_agents_config=result.effective_agents_config` into the `BacktestRun(...)` constructor when saving. |
| `tests/unit/backtest/conftest.py` | Add `_make_performance_metrics`, `_make_stock_signal_record`, and `_make_backtest_run` builders so comparison tests can hand-craft `BacktestRun` fixtures without boilerplate. |
| `tests/unit/backtest/test_result_store.py` | Add test asserting round-trip serialization of `effective_agents_config` field + backward-compat test for existing runs without the field. |
| `tests/unit/backtest/test_engine.py` | Add test asserting `_collect_agents_config_from_context` reads from `ctx.effective_agents_config` and that the engine result carries the field through. |
| `plans/architecture/LLM-BACKTEST-ATTRIBUTION.md` | Add §7.X implementation notes subsection (mirrors §6.10) documenting deltas from the spec: confidence fields on `SignalDivergence`, the spec-vs-reality resolution for `model`/`temperature`, and any other surprises. |

---

## Task 1: Add `effective_agents_config` field to `BacktestRun` and `BacktestResult`

This is the one plumbing change that has to happen before any comparison logic can be written. The field is optional so existing saved runs continue to load.

**Files:**
- Modify: `app/modules/backtest/result_store.py`
- Modify: `app/modules/backtest/models.py`
- Modify: `tests/unit/backtest/test_result_store.py`

### Step 1.1: Write the failing test for `BacktestRun.effective_agents_config`

- [ ] **Append to `tests/unit/backtest/test_result_store.py`**:

```python
class TestBacktestRunEffectiveAgentsConfig:
    """effective_agents_config captures the per-analyst LLM settings that actually
    ran, so compare_runs can detect model/temperature drift between runs."""

    def test_round_trip_with_agents_config(self, tmp_path) -> None:
        from app.modules.equities.config import AgentsConfig, AnalystLLMConfig

        agents = AgentsConfig(
            news_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
            fundamentals_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
            technical_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
        )
        run = BacktestRun(
            run_id="test_run_with_agents",
            timestamp=datetime(2026, 4, 8, 16, 5, 14),
            git_sha="abc123",
            config=BacktestConfig(
                start_date=date(2025, 1, 1),
                end_date=date(2025, 6, 30),
                branch_name="growth",
            ),
            skill_bundle_name=None,
            skill_bundle_hash="a" * 64,
            metrics=None,
            effective_agents_config=agents,
        )

        path = save_run(run, runs_dir=tmp_path)
        loaded = load_run("test_run_with_agents", runs_dir=tmp_path)

        assert loaded.effective_agents_config is not None
        assert loaded.effective_agents_config.news_analyst.model == "claude-sonnet-4-6"
        assert loaded.effective_agents_config.fundamentals_analyst.temperature == 0.3
        assert loaded.effective_agents_config.technical_analyst.model == "claude-sonnet-4-6"

    def test_legacy_run_without_agents_config_loads(self, tmp_path) -> None:
        """Existing saved runs from Phase 1 have no effective_agents_config field;
        they must still load, with the field defaulting to None."""
        legacy_json = """{
            "run_id": "legacy_run",
            "timestamp": "2026-04-08T16:05:14",
            "git_sha": "deadbeef",
            "config": {
                "start_date": "2025-01-01",
                "end_date": "2025-06-30",
                "branch_name": "growth"
            },
            "skill_bundle_name": null,
            "skill_bundle_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "metrics": null
        }"""
        (tmp_path / "legacy_run.json").write_text(legacy_json)

        loaded = load_run("legacy_run", runs_dir=tmp_path)
        assert loaded.effective_agents_config is None
```

### Step 1.2: Run the test to verify failure

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_result_store.py::TestBacktestRunEffectiveAgentsConfig -v`
- Expected: both tests fail with `TypeError: 'effective_agents_config' is not a valid field name` or an `AttributeError` on the `loaded` object.

### Step 1.3: Add the field to `BacktestRun` and `BacktestResult`

- [ ] **Modify `app/modules/backtest/result_store.py`** to import `AgentsConfig` and add the field:

```python
# Near the other imports at the top
from app.modules.equities.config import AgentsConfig

# Inside class BacktestRun(BaseModel): add after llm_cache_misses
class BacktestRun(BaseModel):
    # ... existing fields ...
    llm_cache_hits: int = 0
    llm_cache_misses: int = 0
    effective_agents_config: AgentsConfig | None = None
    """Per-analyst LLM config (model + temperature) that actually ran.

    Captured at end-of-run from BacktestContext.effective_agents_config. Lets
    compare_runs detect model/temperature drift between two saved runs that
    would otherwise look prompt-only. Defaults to None for backward compatibility
    with runs saved before this field existed; a None value is reported as an
    unverifiable compatibility check by compare_runs.
    """
```

- [ ] **Modify `app/modules/backtest/models.py`** to add the same field to `BacktestResult`:

```python
# Add import at the top (within TYPE_CHECKING or at runtime, matching existing style)
from app.modules.equities.config import AgentsConfig

# Inside class BacktestResult(BaseModel): add after llm_cache_misses
class BacktestResult(BaseModel):
    # ... existing fields ...
    llm_cache_hits: int = 0
    llm_cache_misses: int = 0
    effective_agents_config: AgentsConfig | None = None
```

Note: `AgentsConfig` is already a Pydantic BaseModel in `app/modules/equities/config.py` with default values for all fields, so adding it as a nullable field to two other Pydantic models is a pure-declaration change — no serialization helpers needed.

### Step 1.4: Run tests to verify they pass

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_result_store.py -v`
- Expected: all existing tests pass, plus the two new ones in `TestBacktestRunEffectiveAgentsConfig`.

### Step 1.5: Run linter

- [ ] Run: `.venv/bin/ruff check app/modules/backtest/result_store.py app/modules/backtest/models.py tests/unit/backtest/test_result_store.py`
- Expected: `All checks passed!`

### Step 1.6: Commit

- [ ] Run:

```bash
git add app/modules/backtest/result_store.py app/modules/backtest/models.py tests/unit/backtest/test_result_store.py
git commit -m "$(cat <<'EOF'
Add effective_agents_config field to BacktestRun and BacktestResult

Captures the per-analyst LLM settings (model, temperature) that actually ran
so Phase 2's compare_runs can detect drift between two saved runs that would
otherwise look prompt-only. Optional for backward compatibility with runs
saved before this field existed. See plans/architecture/LLM-BACKTEST-ATTRIBUTION.md
§7 and plans/implementation/2026-04-08-llm-backtest-attribution-phase2.md Task 1.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Populate `effective_agents_config` from `BacktestContext` through to saved runs

Now that the field exists, plumb it from the context (where `effective_config = config.equities_config_override or live_config` already resolves) through the engine result into the CLI save path.

**Files:**
- Modify: `app/modules/backtest/context.py`
- Modify: `app/modules/backtest/engine.py`
- Modify: `scripts/run_backtest.py`
- Modify: `tests/unit/backtest/test_engine.py`

### Step 2.1: Write failing test for the engine collector helper

- [ ] **Append to `tests/unit/backtest/test_engine.py`** (create the test class if the file doesn't already have a `TestBacktestEngineHelpers` class):

```python
class TestCollectAgentsConfigFromContext:
    def test_reads_effective_agents_config_from_context(self) -> None:
        from types import SimpleNamespace

        from app.modules.backtest.engine import BacktestEngine
        from app.modules.equities.config import AgentsConfig, AnalystLLMConfig

        agents = AgentsConfig(
            news_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
            fundamentals_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.5),
            technical_analyst=AnalystLLMConfig(model="claude-opus-4-6", temperature=0.2),
        )
        ctx = SimpleNamespace(effective_agents_config=agents)

        result = BacktestEngine._collect_agents_config_from_context(ctx)

        assert result is not None
        assert result.news_analyst.model == "claude-sonnet-4-6"
        assert result.fundamentals_analyst.temperature == 0.5
        assert result.technical_analyst.model == "claude-opus-4-6"

    def test_returns_none_when_attribute_missing(self) -> None:
        """Quantitative (non-LLM) backtests never set effective_agents_config;
        the collector must tolerate its absence."""
        from types import SimpleNamespace

        from app.modules.backtest.engine import BacktestEngine

        ctx = SimpleNamespace()
        result = BacktestEngine._collect_agents_config_from_context(ctx)
        assert result is None
```

### Step 2.2: Run the test to verify failure

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_engine.py::TestCollectAgentsConfigFromContext -v`
- Expected: `AttributeError: type object 'BacktestEngine' has no attribute '_collect_agents_config_from_context'`.

### Step 2.3: Implement the collector helper and wire it through

- [ ] **Modify `app/modules/backtest/engine.py`** — add a new static helper alongside `_collect_signals_from_context` and `_collect_cache_stats_from_context`:

```python
    @staticmethod
    def _collect_agents_config_from_context(ctx):
        """Return ctx.effective_agents_config if set, else None.

        Populated by BacktestContext.create() only in the use_llm_agents=True
        branch; returns None for quantitative backtests where per-analyst LLM
        config is not meaningful.
        """
        return getattr(ctx, "effective_agents_config", None)
```

- [ ] **Modify the `BacktestResult(...)` construction in `engine.py`** (currently near line 265) to pass the new field:

```python
        return BacktestResult(
            backtest_id=backtest_id or str(uuid.uuid4()),
            status=status,
            config=config.model_dump(mode="json"),
            metrics=metrics,
            snapshots=snapshots,
            trades=backtest_trades,
            benchmarks=benchmarks,
            rebalance_count=actual_rebalances,
            duration_seconds=duration,
            error_message=error_message,
            signals=BacktestEngine._collect_signals_from_context(ctx),
            llm_cache_hits=BacktestEngine._collect_cache_stats_from_context(ctx)[0],
            llm_cache_misses=BacktestEngine._collect_cache_stats_from_context(ctx)[1],
            effective_agents_config=BacktestEngine._collect_agents_config_from_context(ctx),
        )
```

- [ ] **Modify `app/modules/backtest/context.py`** — after the existing `effective_config = config.equities_config_override or live_config` line (currently line 227), stash the per-analyst section onto the context for the engine collector to read:

```python
        effective_config = config.equities_config_override or live_config
        # Expose effective per-analyst LLM config so BacktestEngine can record it
        # in BacktestResult.effective_agents_config for Phase 2 compare_runs.
        ctx.effective_agents_config = effective_config.agents
```

- [ ] **Modify `scripts/run_backtest.py`** — update the `BacktestRun(...)` constructor call inside the `if save:` block (currently near line 191) to pass through the new field:

```python
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
```

### Step 2.4: Run tests to verify they pass

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_engine.py::TestCollectAgentsConfigFromContext -v`
- Expected: 2 passed.

- [ ] Run the broader engine test suite to check nothing regressed: `.venv/bin/pytest tests/unit/backtest/test_engine.py -v`
- Expected: all previously-passing tests still pass.

### Step 2.5: Run linter

- [ ] Run: `.venv/bin/ruff check app/modules/backtest/engine.py app/modules/backtest/context.py scripts/run_backtest.py tests/unit/backtest/test_engine.py`
- Expected: `All checks passed!`

### Step 2.6: Commit

- [ ] Run:

```bash
git add app/modules/backtest/engine.py app/modules/backtest/context.py scripts/run_backtest.py tests/unit/backtest/test_engine.py
git commit -m "$(cat <<'EOF'
Capture effective_agents_config from backtest context into saved runs

BacktestContext stashes effective_config.agents on ctx during setup;
BacktestEngine._collect_agents_config_from_context reads it off the context
at end-of-run and populates BacktestResult.effective_agents_config; the CLI
save path propagates it into BacktestRun. Mirrors the existing signals/
cache-stats collector pattern. Enables Phase 2's compare_runs to detect
model/temperature drift between two saved runs.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add conftest builders for `BacktestRun` fixtures and scaffold `comparison.py`

Every comparison test needs to hand-craft two `BacktestRun` objects with known metrics, signals, and configs. Without shared builders each test file would be dominated by fixture boilerplate. This task adds the builders to `tests/unit/backtest/conftest.py` and creates an empty `comparison.py` module with just the dataclass definitions so subsequent tasks can TDD real behavior.

**Files:**
- Modify: `tests/unit/backtest/conftest.py`
- Create: `app/modules/backtest/comparison.py`
- Create: `tests/unit/backtest/test_comparison.py`

### Step 3.1: Write the failing tests for dataclass definitions

- [ ] **Create `tests/unit/backtest/test_comparison.py`** with:

```python
"""Unit tests for app/modules/backtest/comparison.py — Phase 2 run comparison."""
from __future__ import annotations

from dataclasses import fields
from datetime import date

import pytest

from app.modules.backtest.comparison import (
    MetricDelta,
    RunComparison,
    SignalDivergence,
    UniverseDriftCell,
)


class TestDataclassDefinitions:
    def test_metric_delta_fields(self) -> None:
        field_names = {f.name for f in fields(MetricDelta)}
        assert field_names == {"name", "baseline", "treatment", "delta"}

    def test_signal_divergence_fields(self) -> None:
        field_names = {f.name for f in fields(SignalDivergence)}
        assert field_names == {
            "date",
            "symbol",
            "analyst_type",
            "baseline_score",
            "treatment_score",
            "score_delta",
            "baseline_confidence",
            "treatment_confidence",
            "impact",
            "baseline_summary",
            "treatment_summary",
        }

    def test_universe_drift_cell_fields(self) -> None:
        field_names = {f.name for f in fields(UniverseDriftCell)}
        assert field_names == {"date", "symbol", "analyst_type", "present_in"}

    def test_run_comparison_fields(self) -> None:
        field_names = {f.name for f in fields(RunComparison)}
        assert field_names == {
            "baseline",
            "treatment",
            "metric_deltas",
            "signal_divergences",
            "conviction_shifts",
            "compatibility_warnings",
            "universe_drift_cells",
            "high_drift_warning",
        }
```

### Step 3.2: Run the test to verify failure

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_comparison.py -v`
- Expected: `ModuleNotFoundError: No module named 'app.modules.backtest.comparison'`.

### Step 3.3: Create `comparison.py` with dataclass scaffolds

- [ ] **Create `app/modules/backtest/comparison.py`** with:

```python
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
from datetime import date
from typing import Literal

from app.modules.backtest.result_store import BacktestRun


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
```

### Step 3.4: Add `BacktestRun` builders to the backtest conftest

- [ ] **Append to `tests/unit/backtest/conftest.py`**:

```python
from datetime import datetime

from app.modules.backtest.models import BenchmarkComparison, PerformanceMetrics
from app.modules.backtest.result_store import BacktestRun, StockSignalRecord
from app.modules.equities.config import AgentsConfig, AnalystLLMConfig


def _make_performance_metrics(**overrides) -> PerformanceMetrics:
    defaults = dict(
        total_return=0.05,
        annualized_return=0.12,
        volatility=0.18,
        sharpe_ratio=1.2,
        sortino_ratio=1.8,
        calmar_ratio=2.0,
        max_drawdown=0.06,
        max_drawdown_duration_days=14,
        total_trades=10,
        win_rate=0.6,
        profit_factor=1.5,
        avg_win=0.02,
        avg_loss=0.01,
        avg_position_count=5.0,
        max_position_count=8,
        avg_long_exposure=0.9,
        turnover_rate=3.0,
        value_at_risk_95=-0.015,
        conditional_var_95=-0.022,
        ulcer_index=0.03,
    )
    defaults.update(overrides)
    return PerformanceMetrics(**defaults)


def _make_benchmark_comparison(**overrides) -> BenchmarkComparison:
    defaults = dict(
        benchmark_symbol="SPY",
        benchmark_total_return=0.04,
        benchmark_annualized_return=0.10,
        benchmark_sharpe=1.1,
        benchmark_max_drawdown=0.05,
        alpha=0.02,
        beta=0.9,
        information_ratio=0.3,
        tracking_error=0.08,
        up_capture_ratio=95.0,
        down_capture_ratio=85.0,
    )
    defaults.update(overrides)
    return BenchmarkComparison(**defaults)


def _make_stock_signal_record(**overrides) -> StockSignalRecord:
    defaults = dict(
        date=date(2025, 6, 2),
        symbol="AMZN",
        analyst_type="fundamentals",
        bullish_score=7,
        confidence=6,
        summary="Strong earnings growth offset by elevated P/E; moderate conviction.",
    )
    defaults.update(overrides)
    return StockSignalRecord(**defaults)


def _make_agents_config(
    model: str = "claude-sonnet-4-6",
    temperature: float = 0.3,
) -> AgentsConfig:
    return AgentsConfig(
        news_analyst=AnalystLLMConfig(model=model, temperature=temperature),
        fundamentals_analyst=AnalystLLMConfig(model=model, temperature=temperature),
        technical_analyst=AnalystLLMConfig(model=model, temperature=temperature),
    )


def _make_backtest_run(**overrides) -> BacktestRun:
    defaults = dict(
        run_id="test_run",
        timestamp=datetime(2026, 4, 8, 16, 5, 14),
        git_sha="abc123def456",
        config=_make_backtest_config(),
        skill_bundle_name=None,
        skill_bundle_hash="a" * 64,
        metrics=_make_performance_metrics(),
        benchmarks=[_make_benchmark_comparison()],
        snapshots=[],
        trades=[],
        signals=[],
        llm_cache_hits=0,
        llm_cache_misses=0,
        effective_agents_config=_make_agents_config(),
    )
    defaults.update(overrides)
    return BacktestRun(**defaults)
```

### Step 3.5: Run tests to verify they pass

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_comparison.py -v`
- Expected: 4 passed.

### Step 3.6: Run linter

- [ ] Run: `.venv/bin/ruff check app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py tests/unit/backtest/conftest.py`
- Expected: `All checks passed!`

### Step 3.7: Commit

- [ ] Run:

```bash
git add app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py tests/unit/backtest/conftest.py
git commit -m "$(cat <<'EOF'
Scaffold comparison.py dataclasses and BacktestRun test builders

Empty RunComparison/MetricDelta/SignalDivergence/UniverseDriftCell @dataclasses
plus conftest helpers (_make_performance_metrics, _make_stock_signal_record,
_make_agents_config, _make_backtest_run) so subsequent Phase 2 tasks can
TDD real comparison behavior without fixture boilerplate.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Implement `compare_runs()` — compatibility check logic

First pass at `compare_runs()` that populates only `compatibility_warnings`. Metric deltas, signal divergences, and drift are added in later tasks. Splitting this way keeps each task's test set small and focused.

**Files:**
- Modify: `app/modules/backtest/comparison.py`
- Modify: `tests/unit/backtest/test_comparison.py`

### Step 4.1: Write failing tests for the compatibility check

- [ ] **Append to `tests/unit/backtest/test_comparison.py`**:

```python
from datetime import date, datetime

from app.modules.backtest.comparison import compare_runs
from tests.unit.backtest.conftest import (
    _make_agents_config,
    _make_backtest_config,
    _make_backtest_run,
)


class TestCompareRunsCompatibilityWarnings:
    def test_identical_configs_produce_no_warnings(self) -> None:
        baseline = _make_backtest_run(run_id="b", skill_bundle_hash="a" * 64)
        treatment = _make_backtest_run(run_id="t", skill_bundle_hash="b" * 64)
        cmp = compare_runs(baseline, treatment)
        assert cmp.compatibility_warnings == []

    def test_start_date_mismatch_warns(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            config=_make_backtest_config(start_date=date(2025, 1, 1)),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="t",
            config=_make_backtest_config(start_date=date(2025, 2, 1)),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        assert any("start_date" in w for w in cmp.compatibility_warnings)

    def test_end_date_mismatch_warns(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            config=_make_backtest_config(end_date=date(2025, 6, 30)),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="t",
            config=_make_backtest_config(end_date=date(2025, 7, 31)),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        assert any("end_date" in w for w in cmp.compatibility_warnings)

    def test_top_n_mismatch_warns(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            config=_make_backtest_config(top_n=20),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="t",
            config=_make_backtest_config(top_n=50),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        assert any("top_n" in w for w in cmp.compatibility_warnings)

    def test_rebalance_frequency_mismatch_warns(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            config=_make_backtest_config(rebalance_frequency="weekly"),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="t",
            config=_make_backtest_config(rebalance_frequency="monthly"),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        assert any("rebalance_frequency" in w for w in cmp.compatibility_warnings)

    def test_branch_name_mismatch_warns(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            config=_make_backtest_config(branch_name="growth"),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="t",
            config=_make_backtest_config(branch_name="value"),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        assert any("branch_name" in w for w in cmp.compatibility_warnings)

    def test_max_llm_calls_per_rebalance_mismatch_warns(self) -> None:
        from app.modules.backtest.config import LLMBacktestConfig

        baseline = _make_backtest_run(
            run_id="b",
            config=_make_backtest_config(llm_config=LLMBacktestConfig(max_llm_calls_per_rebalance=60)),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="t",
            config=_make_backtest_config(llm_config=LLMBacktestConfig(max_llm_calls_per_rebalance=90)),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        assert any("max_llm_calls_per_rebalance" in w for w in cmp.compatibility_warnings)

    def test_git_sha_mismatch_warns(self) -> None:
        baseline = _make_backtest_run(run_id="b", git_sha="aaa111", skill_bundle_hash="a" * 64)
        treatment = _make_backtest_run(run_id="t", git_sha="bbb222", skill_bundle_hash="b" * 64)
        cmp = compare_runs(baseline, treatment)
        assert any("git_sha" in w and "non-prompt code" in w for w in cmp.compatibility_warnings)

    def test_identical_skill_bundle_hash_warns_nothing_to_attribute(self) -> None:
        baseline = _make_backtest_run(run_id="b", skill_bundle_hash="c" * 64)
        treatment = _make_backtest_run(run_id="t", skill_bundle_hash="c" * 64)
        cmp = compare_runs(baseline, treatment)
        assert any("nothing to attribute" in w for w in cmp.compatibility_warnings)

    def test_model_mismatch_warns_per_analyst(self) -> None:
        from app.modules.equities.config import AgentsConfig, AnalystLLMConfig

        baseline = _make_backtest_run(
            run_id="b",
            skill_bundle_hash="a" * 64,
            effective_agents_config=_make_agents_config(model="claude-sonnet-4-6"),
        )
        treatment_agents = AgentsConfig(
            news_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
            fundamentals_analyst=AnalystLLMConfig(model="claude-opus-4-6", temperature=0.3),
            technical_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
        )
        treatment = _make_backtest_run(
            run_id="t",
            skill_bundle_hash="b" * 64,
            effective_agents_config=treatment_agents,
        )
        cmp = compare_runs(baseline, treatment)
        assert any("fundamentals_analyst.model" in w for w in cmp.compatibility_warnings)

    def test_temperature_mismatch_warns_per_analyst(self) -> None:
        from app.modules.equities.config import AgentsConfig, AnalystLLMConfig

        baseline = _make_backtest_run(
            run_id="b",
            skill_bundle_hash="a" * 64,
            effective_agents_config=_make_agents_config(temperature=0.3),
        )
        treatment_agents = AgentsConfig(
            news_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.7),
            fundamentals_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
            technical_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
        )
        treatment = _make_backtest_run(
            run_id="t",
            skill_bundle_hash="b" * 64,
            effective_agents_config=treatment_agents,
        )
        cmp = compare_runs(baseline, treatment)
        assert any("news_analyst.temperature" in w for w in cmp.compatibility_warnings)

    def test_legacy_run_without_agents_config_emits_unverifiable_warning(self) -> None:
        baseline = _make_backtest_run(
            run_id="b", skill_bundle_hash="a" * 64, effective_agents_config=None
        )
        treatment = _make_backtest_run(run_id="t", skill_bundle_hash="b" * 64)
        cmp = compare_runs(baseline, treatment)
        assert any("cannot verify model/temperature" in w for w in cmp.compatibility_warnings)
```

### Step 4.2: Run the tests to verify failure

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_comparison.py::TestCompareRunsCompatibilityWarnings -v`
- Expected: `ImportError: cannot import name 'compare_runs' from 'app.modules.backtest.comparison'`.

### Step 4.3: Implement the compatibility check

- [ ] **Append to `app/modules/backtest/comparison.py`**:

```python
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


def compare_runs(baseline: BacktestRun, treatment: BacktestRun) -> RunComparison:
    """Compare two BacktestRun instances and return a RunComparison.

    Pure function: no I/O, no mutation of the inputs. Run this on already-loaded
    runs; use result_store.load_run() to hydrate runs from disk first.

    This initial scaffold populates only compatibility_warnings. Metric deltas,
    signal divergences, and universe drift are filled in by subsequent Phase 2
    tasks.
    """
    return RunComparison(
        baseline=baseline,
        treatment=treatment,
        compatibility_warnings=_compatibility_warnings(baseline, treatment),
    )
```

### Step 4.4: Run tests to verify they pass

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_comparison.py -v`
- Expected: 4 dataclass tests + 12 compatibility tests = 16 passed.

### Step 4.5: Run linter

- [ ] Run: `.venv/bin/ruff check app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py`
- Expected: `All checks passed!`

### Step 4.6: Commit

- [ ] Run:

```bash
git add app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py
git commit -m "$(cat <<'EOF'
Implement compare_runs compatibility check

Detects mismatches in start_date/end_date/top_n/rebalance_frequency/
branch_name/max_llm_calls_per_rebalance and per-analyst model+temperature,
plus a 'nothing to attribute' warning when skill_bundle_hashes match and a
'non-prompt code may have changed' warning when git_shas differ. Mismatches
are warnings, not errors — compare_runs proceeds with any two runs and
surfaces the caveats in the report header.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Implement metric delta computation

Add `metric_deltas` population to `compare_runs()`. Covers both the `PerformanceMetrics` fields and per-benchmark `BenchmarkComparison` fields, in a deterministic order.

**Files:**
- Modify: `app/modules/backtest/comparison.py`
- Modify: `tests/unit/backtest/test_comparison.py`

### Step 5.1: Write failing tests

- [ ] **Append to `tests/unit/backtest/test_comparison.py`**:

```python
class TestCompareRunsMetricDeltas:
    def test_metric_deltas_for_performance_metrics(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            metrics=_make_performance_metrics(
                total_return=0.05, sharpe_ratio=1.2, max_drawdown=0.06
            ),
        )
        treatment = _make_backtest_run(
            run_id="t",
            metrics=_make_performance_metrics(
                total_return=0.07, sharpe_ratio=1.35, max_drawdown=0.055
            ),
        )
        cmp = compare_runs(baseline, treatment)

        deltas_by_name = {d.name: d for d in cmp.metric_deltas}
        assert deltas_by_name["total_return"].delta == pytest.approx(0.02)
        assert deltas_by_name["sharpe_ratio"].delta == pytest.approx(0.15)
        assert deltas_by_name["max_drawdown"].delta == pytest.approx(-0.005)

    def test_metric_deltas_preserve_display_order(self) -> None:
        """Order matters for the rendered table. Returns → risk → activity → exposure."""
        baseline = _make_backtest_run(run_id="b")
        treatment = _make_backtest_run(run_id="t")
        cmp = compare_runs(baseline, treatment)
        names = [d.name for d in cmp.metric_deltas]
        # Assert a few anchors rather than the full list (keeps the test flexible).
        assert names.index("total_return") < names.index("sharpe_ratio")
        assert names.index("sharpe_ratio") < names.index("max_drawdown")
        assert names.index("max_drawdown") < names.index("total_trades")

    def test_metric_deltas_include_benchmarks(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            benchmarks=[_make_benchmark_comparison(benchmark_symbol="SPY", alpha=0.02, beta=0.9)],
        )
        treatment = _make_backtest_run(
            run_id="t",
            benchmarks=[_make_benchmark_comparison(benchmark_symbol="SPY", alpha=0.035, beta=0.88)],
        )
        cmp = compare_runs(baseline, treatment)
        deltas_by_name = {d.name: d for d in cmp.metric_deltas}
        assert deltas_by_name["SPY.alpha"].delta == pytest.approx(0.015)
        assert deltas_by_name["SPY.beta"].delta == pytest.approx(-0.02)

    def test_metric_deltas_skip_when_metrics_none(self) -> None:
        baseline = _make_backtest_run(run_id="b", metrics=None)
        treatment = _make_backtest_run(run_id="t", metrics=None)
        cmp = compare_runs(baseline, treatment)
        # When both metrics are None, no deltas are produced — comparison still succeeds.
        assert cmp.metric_deltas == []

    def test_metric_deltas_skip_benchmarks_with_mismatched_symbols(self) -> None:
        """Benchmarks are keyed by symbol; a benchmark present in only one run is skipped."""
        baseline = _make_backtest_run(
            run_id="b",
            benchmarks=[_make_benchmark_comparison(benchmark_symbol="SPY")],
        )
        treatment = _make_backtest_run(
            run_id="t",
            benchmarks=[_make_benchmark_comparison(benchmark_symbol="VOOG")],
        )
        cmp = compare_runs(baseline, treatment)
        deltas_by_name = {d.name for d in cmp.metric_deltas}
        # Neither SPY.* nor VOOG.* deltas should appear — no intersection.
        assert not any(n.startswith("SPY.") or n.startswith("VOOG.") for n in deltas_by_name)

    def test_metric_deltas_need_helper_import(self) -> None:
        # Ensures _make_benchmark_comparison is importable from the conftest module.
        from tests.unit.backtest.conftest import _make_benchmark_comparison, _make_performance_metrics  # noqa: F401
```

### Step 5.2: Import the builders at the top of the test file

- [ ] **Update the imports at the top of `tests/unit/backtest/test_comparison.py`** to include `_make_benchmark_comparison` and `_make_performance_metrics`:

```python
from tests.unit.backtest.conftest import (
    _make_agents_config,
    _make_backtest_config,
    _make_backtest_run,
    _make_benchmark_comparison,
    _make_performance_metrics,
)
```

### Step 5.3: Run the tests to verify failure

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_comparison.py::TestCompareRunsMetricDeltas -v`
- Expected: tests fail because `cmp.metric_deltas` is still empty.

### Step 5.4: Implement metric delta computation

- [ ] **Update `app/modules/backtest/comparison.py`** — add the metric delta helper and wire it into `compare_runs()`:

```python
# Ordered list of PerformanceMetrics fields to include in the metric table.
# Groups: returns → risk → trading activity → exposure. Skip `warnings` (list field).
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


def compare_runs(baseline: BacktestRun, treatment: BacktestRun) -> RunComparison:
    """Compare two BacktestRun instances and return a RunComparison."""
    return RunComparison(
        baseline=baseline,
        treatment=treatment,
        compatibility_warnings=_compatibility_warnings(baseline, treatment),
        metric_deltas=_metric_deltas(baseline, treatment),
    )
```

### Step 5.5: Run tests to verify they pass

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_comparison.py -v`
- Expected: all tests pass including the new `TestCompareRunsMetricDeltas` class.

### Step 5.6: Run linter

- [ ] Run: `.venv/bin/ruff check app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py`
- Expected: `All checks passed!`

### Step 5.7: Commit

- [ ] Run:

```bash
git add app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py
git commit -m "$(cat <<'EOF'
Implement compare_runs metric delta computation

Populates RunComparison.metric_deltas with raw delta (treatment - baseline)
for every PerformanceMetrics field plus per-benchmark BenchmarkComparison
fields (joined by symbol). Ordered returns → risk → activity → exposure for
later rendering. Handles metrics=None on either side (empty deltas list) and
benchmarks with non-intersecting symbols (skipped silently).

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Implement signal divergence computation (core path)

Default behavior: for each `(date, symbol, analyst_type)` key present in BOTH runs, if `score_delta != 0`, emit a `SignalDivergence` with `impact = |score_delta| * max(conf)`. Sort descending. No filters yet — those come in Task 7.

**Files:**
- Modify: `app/modules/backtest/comparison.py`
- Modify: `tests/unit/backtest/test_comparison.py`

### Step 6.1: Write failing tests

- [ ] **Append to `tests/unit/backtest/test_comparison.py`**:

```python
from tests.unit.backtest.conftest import _make_stock_signal_record


class TestCompareRunsSignalDivergences:
    def test_disagreeing_signals_produce_divergence(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2),
                    symbol="AMZN",
                    analyst_type="fundamentals",
                    bullish_score=7,
                    confidence=5,
                    summary="baseline view",
                )
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2),
                    symbol="AMZN",
                    analyst_type="fundamentals",
                    bullish_score=8,
                    confidence=7,
                    summary="treatment view",
                )
            ],
        )
        cmp = compare_runs(baseline, treatment)
        assert len(cmp.signal_divergences) == 1
        div = cmp.signal_divergences[0]
        assert div.symbol == "AMZN"
        assert div.score_delta == 1
        assert div.baseline_score == 7
        assert div.treatment_score == 8
        assert div.baseline_confidence == 5
        assert div.treatment_confidence == 7
        assert div.impact == 7  # |1| * max(5, 7) == 7
        assert div.baseline_summary == "baseline view"
        assert div.treatment_summary == "treatment view"

    def test_matching_signals_excluded_from_divergences(self) -> None:
        identical = _make_stock_signal_record(
            date=date(2025, 6, 2),
            symbol="AMZN",
            analyst_type="fundamentals",
            bullish_score=7,
            confidence=5,
        )
        baseline = _make_backtest_run(run_id="b", signals=[identical])
        treatment = _make_backtest_run(run_id="t", signals=[identical])
        cmp = compare_runs(baseline, treatment)
        assert cmp.signal_divergences == []

    def test_score_delta_zero_excluded_by_default(self) -> None:
        """Confidence-only shifts are NOT in the default divergences list."""
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2),
                    symbol="AMZN",
                    analyst_type="fundamentals",
                    bullish_score=7,
                    confidence=3,
                )
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2),
                    symbol="AMZN",
                    analyst_type="fundamentals",
                    bullish_score=7,
                    confidence=9,
                )
            ],
        )
        cmp = compare_runs(baseline, treatment)
        assert cmp.signal_divergences == []
        # Conviction shifts live in a separate list populated by Task 7.
        assert cmp.conviction_shifts == []

    def test_divergences_sorted_by_impact_descending(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AAA", analyst_type="technical",
                    bullish_score=5, confidence=5,
                ),
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="BBB", analyst_type="technical",
                    bullish_score=5, confidence=5,
                ),
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="CCC", analyst_type="technical",
                    bullish_score=5, confidence=5,
                ),
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AAA", analyst_type="technical",
                    bullish_score=6, confidence=5,  # impact 1*5=5
                ),
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="BBB", analyst_type="technical",
                    bullish_score=8, confidence=9,  # impact 3*9=27
                ),
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="CCC", analyst_type="technical",
                    bullish_score=7, confidence=8,  # impact 2*8=16
                ),
            ],
        )
        cmp = compare_runs(baseline, treatment)
        symbols_in_order = [d.symbol for d in cmp.signal_divergences]
        assert symbols_in_order == ["BBB", "CCC", "AAA"]

    def test_divergences_skip_cells_present_in_only_one_run(self) -> None:
        """Cells present in only one run are drift, not divergence."""
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=5,
                ),
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="GOOGL", analyst_type="fundamentals",
                    bullish_score=8, confidence=6,
                ),
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=6, confidence=5,
                ),
                # GOOGL missing in treatment
            ],
        )
        cmp = compare_runs(baseline, treatment)
        assert len(cmp.signal_divergences) == 1
        assert cmp.signal_divergences[0].symbol == "AMZN"
```

### Step 6.2: Update imports at the top of `test_comparison.py`

- [ ] **Update the imports** in `tests/unit/backtest/test_comparison.py`:

```python
from tests.unit.backtest.conftest import (
    _make_agents_config,
    _make_backtest_config,
    _make_backtest_run,
    _make_benchmark_comparison,
    _make_performance_metrics,
    _make_stock_signal_record,
)
```

### Step 6.3: Run the tests to verify failure

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_comparison.py::TestCompareRunsSignalDivergences -v`
- Expected: tests fail because `cmp.signal_divergences` is still empty.

### Step 6.4: Implement signal divergence computation

- [ ] **Append to `app/modules/backtest/comparison.py`**:

```python
def _index_signals_by_key(
    run: BacktestRun,
) -> dict[tuple[date, str, str], "StockSignalRecord"]:
    """Return a dict keyed by (date, symbol, analyst_type) for fast join."""
    from app.modules.backtest.result_store import StockSignalRecord  # noqa: F401

    return {(s.date, s.symbol, s.analyst_type): s for s in run.signals}


def _signal_divergences(
    baseline: BacktestRun,
    treatment: BacktestRun,
) -> list[SignalDivergence]:
    """Return SignalDivergence rows for every (date, symbol, analyst_type) cell
    where both runs have a signal AND the bullish_score differs.

    Sorted by impact descending. Cells missing in one run are skipped (handled
    by _universe_drift_cells in a later task). Rows with score_delta == 0 are
    excluded from this list (they appear in conviction_shifts if enabled).
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
        impact = abs(score_delta) * max(b_sig.confidence, t_sig.confidence)
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
```

- [ ] **Update `compare_runs()`** to call the new helper:

```python
def compare_runs(baseline: BacktestRun, treatment: BacktestRun) -> RunComparison:
    """Compare two BacktestRun instances and return a RunComparison."""
    return RunComparison(
        baseline=baseline,
        treatment=treatment,
        compatibility_warnings=_compatibility_warnings(baseline, treatment),
        metric_deltas=_metric_deltas(baseline, treatment),
        signal_divergences=_signal_divergences(baseline, treatment),
    )
```

### Step 6.5: Run tests to verify they pass

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_comparison.py -v`
- Expected: all tests pass.

### Step 6.6: Run linter

- [ ] Run: `.venv/bin/ruff check app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py`
- Expected: `All checks passed!`

### Step 6.7: Commit

- [ ] Run:

```bash
git add app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py
git commit -m "$(cat <<'EOF'
Implement compare_runs signal divergence computation

Joins the two runs' signals by (date, symbol, analyst_type) and emits a
SignalDivergence for every cell where bullish_score differs. Impact is
|score_delta| * max(baseline_confidence, treatment_confidence) per Phase 2
design. Sorted impact-desc so the highest-conviction disagreements appear
first. Rows with score_delta=0 are excluded; cells present in only one run
are left for the universe drift helper in a later task.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Add signal divergence filters — `min_confidence` and conviction shifts

Extend `compare_runs()` with two optional parameters: `min_confidence` (filters `signal_divergences` by a max-confidence floor) and `include_conviction_shifts` (populates `conviction_shifts` with rows where `score_delta == 0` but confidence moved, ranked by `|conf_delta| * score`).

**Files:**
- Modify: `app/modules/backtest/comparison.py`
- Modify: `tests/unit/backtest/test_comparison.py`

### Step 7.1: Write failing tests

- [ ] **Append to `tests/unit/backtest/test_comparison.py`**:

```python
class TestCompareRunsFilters:
    def test_min_confidence_filter_drops_low_conviction_rows(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AAA", analyst_type="technical",
                    bullish_score=5, confidence=3,
                ),
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="BBB", analyst_type="technical",
                    bullish_score=5, confidence=8,
                ),
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AAA", analyst_type="technical",
                    bullish_score=7, confidence=3,
                ),
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="BBB", analyst_type="technical",
                    bullish_score=6, confidence=8,
                ),
            ],
        )
        cmp_no_filter = compare_runs(baseline, treatment)
        assert {d.symbol for d in cmp_no_filter.signal_divergences} == {"AAA", "BBB"}

        cmp_filtered = compare_runs(baseline, treatment, min_confidence=5)
        assert {d.symbol for d in cmp_filtered.signal_divergences} == {"BBB"}

    def test_conviction_shifts_default_empty(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    bullish_score=7, confidence=3,
                )
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    bullish_score=7, confidence=9,
                )
            ],
        )
        cmp = compare_runs(baseline, treatment)
        assert cmp.conviction_shifts == []

    def test_include_conviction_shifts_populates_list(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=3,
                    summary="low conviction baseline",
                )
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=9,
                    summary="high conviction treatment",
                )
            ],
        )
        cmp = compare_runs(baseline, treatment, include_conviction_shifts=True)
        assert len(cmp.conviction_shifts) == 1
        shift = cmp.conviction_shifts[0]
        assert shift.score_delta == 0
        assert shift.baseline_confidence == 3
        assert shift.treatment_confidence == 9
        assert shift.impact == 42  # |9 - 3| * 7 == 42

    def test_conviction_shifts_zero_conf_delta_excluded(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[_make_stock_signal_record(bullish_score=7, confidence=5)],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[_make_stock_signal_record(bullish_score=7, confidence=5)],
        )
        cmp = compare_runs(baseline, treatment, include_conviction_shifts=True)
        assert cmp.conviction_shifts == []
```

### Step 7.2: Run the tests to verify failure

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_comparison.py::TestCompareRunsFilters -v`
- Expected: `TypeError: compare_runs() got an unexpected keyword argument 'min_confidence'`.

### Step 7.3: Implement the filters

- [ ] **Update `app/modules/backtest/comparison.py`** — refactor `_signal_divergences` into two helpers and add the filter params to `compare_runs()`:

```python
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


def compare_runs(
    baseline: BacktestRun,
    treatment: BacktestRun,
    *,
    min_confidence: int = 0,
    include_conviction_shifts: bool = False,
) -> RunComparison:
    """Compare two BacktestRun instances and return a RunComparison.

    Args:
        baseline: reference run to diff against
        treatment: run being evaluated
        min_confidence: drop divergences where max(baseline_conf, treatment_conf)
            is below this threshold. Default 0 (no filter).
        include_conviction_shifts: when True, populate RunComparison.conviction_shifts
            with rows where score_delta == 0 but confidence moved, ranked by
            |conf_delta| * score.
    """
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
    )
```

### Step 7.4: Run tests to verify they pass

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_comparison.py -v`
- Expected: all tests pass including the new filter tests.

### Step 7.5: Run linter

- [ ] Run: `.venv/bin/ruff check app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py`
- Expected: `All checks passed!`

### Step 7.6: Commit

- [ ] Run:

```bash
git add app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py
git commit -m "$(cat <<'EOF'
Add min_confidence filter and conviction shift tracking to compare_runs

min_confidence=N drops divergences where max(baseline_conf, treatment_conf)
is below the floor. include_conviction_shifts=True populates
RunComparison.conviction_shifts with score_delta=0-but-conf-moved rows,
ranked by |conf_delta| * score. Both default off so existing behavior is
unchanged.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Implement universe drift computation

Populate `universe_drift_cells` with cells present in only one run, and set `high_drift_warning` when the drift count exceeds 10% of total signals.

**Files:**
- Modify: `app/modules/backtest/comparison.py`
- Modify: `tests/unit/backtest/test_comparison.py`

### Step 8.1: Write failing tests

- [ ] **Append to `tests/unit/backtest/test_comparison.py`**:

```python
class TestCompareRunsUniverseDrift:
    def test_cells_in_baseline_only_become_drift_cells(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=5,
                ),
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="GOOGL", analyst_type="fundamentals",
                    bullish_score=8, confidence=6,
                ),
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=5,
                ),
            ],
        )
        cmp = compare_runs(baseline, treatment)
        assert len(cmp.universe_drift_cells) == 1
        drift = cmp.universe_drift_cells[0]
        assert drift.symbol == "GOOGL"
        assert drift.present_in == "baseline"

    def test_cells_in_treatment_only_become_drift_cells(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=5,
                ),
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=5,
                ),
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="MSFT", analyst_type="technical",
                    bullish_score=6, confidence=7,
                ),
            ],
        )
        cmp = compare_runs(baseline, treatment)
        assert len(cmp.universe_drift_cells) == 1
        drift = cmp.universe_drift_cells[0]
        assert drift.symbol == "MSFT"
        assert drift.present_in == "treatment"
        assert drift.analyst_type == "technical"

    def test_no_drift_leaves_lists_empty(self) -> None:
        sig = _make_stock_signal_record(
            date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
            bullish_score=7, confidence=5,
        )
        baseline = _make_backtest_run(run_id="b", signals=[sig])
        treatment = _make_backtest_run(run_id="t", signals=[sig])
        cmp = compare_runs(baseline, treatment)
        assert cmp.universe_drift_cells == []
        assert cmp.high_drift_warning is None

    def test_high_drift_above_10_percent_sets_warning(self) -> None:
        # 1 shared signal + 1 baseline-only + 1 treatment-only = 3 total signals;
        # 2 drift cells / 3 total = 66.6% → warning should fire.
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(symbol="AAA", analyst_type="technical",
                                          bullish_score=5, confidence=5),
                _make_stock_signal_record(symbol="BBB", analyst_type="technical",
                                          bullish_score=5, confidence=5),
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(symbol="AAA", analyst_type="technical",
                                          bullish_score=6, confidence=5),
                _make_stock_signal_record(symbol="CCC", analyst_type="technical",
                                          bullish_score=5, confidence=5),
            ],
        )
        cmp = compare_runs(baseline, treatment)
        assert len(cmp.universe_drift_cells) == 2
        assert cmp.high_drift_warning is not None
        assert "HIGH DRIFT" in cmp.high_drift_warning
        assert "%" in cmp.high_drift_warning

    def test_low_drift_below_10_percent_no_warning(self) -> None:
        # 9 shared + 1 drift = 10 total → 10% → NOT above 10% → no warning.
        shared_symbols = [f"S{i:02d}" for i in range(9)]
        baseline_signals = [
            _make_stock_signal_record(
                symbol=sym, analyst_type="technical",
                bullish_score=5, confidence=5,
            )
            for sym in shared_symbols
        ]
        baseline_signals.append(
            _make_stock_signal_record(
                symbol="DRIFT", analyst_type="technical",
                bullish_score=5, confidence=5,
            )
        )
        treatment_signals = [
            _make_stock_signal_record(
                symbol=sym, analyst_type="technical",
                bullish_score=6, confidence=5,  # score differs so they count as shared divergences
            )
            for sym in shared_symbols
        ]
        baseline = _make_backtest_run(run_id="b", signals=baseline_signals)
        treatment = _make_backtest_run(run_id="t", signals=treatment_signals)
        cmp = compare_runs(baseline, treatment)
        # Drift = 1 cell, total signals = 10 (9 shared union + 1 baseline-only),
        # drift_pct = 10.0% → NOT strictly above 10% → no warning.
        assert len(cmp.universe_drift_cells) == 1
        assert cmp.high_drift_warning is None
```

### Step 8.2: Run the tests to verify failure

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_comparison.py::TestCompareRunsUniverseDrift -v`
- Expected: tests fail because `universe_drift_cells` is still empty and `high_drift_warning` is None by default but counts are off.

### Step 8.3: Implement drift computation

- [ ] **Append to `app/modules/backtest/comparison.py`**:

```python
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
```

- [ ] **Update `compare_runs()`** to call the new helpers:

```python
def compare_runs(
    baseline: BacktestRun,
    treatment: BacktestRun,
    *,
    min_confidence: int = 0,
    include_conviction_shifts: bool = False,
) -> RunComparison:
    """..."""
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
```

### Step 8.4: Run tests to verify they pass

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_comparison.py -v`
- Expected: all tests pass including the new drift tests.

### Step 8.5: Run linter

- [ ] Run: `.venv/bin/ruff check app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py`
- Expected: `All checks passed!`

### Step 8.6: Commit

- [ ] Run:

```bash
git add app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py
git commit -m "$(cat <<'EOF'
Implement universe drift detection in compare_runs

Populates RunComparison.universe_drift_cells with (date, symbol, analyst_type)
cells present in exactly one run. When drift exceeds 10% of total unique
signals, sets high_drift_warning to a loud human-readable message naming
the --show-drift CLI flag as the remediation. Drift cells are tracked
separately from divergences so structural missingness doesn't get
misattributed to prompt effects.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Implement `format_metric_table()` with ΔRAW banner

Render the metric deltas as a human-readable text block with the loud "no noise floor" banner at the top and the `ΔRAW` column label.

**Files:**
- Modify: `app/modules/backtest/comparison.py`
- Modify: `tests/unit/backtest/test_comparison.py`

### Step 9.1: Write failing tests

- [ ] **Append to `tests/unit/backtest/test_comparison.py`**:

```python
from app.modules.backtest.comparison import format_metric_table


class TestFormatMetricTable:
    def test_includes_loud_banner(self) -> None:
        baseline = _make_backtest_run(run_id="b")
        treatment = _make_backtest_run(run_id="t")
        cmp = compare_runs(baseline, treatment)
        output = format_metric_table(cmp)
        assert "RAW METRIC DELTAS" in output
        assert "NO NOISE FLOOR" in output
        assert "NO SIGNIFICANCE TESTING" in output
        assert "Phase 3" in output

    def test_uses_delta_raw_column_label(self) -> None:
        baseline = _make_backtest_run(run_id="b")
        treatment = _make_backtest_run(run_id="t")
        cmp = compare_runs(baseline, treatment)
        output = format_metric_table(cmp)
        assert "ΔRAW" in output

    def test_does_not_include_percent_change_column(self) -> None:
        """Percent changes invite causal interpretation — omit entirely."""
        baseline = _make_backtest_run(run_id="b")
        treatment = _make_backtest_run(run_id="t")
        cmp = compare_runs(baseline, treatment)
        output = format_metric_table(cmp)
        # No '%change' column header; the banner may contain '%' but the column row shouldn't.
        header_line = next(
            (ln for ln in output.splitlines() if "ΔRAW" in ln),
            "",
        )
        assert "%" not in header_line

    def test_empty_metrics_prints_placeholder(self) -> None:
        baseline = _make_backtest_run(run_id="b", metrics=None)
        treatment = _make_backtest_run(run_id="t", metrics=None)
        cmp = compare_runs(baseline, treatment)
        output = format_metric_table(cmp)
        assert "no metrics" in output.lower() or "no deltas" in output.lower()

    def test_renders_each_metric_row(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            metrics=_make_performance_metrics(total_return=0.05, sharpe_ratio=1.2),
        )
        treatment = _make_backtest_run(
            run_id="t",
            metrics=_make_performance_metrics(total_return=0.07, sharpe_ratio=1.35),
        )
        cmp = compare_runs(baseline, treatment)
        output = format_metric_table(cmp)
        assert "total_return" in output
        assert "sharpe_ratio" in output
```

### Step 9.2: Run the tests to verify failure

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_comparison.py::TestFormatMetricTable -v`
- Expected: `ImportError: cannot import name 'format_metric_table'`.

### Step 9.3: Implement the formatter

- [ ] **Append to `app/modules/backtest/comparison.py`**:

```python
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
```

### Step 9.4: Run tests to verify they pass

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_comparison.py::TestFormatMetricTable -v`
- Expected: 5 passed.

### Step 9.5: Run linter

- [ ] Run: `.venv/bin/ruff check app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py`
- Expected: `All checks passed!`

### Step 9.6: Commit

- [ ] Run:

```bash
git add app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py
git commit -m "$(cat <<'EOF'
Implement format_metric_table with loud no-noise-floor banner

Renders RunComparison.metric_deltas as a fixed-width text block prefixed by
a 5-line banner reading 'RAW METRIC DELTAS — NO NOISE FLOOR, NO SIGNIFICANCE
TESTING' and naming Phase 3 as the future source of verdict labels. Delta
column is labelled ΔRAW (not 'Delta') to reinforce at the row level that
these values have not been significance-tested. No percent changes are
shown — raw absolute deltas only.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Implement `format_signal_drilldown()`

Render the sorted signal divergences as a block of detailed divergence entries with full verbatim analyst summaries. Handle empty, top-N truncation, and optional conviction-shift subsection.

**Files:**
- Modify: `app/modules/backtest/comparison.py`
- Modify: `tests/unit/backtest/test_comparison.py`

### Step 10.1: Write failing tests

- [ ] **Append to `tests/unit/backtest/test_comparison.py`**:

```python
from app.modules.backtest.comparison import format_signal_drilldown


class TestFormatSignalDrilldown:
    def test_empty_divergences_prints_placeholder(self) -> None:
        baseline = _make_backtest_run(run_id="b")
        treatment = _make_backtest_run(run_id="t")
        cmp = compare_runs(baseline, treatment)
        output = format_signal_drilldown(cmp)
        assert "no signal divergences" in output.lower()

    def test_renders_divergence_fields(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=5,
                    summary="baseline says moderately bullish",
                )
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=8, confidence=7,
                    summary="treatment says more bullish after margin expansion",
                )
            ],
        )
        cmp = compare_runs(baseline, treatment)
        output = format_signal_drilldown(cmp)
        assert "AMZN" in output
        assert "fundamentals" in output
        assert "2025-06-02" in output
        assert "7" in output and "8" in output  # scores
        assert "baseline says moderately bullish" in output
        assert "treatment says more bullish after margin expansion" in output

    def test_honors_top_n_truncation(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    symbol=f"S{i}", analyst_type="technical",
                    bullish_score=5, confidence=5,
                )
                for i in range(30)
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    symbol=f"S{i}", analyst_type="technical",
                    bullish_score=6, confidence=5,
                )
                for i in range(30)
            ],
        )
        cmp = compare_runs(baseline, treatment)
        output = format_signal_drilldown(cmp, top_n=5)
        # Only 5 divergence blocks should be rendered; count by looking for
        # a distinctive per-row marker like "Score:" or the symbol prefix.
        displayed_symbols = [f"S{i}" for i in range(30) if f" S{i} " in output or f"S{i},".strip(",") in output]
        # We at least expect significantly fewer than 30 to appear.
        assert len(displayed_symbols) <= 10
        # And a truncation notice should be present.
        assert "showing top" in output.lower() or "truncated" in output.lower()

    def test_conviction_shifts_subsection_when_nonempty(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="GOOGL", analyst_type="technical",
                    bullish_score=6, confidence=3,
                    summary="cautious baseline",
                )
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="GOOGL", analyst_type="technical",
                    bullish_score=6, confidence=9,
                    summary="confident treatment",
                )
            ],
        )
        cmp = compare_runs(baseline, treatment, include_conviction_shifts=True)
        output = format_signal_drilldown(cmp)
        assert "CONVICTION SHIFTS" in output.upper() or "conviction shift" in output.lower()
        assert "GOOGL" in output
```

### Step 10.2: Run the tests to verify failure

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_comparison.py::TestFormatSignalDrilldown -v`
- Expected: `ImportError: cannot import name 'format_signal_drilldown'`.

### Step 10.3: Implement the formatter

- [ ] **Append to `app/modules/backtest/comparison.py`**:

```python
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
```

### Step 10.4: Run tests to verify they pass

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_comparison.py -v`
- Expected: all tests pass including `TestFormatSignalDrilldown`.

### Step 10.5: Run linter

- [ ] Run: `.venv/bin/ruff check app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py`
- Expected: `All checks passed!`

### Step 10.6: Commit

- [ ] Run:

```bash
git add app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py
git commit -m "$(cat <<'EOF'
Implement format_signal_drilldown with verbatim analyst summaries

Renders RunComparison.signal_divergences as impact-sorted blocks with full
verbatim baseline and treatment summaries. Honors top_n truncation (default
20) with a 'showing top N of M' header when truncating. When conviction
shifts are present, renders them in a separate subsection below the main
drilldown.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Implement drift section formatter + `RunComparison.to_json_dict()`

The drift footer (always shown) renders the count+percent of drifted cells. The optional `--show-drift` detail dumps the full list. The JSON method emits the curated Phase 2 schema.

**Files:**
- Modify: `app/modules/backtest/comparison.py`
- Modify: `tests/unit/backtest/test_comparison.py`

### Step 11.1: Write failing tests

- [ ] **Append to `tests/unit/backtest/test_comparison.py`**:

```python
from app.modules.backtest.comparison import format_drift_section


class TestFormatDriftSection:
    def test_empty_drift_shows_no_drift_footer(self) -> None:
        baseline = _make_backtest_run(run_id="b")
        treatment = _make_backtest_run(run_id="t")
        cmp = compare_runs(baseline, treatment)
        output = format_drift_section(cmp, show_drift=False)
        assert "no drift" in output.lower() or "0 cells" in output.lower()

    def test_drift_footer_shows_count_and_percent(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(symbol="AAA", bullish_score=5, confidence=5),
                _make_stock_signal_record(symbol="BBB", bullish_score=5, confidence=5),
                _make_stock_signal_record(symbol="CCC", bullish_score=5, confidence=5),
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(symbol="AAA", bullish_score=5, confidence=5),
                _make_stock_signal_record(symbol="BBB", bullish_score=5, confidence=5),
                _make_stock_signal_record(symbol="DDD", bullish_score=5, confidence=5),
            ],
        )
        cmp = compare_runs(baseline, treatment)
        output = format_drift_section(cmp, show_drift=False)
        assert "2" in output  # 2 drift cells (CCC baseline-only, DDD treatment-only)
        assert "%" in output

    def test_show_drift_dumps_full_list(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(symbol="BASELINE_ONLY", bullish_score=5, confidence=5),
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(symbol="TREATMENT_ONLY", bullish_score=5, confidence=5),
            ],
        )
        cmp = compare_runs(baseline, treatment)
        output = format_drift_section(cmp, show_drift=True)
        assert "BASELINE_ONLY" in output
        assert "TREATMENT_ONLY" in output


class TestRunComparisonToJsonDict:
    def test_curated_schema_has_expected_keys(self) -> None:
        baseline = _make_backtest_run(run_id="b", skill_bundle_hash="a" * 64)
        treatment = _make_backtest_run(run_id="t", skill_bundle_hash="b" * 64)
        cmp = compare_runs(baseline, treatment)
        d = cmp.to_json_dict()
        expected_keys = {
            "baseline_run_id",
            "treatment_run_id",
            "baseline_skill_bundle_hash",
            "treatment_skill_bundle_hash",
            "generated_at",
            "compatibility_warnings",
            "metric_deltas",
            "signal_divergences",
            "conviction_shifts",
            "universe_drift",
        }
        assert set(d.keys()) == expected_keys
        assert d["baseline_run_id"] == "b"
        assert d["treatment_run_id"] == "t"

    def test_json_dict_does_not_duplicate_full_runs(self) -> None:
        """Consumers should join by run_id; the curated schema must not embed
        the full BacktestRun payloads on top of signal_divergences."""
        baseline = _make_backtest_run(run_id="b")
        treatment = _make_backtest_run(run_id="t")
        cmp = compare_runs(baseline, treatment)
        d = cmp.to_json_dict()
        assert "baseline" not in d
        assert "treatment" not in d
        assert "snapshots" not in d  # definitely shouldn't be in there

    def test_json_dict_drift_always_populated(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[_make_stock_signal_record(symbol="BASELINE_ONLY")],
        )
        treatment = _make_backtest_run(run_id="t", signals=[])
        cmp = compare_runs(baseline, treatment)
        d = cmp.to_json_dict()
        assert d["universe_drift"]["count"] == 1
        assert len(d["universe_drift"]["cells"]) == 1
        assert d["universe_drift"]["cells"][0]["symbol"] == "BASELINE_ONLY"

    def test_json_dict_divergence_entries_include_confidences(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[_make_stock_signal_record(bullish_score=7, confidence=5)],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[_make_stock_signal_record(bullish_score=8, confidence=7)],
        )
        cmp = compare_runs(baseline, treatment)
        d = cmp.to_json_dict()
        assert len(d["signal_divergences"]) == 1
        div = d["signal_divergences"][0]
        assert div["baseline_confidence"] == 5
        assert div["treatment_confidence"] == 7
        assert div["impact"] == 7  # |1| * max(5, 7) == 7
```

### Step 11.2: Run the tests to verify failure

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_comparison.py::TestFormatDriftSection tests/unit/backtest/test_comparison.py::TestRunComparisonToJsonDict -v`
- Expected: `ImportError: cannot import name 'format_drift_section'` and `AttributeError: 'RunComparison' object has no attribute 'to_json_dict'`.

### Step 11.3: Implement `format_drift_section` and `to_json_dict`

- [ ] **Append to `app/modules/backtest/comparison.py`**:

```python
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
    from datetime import datetime

    total_signals = _total_unique_signal_keys(cmp.baseline, cmp.treatment)
    drift_pct = (
        (len(cmp.universe_drift_cells) / total_signals * 100) if total_signals > 0 else 0.0
    )
    return {
        "baseline_run_id": cmp.baseline.run_id,
        "treatment_run_id": cmp.treatment.run_id,
        "baseline_skill_bundle_hash": cmp.baseline.skill_bundle_hash,
        "treatment_skill_bundle_hash": cmp.treatment.skill_bundle_hash,
        "generated_at": datetime.utcnow().isoformat(),
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
```

- [ ] **Add the `to_json_dict` method to `RunComparison`** — modify the dataclass definition in `comparison.py` to include it:

```python
@dataclass
class RunComparison:
    # ... existing fields ...
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
```

### Step 11.4: Run tests to verify they pass

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_comparison.py -v`
- Expected: all tests pass.

### Step 11.5: Run linter

- [ ] Run: `.venv/bin/ruff check app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py`
- Expected: `All checks passed!`

### Step 11.6: Commit

- [ ] Run:

```bash
git add app/modules/backtest/comparison.py tests/unit/backtest/test_comparison.py
git commit -m "$(cat <<'EOF'
Add format_drift_section and RunComparison.to_json_dict

format_drift_section renders a one-line footer by default (count + percent
of total signals) and a full baseline-only / treatment-only listing when
show_drift=True. RunComparison.to_json_dict emits the curated Phase 2 schema
(not a full BacktestRun dump) with metric_deltas, signal_divergences,
conviction_shifts, universe_drift, and compatibility_warnings. Consumers who
need the full runs can re-load them by run_id.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Create `scripts/compare_runs.py` CLI

Wrap the comparison module in an argparse CLI. Dispatch to text or JSON output, handle exit codes, call the formatters in order.

**Files:**
- Create: `scripts/compare_runs.py`
- Create: `tests/unit/backtest/test_compare_runs_script.py`

### Step 12.1: Write failing tests

- [ ] **Create `tests/unit/backtest/test_compare_runs_script.py`** with:

```python
"""Unit tests for scripts/compare_runs.py — Phase 2 CLI."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.modules.backtest.result_store import save_run
from scripts import compare_runs as compare_runs_script
from tests.unit.backtest.conftest import (
    _make_backtest_config,
    _make_backtest_run,
    _make_stock_signal_record,
)


class TestCompareRunsCLI:
    def test_missing_baseline_run_exits_1(self, tmp_path: Path, capsys) -> None:
        # Only save the treatment run.
        treatment = _make_backtest_run(run_id="treatment1", skill_bundle_hash="b" * 64)
        save_run(treatment, runs_dir=tmp_path)

        exit_code = compare_runs_script.main(
            ["baseline_does_not_exist", "treatment1", "--runs-dir", str(tmp_path)]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "baseline_does_not_exist" in captured.err

    def test_missing_treatment_run_exits_1(self, tmp_path: Path, capsys) -> None:
        baseline = _make_backtest_run(run_id="baseline1", skill_bundle_hash="a" * 64)
        save_run(baseline, runs_dir=tmp_path)

        exit_code = compare_runs_script.main(
            ["baseline1", "treatment_missing", "--runs-dir", str(tmp_path)]
        )
        assert exit_code == 1

    def test_wholly_incompatible_dates_exits_2(self, tmp_path: Path) -> None:
        baseline = _make_backtest_run(
            run_id="baseline_old",
            config=_make_backtest_config(
                start_date=date(2024, 1, 1), end_date=date(2024, 6, 30)
            ),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="treatment_new",
            config=_make_backtest_config(
                start_date=date(2025, 1, 1), end_date=date(2025, 6, 30)
            ),
            skill_bundle_hash="b" * 64,
        )
        save_run(baseline, runs_dir=tmp_path)
        save_run(treatment, runs_dir=tmp_path)

        exit_code = compare_runs_script.main(
            ["baseline_old", "treatment_new", "--runs-dir", str(tmp_path)]
        )
        assert exit_code == 2

    def test_happy_path_exits_0_with_text_output(self, tmp_path: Path, capsys) -> None:
        baseline = _make_backtest_run(
            run_id="baseline_happy",
            skill_bundle_hash="a" * 64,
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=5,
                )
            ],
        )
        treatment = _make_backtest_run(
            run_id="treatment_happy",
            skill_bundle_hash="b" * 64,
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=8, confidence=7,
                )
            ],
        )
        save_run(baseline, runs_dir=tmp_path)
        save_run(treatment, runs_dir=tmp_path)

        exit_code = compare_runs_script.main(
            ["baseline_happy", "treatment_happy", "--runs-dir", str(tmp_path)]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        # Banner + metric table + drilldown + drift footer should all appear.
        assert "RAW METRIC DELTAS" in captured.out
        assert "SIGNAL DRILLDOWN" in captured.out
        assert "UNIVERSE DRIFT" in captured.out
        assert "AMZN" in captured.out

    def test_json_flag_emits_curated_json_only(self, tmp_path: Path, capsys) -> None:
        baseline = _make_backtest_run(run_id="b_json", skill_bundle_hash="a" * 64)
        treatment = _make_backtest_run(run_id="t_json", skill_bundle_hash="b" * 64)
        save_run(baseline, runs_dir=tmp_path)
        save_run(treatment, runs_dir=tmp_path)

        exit_code = compare_runs_script.main(
            ["b_json", "t_json", "--runs-dir", str(tmp_path), "--json"]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        # Output must be valid JSON with no text banner leaking in.
        data = json.loads(captured.out)
        assert data["baseline_run_id"] == "b_json"
        assert data["treatment_run_id"] == "t_json"
        assert "RAW METRIC DELTAS" not in captured.out

    def test_metrics_only_flag_suppresses_drilldown(
        self, tmp_path: Path, capsys
    ) -> None:
        baseline = _make_backtest_run(
            run_id="b_mo",
            skill_bundle_hash="a" * 64,
            signals=[_make_stock_signal_record(bullish_score=7)],
        )
        treatment = _make_backtest_run(
            run_id="t_mo",
            skill_bundle_hash="b" * 64,
            signals=[_make_stock_signal_record(bullish_score=9)],
        )
        save_run(baseline, runs_dir=tmp_path)
        save_run(treatment, runs_dir=tmp_path)

        exit_code = compare_runs_script.main(
            ["b_mo", "t_mo", "--runs-dir", str(tmp_path), "--metrics-only"]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "RAW METRIC DELTAS" in captured.out
        assert "SIGNAL DRILLDOWN" not in captured.out

    def test_min_confidence_flag_passes_through(
        self, tmp_path: Path, capsys
    ) -> None:
        baseline = _make_backtest_run(
            run_id="b_mc",
            skill_bundle_hash="a" * 64,
            signals=[
                _make_stock_signal_record(
                    symbol="LOW_CONF", bullish_score=5, confidence=3
                )
            ],
        )
        treatment = _make_backtest_run(
            run_id="t_mc",
            skill_bundle_hash="b" * 64,
            signals=[
                _make_stock_signal_record(
                    symbol="LOW_CONF", bullish_score=7, confidence=3
                )
            ],
        )
        save_run(baseline, runs_dir=tmp_path)
        save_run(treatment, runs_dir=tmp_path)

        exit_code = compare_runs_script.main(
            ["b_mc", "t_mc", "--runs-dir", str(tmp_path), "--min-confidence", "5"]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        # The divergence should have been filtered out; drilldown says no divergences.
        assert "no signal divergences" in captured.out.lower()
```

### Step 12.2: Run the tests to verify failure

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_compare_runs_script.py -v`
- Expected: `ModuleNotFoundError: No module named 'scripts.compare_runs'`.

### Step 12.3: Create the CLI

- [ ] **Create `scripts/compare_runs.py`** with:

```python
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
```

### Step 12.4: Run tests to verify they pass

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_compare_runs_script.py -v`
- Expected: all tests pass.

### Step 12.5: Run linter

- [ ] Run: `.venv/bin/ruff check scripts/compare_runs.py tests/unit/backtest/test_compare_runs_script.py`
- Expected: `All checks passed!`

### Step 12.6: Commit

- [ ] Run:

```bash
git add scripts/compare_runs.py tests/unit/backtest/test_compare_runs_script.py
git commit -m "$(cat <<'EOF'
Add scripts/compare_runs.py CLI for Phase 2 attribution reports

Wraps app.modules.backtest.comparison with argparse + exit codes: 0 success,
1 run not found, 2 wholly incompatible date ranges. Text output (default)
emits banner → compatibility warnings → metric table → signal drilldown →
drift footer. --json emits the curated Phase 2 schema (no full BacktestRun
duplication). Flags: --top-n, --metrics-only, --min-confidence,
--include-conviction-shifts, --show-drift, --json, --runs-dir.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Create `scripts/inspect_run.py` — default text rendering

Default output: header (run id, timestamp, git SHA), config block, metrics, benchmarks, trade summary line, signal count breakdown, cache stats, hint footer.

**Files:**
- Create: `scripts/inspect_run.py`
- Create: `tests/unit/backtest/test_inspect_run_script.py`

### Step 13.1: Write failing tests

- [ ] **Create `tests/unit/backtest/test_inspect_run_script.py`** with:

```python
"""Unit tests for scripts/inspect_run.py — Phase 2 single-run drilldown CLI."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.modules.backtest.models import BacktestTrade
from app.modules.backtest.result_store import save_run
from scripts import inspect_run as inspect_run_script
from tests.unit.backtest.conftest import (
    _make_backtest_run,
    _make_backtest_trade,
    _make_stock_signal_record,
)


class TestInspectRunDefault:
    def test_missing_run_exits_1(self, tmp_path: Path, capsys) -> None:
        exit_code = inspect_run_script.main(
            ["does_not_exist", "--runs-dir", str(tmp_path)]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "does_not_exist" in captured.err

    def test_default_output_has_header_metrics_signal_summary(
        self, tmp_path: Path, capsys
    ) -> None:
        run = _make_backtest_run(
            run_id="inspected_run",
            signals=[
                _make_stock_signal_record(
                    symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=5,
                ),
                _make_stock_signal_record(
                    symbol="AMZN", analyst_type="technical",
                    bullish_score=6, confidence=6,
                ),
                _make_stock_signal_record(
                    symbol="GOOGL", analyst_type="fundamentals",
                    bullish_score=8, confidence=6,
                ),
            ],
            llm_cache_hits=2,
            llm_cache_misses=1,
        )
        save_run(run, runs_dir=tmp_path)

        exit_code = inspect_run_script.main(
            ["inspected_run", "--runs-dir", str(tmp_path)]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        out = captured.out
        assert "inspected_run" in out
        assert "Config:" in out
        assert "Metrics:" in out
        assert "Signals:" in out
        assert "fundamentals: 2" in out or "fundamentals:  2" in out
        assert "technical:" in out
        assert "LLM cache" in out.lower() or "cache:" in out.lower()
        assert "2" in out and "1" in out  # cache hits/misses

    def test_default_output_does_not_dump_full_signals(
        self, tmp_path: Path, capsys
    ) -> None:
        run = _make_backtest_run(
            run_id="nosignals",
            signals=[
                _make_stock_signal_record(
                    symbol="AMZN", summary="very long verbatim analyst summary here"
                )
            ],
        )
        save_run(run, runs_dir=tmp_path)
        exit_code = inspect_run_script.main(
            ["nosignals", "--runs-dir", str(tmp_path)]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "very long verbatim analyst summary here" not in captured.out

    def test_default_output_shows_trade_summary_line_not_full_list(
        self, tmp_path: Path, capsys
    ) -> None:
        trades = [
            _make_backtest_trade(trade_date=date(2025, 6, 2), symbol="AAA", side="buy"),
            _make_backtest_trade(trade_date=date(2025, 6, 2), symbol="BBB", side="buy"),
            _make_backtest_trade(trade_date=date(2025, 6, 9), symbol="CCC", side="sell"),
        ]
        run = _make_backtest_run(run_id="tradesrun", trades=trades)
        save_run(run, runs_dir=tmp_path)

        exit_code = inspect_run_script.main(
            ["tradesrun", "--runs-dir", str(tmp_path)]
        )
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Trades:" in out
        assert "3 total" in out or "3" in out.split("Trades:")[1].split("\n")[0]
        assert "buy: 2" in out
        assert "sell: 1" in out
        # Full trade rows should NOT appear.
        assert "2025-06-02 buy" not in out
```

### Step 13.2: Run the tests to verify failure

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_inspect_run_script.py -v`
- Expected: `ModuleNotFoundError: No module named 'scripts.inspect_run'`.

### Step 13.3: Create the CLI skeleton and default text path

- [ ] **Create `scripts/inspect_run.py`** with:

```python
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
import json
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
    per_analyst = Counter(s.analyst_type for s in signals)
    per_analyst_per_symbol: dict[str, Counter] = {}
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
        help="Directory containing saved BacktestRun JSON files (default: data/backtest_runs)",
    )
    args = parser.parse_args(argv)

    try:
        run = load_run(args.run_id, runs_dir=args.runs_dir)
    except FileNotFoundError as e:
        print(f"ERROR: run not found: {e}", file=sys.stderr)
        return 1

    _print_default_report(run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

### Step 13.4: Run tests to verify they pass

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_inspect_run_script.py -v`
- Expected: all tests pass.

### Step 13.5: Run linter

- [ ] Run: `.venv/bin/ruff check scripts/inspect_run.py tests/unit/backtest/test_inspect_run_script.py`
- Expected: `All checks passed!`

### Step 13.6: Commit

- [ ] Run:

```bash
git add scripts/inspect_run.py tests/unit/backtest/test_inspect_run_script.py
git commit -m "$(cat <<'EOF'
Add scripts/inspect_run.py default text rendering

Single-run drill-down that prints header + config block + metrics +
benchmarks + trade summary line + per-analyst signal count breakdown +
LLM cache stats + footer hint. Trades and signals are summarized (not
dumped) by default because real runs can have hundreds of each; opt-in
flags follow in a later task. Exit codes: 0 success, 1 run not found.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Add `inspect_run` detail flags and JSON output

Add `--trades`, `--signals`, `--signals-top N`, `--snapshots`, `--symbol`, `--analyst-type`, `--json`. Wire them into the existing CLI skeleton.

**Files:**
- Modify: `scripts/inspect_run.py`
- Modify: `tests/unit/backtest/test_inspect_run_script.py`

### Step 14.1: Write failing tests

- [ ] **Append to `tests/unit/backtest/test_inspect_run_script.py`**:

```python
class TestInspectRunDetailFlags:
    def test_trades_flag_dumps_full_list(self, tmp_path: Path, capsys) -> None:
        trades = [
            _make_backtest_trade(trade_date=date(2025, 6, 2), symbol="AAA", side="buy"),
            _make_backtest_trade(trade_date=date(2025, 6, 2), symbol="BBB", side="buy"),
        ]
        run = _make_backtest_run(run_id="trades_full", trades=trades)
        save_run(run, runs_dir=tmp_path)

        exit_code = inspect_run_script.main(
            ["trades_full", "--runs-dir", str(tmp_path), "--trades"]
        )
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "AAA" in out
        assert "BBB" in out
        assert "2025-06-02" in out

    def test_signals_flag_dumps_full_verbatim_summaries(
        self, tmp_path: Path, capsys
    ) -> None:
        run = _make_backtest_run(
            run_id="sig_full",
            signals=[
                _make_stock_signal_record(
                    symbol="AMZN",
                    summary="EXACT VERBATIM SUMMARY TEXT MARKER",
                )
            ],
        )
        save_run(run, runs_dir=tmp_path)
        exit_code = inspect_run_script.main(
            ["sig_full", "--runs-dir", str(tmp_path), "--signals"]
        )
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "EXACT VERBATIM SUMMARY TEXT MARKER" in out

    def test_signals_top_n_limits_output(self, tmp_path: Path, capsys) -> None:
        signals = [
            _make_stock_signal_record(
                symbol=f"S{i}", analyst_type="technical",
                summary=f"summary for S{i}",
            )
            for i in range(10)
        ]
        run = _make_backtest_run(run_id="sig_topn", signals=signals)
        save_run(run, runs_dir=tmp_path)

        exit_code = inspect_run_script.main(
            [
                "sig_topn",
                "--runs-dir",
                str(tmp_path),
                "--signals",
                "--signals-top",
                "3",
            ]
        )
        assert exit_code == 0
        out = capsys.readouterr().out
        # Only 3 of 10 should appear in the rendered summaries.
        appearing = sum(1 for i in range(10) if f"summary for S{i}" in out)
        assert appearing == 3

    def test_symbol_filter_on_signals(self, tmp_path: Path, capsys) -> None:
        run = _make_backtest_run(
            run_id="sym_filter",
            signals=[
                _make_stock_signal_record(symbol="AMZN", summary="amzn_marker"),
                _make_stock_signal_record(symbol="GOOGL", summary="googl_marker"),
            ],
        )
        save_run(run, runs_dir=tmp_path)
        exit_code = inspect_run_script.main(
            [
                "sym_filter",
                "--runs-dir",
                str(tmp_path),
                "--signals",
                "--symbol",
                "GOOGL",
            ]
        )
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "googl_marker" in out
        assert "amzn_marker" not in out

    def test_analyst_type_filter_on_signals(self, tmp_path: Path, capsys) -> None:
        run = _make_backtest_run(
            run_id="at_filter",
            signals=[
                _make_stock_signal_record(
                    analyst_type="fundamentals", summary="fund_marker"
                ),
                _make_stock_signal_record(
                    analyst_type="technical", summary="tech_marker"
                ),
            ],
        )
        save_run(run, runs_dir=tmp_path)
        exit_code = inspect_run_script.main(
            [
                "at_filter",
                "--runs-dir",
                str(tmp_path),
                "--signals",
                "--analyst-type",
                "fundamentals",
            ]
        )
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "fund_marker" in out
        assert "tech_marker" not in out

    def test_json_flag_dumps_full_backtest_run(self, tmp_path: Path, capsys) -> None:
        run = _make_backtest_run(
            run_id="json_full",
            signals=[_make_stock_signal_record(symbol="AMZN", summary="json_sig")],
        )
        save_run(run, runs_dir=tmp_path)

        exit_code = inspect_run_script.main(
            ["json_full", "--runs-dir", str(tmp_path), "--json"]
        )
        assert exit_code == 0
        out = capsys.readouterr().out
        import json as json_lib

        parsed = json_lib.loads(out)
        assert parsed["run_id"] == "json_full"
        # Full BacktestRun dump includes signals, metrics, snapshots.
        assert parsed["signals"][0]["summary"] == "json_sig"

    def test_json_flag_with_symbol_filter_filters_signals_array(
        self, tmp_path: Path, capsys
    ) -> None:
        run = _make_backtest_run(
            run_id="json_sym",
            signals=[
                _make_stock_signal_record(symbol="AMZN", summary="amzn_json"),
                _make_stock_signal_record(symbol="GOOGL", summary="googl_json"),
            ],
        )
        save_run(run, runs_dir=tmp_path)
        exit_code = inspect_run_script.main(
            [
                "json_sym",
                "--runs-dir",
                str(tmp_path),
                "--json",
                "--symbol",
                "GOOGL",
            ]
        )
        assert exit_code == 0
        import json as json_lib

        parsed = json_lib.loads(capsys.readouterr().out)
        symbols = {s["symbol"] for s in parsed["signals"]}
        assert symbols == {"GOOGL"}
```

### Step 14.2: Run the tests to verify failure

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_inspect_run_script.py::TestInspectRunDetailFlags -v`
- Expected: tests fail because the flags don't exist yet.

### Step 14.3: Extend the CLI with detail flags and JSON output

- [ ] **Modify `scripts/inspect_run.py`** — add formatters and extend `main()`:

```python
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


def _emit_json(run: BacktestRun, *, symbol_filter: str | None, analyst_type_filter: str | None) -> None:
    """Dump the full BacktestRun as JSON, optionally filtering signals + trades."""
    import json as json_lib

    data = json_lib.loads(run.model_dump_json())
    if symbol_filter:
        data["trades"] = [t for t in data["trades"] if t.get("symbol") == symbol_filter]
        data["signals"] = [
            s for s in data["signals"] if s.get("symbol") == symbol_filter
        ]
    if analyst_type_filter:
        data["signals"] = [
            s for s in data["signals"] if s.get("analyst_type") == analyst_type_filter
        ]
    print(json_lib.dumps(data, indent=2, default=str))
```

- [ ] **Update `main()` in `scripts/inspect_run.py`** to wire in the new flags:

```python
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
    parser.add_argument("--symbol", default=None, help="Filter trades and signals to a symbol")
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
        _emit_json(run, symbol_filter=args.symbol, analyst_type_filter=args.analyst_type)
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
```

### Step 14.4: Run tests to verify they pass

- [ ] Run: `.venv/bin/pytest tests/unit/backtest/test_inspect_run_script.py -v`
- Expected: all tests pass (both default + detail-flag classes).

### Step 14.5: Run linter

- [ ] Run: `.venv/bin/ruff check scripts/inspect_run.py tests/unit/backtest/test_inspect_run_script.py`
- Expected: `All checks passed!`

### Step 14.6: Commit

- [ ] Run:

```bash
git add scripts/inspect_run.py tests/unit/backtest/test_inspect_run_script.py
git commit -m "$(cat <<'EOF'
Add inspect_run detail flags and JSON output

--trades dumps full trade list; --signals dumps verbatim analyst summaries;
--signals-top N chronologically truncates; --snapshots dumps per-day NAV;
--symbol and --analyst-type filter trades and signals (composes with flags);
--json dumps the full BacktestRun Pydantic model, with --symbol /
--analyst-type filtering the signals/trades arrays in the JSON payload.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: Update architecture spec with Phase 2 implementation notes

Mirror the §6.10 pattern from Phase 1. Document deltas from the spec: confidence fields on `SignalDivergence`, the `effective_agents_config` capture approach, the drift threshold of 10%, conviction shifts, and any other surprises discovered during implementation.

**Files:**
- Modify: `plans/architecture/LLM-BACKTEST-ATTRIBUTION.md`

### Step 15.1: Add the implementation notes subsection

- [ ] **Append a new §7.4 subsection** at the end of §7 in `plans/architecture/LLM-BACKTEST-ATTRIBUTION.md`, just before the `---` separator that precedes §8. Content:

```markdown
### 7.4. Phase 2 Implementation Notes

Phase 2 was implemented in commits on 2026-04-08 per the implementation plan at `plans/implementation/2026-04-08-llm-backtest-attribution-phase2.md`. Deltas from the original spec:

- **`SignalDivergence` carries both baseline and treatment confidence fields**, not just the `impact` scalar. This was necessary to render the drilldown with per-side confidence numbers and to power the `--min-confidence` filter and `--include-conviction-shifts` flag. The spec's original field list implicitly collapsed them into `impact`.
- **Compatibility check's `model` and `temperature` comparison** was blocked by a Phase 1 gap: `BacktestConfig` doesn't store these values — they live on `EquitiesConfig.agents.{analyst}.{model,temperature}` and are resolved at runtime in `BacktestContext`. Phase 2 closes the gap by adding `effective_agents_config: AgentsConfig | None` to `BacktestRun` and `BacktestResult`, populated by a new `BacktestEngine._collect_agents_config_from_context` static helper that reads `ctx.effective_agents_config` (stashed during `BacktestContext.create`). Runs saved before Phase 2 have this field as `None` and emit a single "cannot verify model/temperature" warning during comparison instead of per-analyst mismatches.
- **High-drift auto-warning threshold is 10%**, configured in `comparison._HIGH_DRIFT_THRESHOLD`. Chosen as a round number large enough to tolerate a few drift cells on small post-screening universes (the real Phase 1 saved run has 14 signals total) without firing on every comparison, while still loud enough to catch the scenario where screening output differs materially between runs.
- **Conviction shifts (`score_delta == 0` but confidence moved) live in `RunComparison.conviction_shifts`**, a separate list from `signal_divergences`. They're excluded by default per the spec ("skip if score_delta == 0") and opted in via `--include-conviction-shifts`. Impact formula for conviction shifts is `|conf_delta| × score` (where `score` is the shared bullish_score), distinct from the main formula `|score_delta| × max(conf)`.
- **`compare_runs --json` schema is curated**, not a full `BacktestRun` dump. The fields are: `baseline_run_id`, `treatment_run_id`, `baseline_skill_bundle_hash`, `treatment_skill_bundle_hash`, `generated_at`, `compatibility_warnings`, `metric_deltas`, `signal_divergences`, `conviction_shifts`, `universe_drift`. Consumers who need the full runs can reload them by run_id via `result_store.load_run` or `inspect_run --json`. Drilldown truncation (`--top-n`) does NOT apply to the JSON output — every divergence and drift cell is always included.
- **`inspect_run` default output summarizes trades**, not dumps them. A weekly-rebalance run with top_n=20 over a year can have hundreds of trades; dumping by default would drown the header. `--trades` is the opt-in to see the full list. Same reasoning for `--signals`, `--snapshots`.
- **`inspect_run --json` filters are preserved in the JSON payload** — `--symbol` and `--analyst-type` filter the signals/trades arrays even in JSON mode. All other text-section flags are ignored in `--json` mode.
```

### Step 15.2: Commit

- [ ] Run:

```bash
git add plans/architecture/LLM-BACKTEST-ATTRIBUTION.md
git commit -m "$(cat <<'EOF'
Document Phase 2 implementation notes in LLM-BACKTEST-ATTRIBUTION.md

Adds §7.4 subsection mirroring §6.10 from Phase 1. Documents deltas from
the original §7 spec: confidence fields on SignalDivergence, the
effective_agents_config capture approach, the 10% high-drift threshold,
conviction shifts handling, the curated JSON schema rationale, and the
inspect_run summarize-by-default policy for trades/signals/snapshots.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

Before handing off, verify:

**Spec coverage (§7 Phase 2):**
- §7.1 `comparison.py` dataclasses + `compare_runs` + formatters → Tasks 3, 4, 5, 6, 7, 8, 9, 10, 11 ✓
- §7.1 compatibility logic (start_date, end_date, top_n, rebalance_frequency, branch_name, model, temperature, max_llm_calls_per_rebalance) → Task 4 (with Task 1/2 unblocking model/temperature via `effective_agents_config`) ✓
- §7.1 skill_bundle_hash identical → "nothing to attribute" warning → Task 4 ✓
- §7.1 signal divergence computation (impact = `|score_delta| × max(conf)`, sort desc, skip zero delta) → Task 6 ✓
- §7.1 universe drift tracking (cells in one run only, excluded from divergences) → Task 8 ✓
- §7.2 `scripts/compare_runs.py` (exit codes 0/1/2, `--top-n`, `--metrics-only`, `--json`) → Task 12 ✓
- §7.3 `scripts/inspect_run.py` (single-run drill-down) → Tasks 13, 14 ✓

**Design decisions from brainstorming locked in as code:**
- Signal ranking: spec formula + `--min-confidence` floor → Task 7 ✓
- Conviction shifts excluded by default, `--include-conviction-shifts` merges via `|conf_delta| × score` → Task 7 ✓
- Universe drift: footer + 10% auto-warning + `--show-drift` detail + cell-level keys → Tasks 8, 11 ✓
- Metric verdict absence: loud banner + ΔRAW column label → Task 9 ✓
- `inspect_run` default: header + summaries; `--trades`/`--signals` opt-in → Tasks 13, 14 ✓
- Output format: text default, `--json` opt-in mutually exclusive; compare_runs JSON curated; inspect_run JSON full dump → Tasks 12, 14 ✓

**Not in spec but necessary for Phase 2:**
- Task 1: `effective_agents_config` field on `BacktestRun`/`BacktestResult` — unblocks the spec's model/temperature compatibility check, which Phase 1 did not store.
- Task 2: engine wiring for `effective_agents_config` — mirror of Phase 1's signal collector pattern.
- Task 3: conftest builders for `BacktestRun` fixtures — shared test utilities for all subsequent comparison tests.
- Task 15: architecture spec update — mirrors §6.10 from Phase 1 and documents the plumbing deltas so Phase 3 authors have accurate context.

**Placeholder scan:**
- No "TBD", "TODO", "implement later", "similar to Task N", "add appropriate error handling".
- All test code is complete and runnable.
- All implementation code is complete.
- All commit messages use the HEREDOC + Co-Authored-By pattern.

**Type consistency:**
- `compare_runs(baseline, treatment, *, min_confidence: int = 0, include_conviction_shifts: bool = False) -> RunComparison` consistent across Tasks 4, 5, 6, 7, 8, 12.
- `SignalDivergence` field set consistent across Tasks 3, 6, 7, 11.
- `RunComparison.to_json_dict() -> dict` referenced in Tasks 11, 12.
- `format_metric_table(cmp: RunComparison) -> str`, `format_signal_drilldown(cmp, top_n=20) -> str`, `format_drift_section(cmp, *, show_drift=False) -> str` consistent across Tasks 9, 10, 11, 12.
- `effective_agents_config: AgentsConfig | None` consistent across Tasks 1, 2, 4.

**Tooling:**
- All `pytest` commands use `.venv/bin/pytest` per project convention.
- All `ruff` commands use `.venv/bin/ruff` per project convention.
- All commits use the HEREDOC pattern with `Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>` trailer.

**Commit message style:** Matches existing repo convention (imperative, no conventional-commits prefix, HEREDOC body, Co-Authored-By trailer).

**Ordering:** Tasks are linearized by dependency — Task 1 blocks Task 2, Task 2 blocks Task 4's model/temperature warnings, Task 3 blocks everything requiring `_make_backtest_run`, Tasks 4–8 build `compare_runs` incrementally, Tasks 9–11 add formatters, Task 12 wraps them in a CLI, Tasks 13–14 do `inspect_run`, Task 15 finalizes docs. Each task is self-contained and testable on completion.

---

## Out-of-Scope Reminders (Phase 3)

These are **explicitly not part of this plan**. They will get their own implementation plan after Phase 2 ships and is used against real prompt iterations:

- `app/modules/backtest/statistics.py` — variance, per-metric deltas, verdict labels (LIKELY / POSSIBLE / WITHIN NOISE / ZERO NOISE).
- `app/modules/backtest/noise_floor_store.py` — load/save noise-floor estimates keyed by experiment config hash.
- `app/modules/backtest/experiment.py` — full experiment harness orchestration.
- `scripts/probe_noise.py` — variance probe + noise-floor computation CLI.
- `scripts/run_experiment.py` — end-to-end experiment runner CLI.
- `BacktestTier` presets in `config.py` and `hash_experiment_config` helper.
- Replacing Phase 2's `format_metric_table` banner with real per-metric verdict labels — that replacement is the core Phase 3 deliverable and will update the table formatter at that time.
- Position-size-weighted impact computation (considered and rejected during Phase 2 brainstorming because it confounds prompt attribution with portfolio-state-at-the-time — revisit in Phase 3 if verdicts show the pure-confidence impact misranks divergences that materially affected trading).

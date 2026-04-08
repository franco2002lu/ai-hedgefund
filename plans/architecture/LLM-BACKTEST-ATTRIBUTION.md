# LLM-Mode Backtest Attribution

This document defines the architecture for running backtests with the real LLM analysts (instead of the deterministic quantitative replacements) and attributing observed performance shifts to specific prompt-content changes with statistical confidence.

> **Prerequisites**: Project Phase 3 backtesting infrastructure (`BACKTESTING-INFRASTRUCTURE.md`) and the Project Phase 2 equities pipeline (`PHASE2-EQUITIES-BRANCH.md`) must be operational. The LLM-mode plumbing in the `use_llm_agents=True` branch of `app/modules/backtest/context.py` already exists and is gated behind `BacktestConfig.use_llm_agents=True`.

> **Note on phase terminology**: This document uses "Phase 1 / 2 / 3" to refer to **delivery stages of this spec** (incremental shippable slices). These are NOT the same as the Project Phases referenced above. To avoid confusion, the broader project phases are always written as "Project Phase N", while the stages internal to this spec are written as "Phase N" without the "Project" prefix.

> **Implementation planning**: This spec is intended to drive **three separate implementation plans**, one per delivery phase (Phase 1, Phase 2, Phase 3). Each phase is independently shippable and useful; the user can stop after any phase if their needs are met.

---

## 1. Motivation

The current backtesting engine swaps the LLM-based analysts (`NewsAnalyst`, `FundamentalsAnalyst`, `TechnicalAnalyst`) for deterministic quantitative replacements (`QuantitativeNewsAnalyst`, etc. in `app/modules/backtest/quantitative_analysts.py`). This is by design — quantitative replacements give bit-exact reproducibility and zero LLM cost — but it means the backtest cannot detect performance shifts caused by changes to the analyst skill files in `app/modules/equities/agents/skills/`.

When a user edits a skill file (for example, adding a `## Critical Reminders` section to `base/fundamentals.md`), they currently have no way to answer the question: **"did this prompt change improve or hurt portfolio performance, and is the observed delta real or LLM noise?"**

This document specifies the components needed to answer that question.

---

## 2. Goals

1. **Reproducible LLM-mode backtests** — running the same backtest twice with the same prompts must produce bit-identical results, despite Anthropic API nondeterminism (Claude does not expose a `seed` parameter, and `temperature=0` is insufficient).
2. **Per-signal drill-down** — for any two saved runs, identify which specific `(date, symbol, analyst_type)` cells diverged most strongly between the prompt versions, with both LLM responses side by side.
3. **Statistical attribution** — distinguish "real" performance shifts from LLM noise by computing a per-config noise floor (mean ± stddev across N variance-probe runs) and labeling each metric delta with a verdict (`LIKELY SIGNAL` / `POSSIBLE SIGNAL` / `WITHIN NOISE`).
4. **Tiered configs** — support multiple backtest scales (`quick` / `medium` / `full`) so users can iterate cheaply during exploration and reserve expensive runs for production sign-offs.
5. **Composable + harness** — expose composable CLI primitives (run, probe, compare, inspect) and a thin experiment-runner harness that orchestrates them for the common case.

## 3. Non-Goals

- **Replacing the deterministic backtest path.** The quantitative analysts remain the default for `python -m scripts.run_backtest`. LLM-mode is opt-in via `--llm`.
- **Full statistical hypothesis testing.** The verdict logic uses simple sigma thresholds (1σ, 2σ), not formal p-values, Bonferroni corrections, or bootstrap CIs. Sufficient for practical decision-making, not for academic publication.
- **Eliminating Anthropic API nondeterminism at the call level.** We accept that two real API calls with identical inputs may differ; we work around it by **caching the first response** persistently rather than trying to make every call deterministic.
- **Multi-variant tournaments** (e.g., comparing 5 prompt variants in parallel). The harness compares exactly two variants (`baseline` and `treatment`).
- **Continuous nightly experimentation infrastructure.** Out of scope for this spec; could be added later by wrapping `scripts/run_experiment.py` in cron.
- **Web UI for browsing results.** All output is text/JSON; users use shell tools to inspect.

---

## 4. Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Reproducibility strategy | Persistent LLM response cache keyed on `hash(system_prompt + user_prompt + model + temperature)` | Anthropic does not expose a `seed`; `temperature=0` is insufficient. The only path to bit-exact reproducibility is to cache real responses. |
| Cache backend | SQLite (`data/llm_response_cache.db`) | Atomic writes, single file, concurrent-safe (analysts run in parallel via `asyncio.gather`), introspectable via SQL. JSON files would have one-file-per-call overhead. |
| Cache wrapping point | `AnthropicAnalystClient.invoke()` (in `llm_client.py`) | Lowest level that sees the fully-composed prompt. Wrapping at the analyst layer would miss prompt-content changes; wrapping at HTTP would be too low. |
| Skill version management | Live skills directory + named bundles in `data/skill_bundles/<name>/` as escape hatch | Most of the time the user works on whatever git ref they're on. Bundles enable parallel A/B in one shell session without git checkouts. The system auto-fingerprints whichever directory is loaded. |
| Skills directory plumbing | Optional `skills_dir: Path \| None` parameter all the way down (`compose_system_prompt` → analyst constructors → `BacktestContext`) | Explicit, testable, lru_cache key includes the path so multiple bundles coexist without collision. Avoids global mutable state. |
| Result store format | Per-run JSON files in `data/backtest_runs/<id>.json` | Human-readable, easy to diff, easy to share. SQLite would be overkill for write-once-read-occasionally data. |
| Noise floor cache lifecycle | Per-config, manually invalidated, with stale warning at 30 days | Variance probing is the most expensive operation. Auto-expiry (TTL) is annoying; auto-reprobe is too implicit. Manual invalidation is cheap by default with explicit control. |
| Noise floor cache backend | SQLite (`data/noise_floor_cache.db`) | Same atomic-write requirements as the LLM cache. Single key (`config_hash`) keeps schema simple. |
| `compute_config_hash` excludes `skills_bundle` | Yes — by design | The noise floor is supposed to be reusable across prompt variants; the whole point is "is this prompt-shift larger than the prompt-independent noise". |
| Verdict thresholds | 1σ / 2σ (POSSIBLE / LIKELY signal) | Practical decision aid, not statistical inference. Z-score-style intuition without the formal apparatus. |
| Tier presets | `(top_n, duration_days, rebalance_frequency)` anchored on `--end-date`, not "today" | Anchoring on an explicit end date keeps `config_hash` stable across re-runs of the "same" experiment. |
| Auto-probe on missing noise floor | **No** — hard error with copy-pastable command | Variance probes cost ~$50–$1,100 depending on tier. Should never happen accidentally. |
| Statistical methodology | Simple sigma-based labels, no formal hypothesis tests | YAGNI. The user will read 6–8 verdicts per experiment and make a judgment call. Formal p-values would be more rigorous but harder to interpret. |
| Variance probe runs | Default `N=5`, configurable via `--runs` | Five samples are enough for a stddev estimate that's stable to ±20%. More is better but the cost scales linearly. |

---

## 5. Architecture

### Component map

```
┌─ scripts/ ────────────────────────────────────────────────────┐
│  run_backtest.py    (extended: --llm, --skills-bundle, ...)   │  ← Phase 1
│  bundle_skills.py   (new: snapshot live skills/ as bundle)    │  ← Phase 1
│  compare_runs.py    (new: diff two saved runs)                │  ← Phase 2
│  inspect_run.py     (new: dump structured info on one run)    │  ← Phase 2
│  probe_noise.py     (new: variance probe + noise floor calc)  │  ← Phase 3
│  run_experiment.py  (new: full experiment harness)            │  ← Phase 3
└────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─ app/modules/backtest/ ───────────────────────────────────────┐
│  llm_response_cache.py   (new: persistent SQLite cache)       │  ← P1
│  result_store.py         (new: load/save backtest run files)  │  ← P1
│  comparison.py           (new: diff logic + drill-down)       │  ← P2
│  noise_floor_store.py    (new: load/save noise estimates)     │  ← P3
│  statistics.py           (new: variance, deltas, verdicts)    │  ← P3
│  experiment.py           (new: harness orchestration)         │  ← P3
│  config.py               (extended: tier presets, cache cfg)  │  ← P1+P3
│  context.py              (extended: bundle resolution)        │  ← P1
└────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─ app/modules/equities/agents/ ─────────────────────────────────┐
│  skills/loader.py        (extended: skills_dir parameter)     │  ← P1
│  llm_client.py           (extended: cache lookup wrapper)     │  ← P1
│  {fundamentals,news,technical}_analyst.py                     │  ← P1
│                          (skills_dir constructor parameter)   │
└────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─ data/ (created at runtime) ───────────────────────────────────┐
│  llm_response_cache.db   SQLite, keyed by (prompt+model+temp) │  ← P1
│  backtest_runs/<id>.json One file per saved backtest run     │  ← P1
│  skill_bundles/<name>/   Snapshotted skill directories       │  ← P1
│  noise_floor_cache.db    SQLite, keyed by config hash        │  ← P3
│  experiments/<id>.json   Saved experiment results            │  ← P3
└────────────────────────────────────────────────────────────────┘
```

### Module boundaries

All new code lives inside the existing `backtest` and `equities` modules. No new top-level packages. Honors the modular monolith pattern from `CLAUDE.md`.

The dependency direction is one-way: `experiment.py` depends on `comparison.py` and `statistics.py`; `comparison.py` depends on `result_store.py`; `result_store.py` and `noise_floor_store.py` are leaves. The CLI scripts depend on the modules but never on each other.

---

## 6. Phase 1: Reproducible LLM-Mode Backtests

**Deliverable in one sentence**: After Phase 1 you can run `python -m scripts.run_backtest 2025-01-01 2025-12-31 --top-n 50 --llm --save`, save the result, run again with the same arguments, and get bit-identical numbers because every LLM call hits the persistent cache.

### 6.1. `app/modules/backtest/llm_response_cache.py` (new)

SQLite-backed persistent cache. Single table.

```sql
CREATE TABLE llm_responses (
    cache_key TEXT PRIMARY KEY,
    system_prompt TEXT NOT NULL,
    user_prompt TEXT NOT NULL,
    model TEXT NOT NULL,
    temperature REAL NOT NULL,
    response_json TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    hit_count INTEGER DEFAULT 0
);
CREATE INDEX idx_created_at ON llm_responses(created_at);
```

```python
class LLMResponseCache:
    def __init__(self, db_path: Path) -> None: ...
    def get(self, system_prompt: str, user_prompt: str, model: str, temperature: float) -> dict | None: ...
    def put(self, system_prompt: str, user_prompt: str, model: str, temperature: float, response: dict) -> None: ...
    def stats(self) -> dict: ...   # entry count, hits, misses, db size
    def close(self) -> None: ...
```

The full system+user prompts are stored alongside the hash so cache entries can be inspected without re-deriving the input. `get` increments `hit_count` on a hit.

### 6.2. `app/modules/equities/agents/llm_client.py` (modified)

`AnthropicAnalystClient` gains a `response_cache: LLMResponseCache | None = None` constructor argument. Cache lookup happens **inside** `invoke()` after the system prompt is finalized but before the API call:

```python
async def invoke(self, prompt: str, *, system_prompt: str | None = None) -> dict:
    if self._response_cache is not None and system_prompt is not None:
        cached = self._response_cache.get(system_prompt, prompt, self.model, self.temperature)
        if cached is not None:
            return cached
    # ... existing API call logic ...
    if self._response_cache is not None and system_prompt is not None:
        self._response_cache.put(system_prompt, prompt, self.model, self.temperature, response)
    return response
```

The cache is invisible to the analyst classes — they just see faster, deterministic responses.

### 6.3. `app/modules/equities/agents/skills/loader.py` (modified)

`compose_system_prompt` gains `skills_dir: Path | None = None` as the last argument. When `None`, uses the existing `_SKILLS_DIR` constant (no behavior change for default callers). When provided, reads all layers from the alternate directory. The lru_cache key includes `skills_dir` so multiple bundles can coexist without cache collision.

```python
@lru_cache(maxsize=64)
def compose_system_prompt(
    analyst_type: str,
    branch_name: str = "",
    sector: str | None = None,
    skills_dir: Path | None = None,
) -> str:
    skills_root = skills_dir if skills_dir is not None else _SKILLS_DIR
    # ... rest unchanged, using skills_root in place of _SKILLS_DIR ...
```

### 6.4. `{fundamentals,news,technical}_analyst.py` (modified)

Each analyst class gains `skills_dir: Path | None = None` in its constructor. The analyst stores it and passes it through to every `compose_system_prompt(...)` call. Default `None` preserves all existing call sites including unit tests.

### 6.5. `app/modules/backtest/config.py` (modified)

Three new fields on `BacktestConfig`:

```python
skills_bundle: str | None = None       # bundle name in data/skill_bundles/
use_llm_response_cache: bool = True    # set False for variance probes
llm_response_cache_path: Path = Path("data/llm_response_cache.db")
```

### 6.6. `app/modules/backtest/context.py` (modified)

The existing `use_llm_agents=True` branch in `BacktestContext.create()` gets two additions:

1. **Resolve `config.skills_bundle`** to a directory path under `data/skill_bundles/<name>/`. Raise `ValueError` if it doesn't exist. If `None`, pass `skills_dir=None` (live skills).
2. **Construct `LLMResponseCache`** once if `use_llm_response_cache=True`, pass the same instance into all three `AnthropicAnalystClient` constructors. Pass `skills_dir=resolved_path` into all three analyst constructors.

### 6.7. `app/modules/backtest/result_store.py` (new)

```python
class StockSignalRecord(BaseModel):
    date: date
    symbol: str
    analyst_type: str
    bullish_score: int
    confidence: int
    summary: str

class BacktestRun(BaseModel):
    run_id: str  # f"{timestamp}_{short_hash}_{config_label}"
    timestamp: datetime
    git_sha: str
    config: BacktestConfig
    skill_bundle_name: str | None
    skill_bundle_hash: str  # full sha256 of all skill files concatenated
    metrics: PortfolioMetrics
    benchmarks: list[BenchmarkComparison]
    snapshots: list[PortfolioSnapshot]
    trades: list[Trade]
    signals: list[StockSignalRecord]
    llm_cache_hits: int
    llm_cache_misses: int

def hash_skill_bundle(skills_dir: Path) -> str: ...
def save_run(run: BacktestRun, runs_dir: Path = Path("data/backtest_runs")) -> Path: ...
def load_run(run_id: str, runs_dir: Path = Path("data/backtest_runs")) -> BacktestRun: ...
def list_runs(runs_dir: Path = Path("data/backtest_runs")) -> list[dict]: ...
```

`hash_skill_bundle` walks the directory, sorts file paths deterministically, and hashes the concatenated content. Stable across machines, OS-agnostic. Returns a full sha256; callers can take a 12-char prefix for human use.

The `signals` list captures **every** `(date, symbol, analyst_type, score, confidence, summary)` tuple from the run. This is what makes Phase 2's drill-down possible — the data already exists in `BacktestContext.llm_cache` at end-of-run; we just dump it.

### 6.8. `scripts/bundle_skills.py` (new)

```bash
python -m scripts.bundle_skills <name> [--force]
```

Copies `app/modules/equities/agents/skills/` (excluding `__pycache__`) to `data/skill_bundles/<name>/`. Refuses to overwrite an existing bundle without `--force`. Records the git SHA of the source skills directory in a `.bundle_meta.json` file inside the bundle for provenance.

### 6.9. `scripts/run_backtest.py` (modified)

New flags:

```
--llm                              Enable LLM-mode (default: False, quantitative)
--temperature FLOAT                Analyst temperature (default: per-analyst config)
--max-llm-calls-per-rebalance INT  LLM call cap (default: 60)
--no-llm-cache                     Disable persistent response cache
--skills-bundle NAME               Load skills from data/skill_bundles/NAME
--save                             Persist result to data/backtest_runs/
```

When `--save` is used, the script computes the prompt fingerprint, builds a `BacktestRun` from the engine output, calls `save_run`, and prints the resulting `run_id` so it can be referenced later.

### 6.10. Phase 1 Implementation Notes

Phase 1 was implemented in commits on 2026-04-07 per the implementation plan at `plans/implementation/2026-04-07-llm-backtest-attribution-phase1.md`. Deltas from the original spec:

- **`_load_output_format` now takes a string-path argument** (not `Path`) so `lru_cache` can hash it. Empty string means "use the package default". This is an internal detail; callers of `compose_system_prompt` pass `Path | None` as specified.
- **`resolve_skills_bundle` accepts `"live"` as a synonym for `None`.** This gives the CLI a way to explicitly say "use the current skills directory" without defaulting.
- **`LLMResponseCache.hits`/`misses` are instance counters, not persistent columns.** They track the current process's cache activity and are read at end-of-run to populate `BacktestResult.llm_cache_hits`/`llm_cache_misses`. Per-row `hit_count` remains in the SQL schema for future stats queries.
- **`BacktestEngine._collect_signals_from_context` and `_collect_cache_stats_from_context`** are new static helpers that translate runtime context state into the result schema. Kept static to make them individually unit-testable.

---

## 7. Phase 2: Comparison Primitives + Drill-Down

**Deliverable in one sentence**: After Phase 2 you can run `python -m scripts.compare_runs <baseline_id> <treatment_id>` and immediately see "Sharpe shifted +0.07, Total Return +0.7%, and the top 5 divergences are: AAPL 2025-03-15 fundamentals 6→7 ('expanded operating margin' vs 'margin pressure')..."

### 7.1. `app/modules/backtest/comparison.py` (new)

```python
@dataclass
class MetricDelta:
    name: str
    baseline: float
    treatment: float
    delta: float

@dataclass
class SignalDivergence:
    date: date
    symbol: str
    analyst_type: str
    baseline_score: int
    treatment_score: int
    score_delta: int
    impact: float  # |score_delta| × max(confidences) — sort key
    baseline_summary: str
    treatment_summary: str

@dataclass
class RunComparison:
    baseline: BacktestRun
    treatment: BacktestRun
    metric_deltas: list[MetricDelta]
    signal_divergences: list[SignalDivergence]  # sorted by impact desc
    compatibility_warnings: list[str]
    universe_drift_count: int  # signals present in one run only

def compare_runs(baseline: BacktestRun, treatment: BacktestRun) -> RunComparison: ...
def format_metric_table(cmp: RunComparison) -> str: ...
def format_signal_drilldown(cmp: RunComparison, top_n: int = 20) -> str: ...
```

**Compatibility logic**: a run is "compatible" with another if all of these match: `start_date`, `end_date`, `top_n`, `rebalance_frequency`, `branch_name`, `model`, `temperature`, `max_llm_calls_per_rebalance`. Mismatches generate warnings, not errors. If `skill_bundle_hash` is identical between runs, generate a loud warning ("nothing to attribute").

**Signal divergence computation**: for each `(date, symbol, analyst_type)` triple, look up the signal in both runs. If both exist, compute `score_delta = treatment_score - baseline_score`. Skip if `score_delta == 0`. Compute `impact = |score_delta| × max(baseline_confidence, treatment_confidence)`. Sort the resulting list by impact descending. Signals present in one run but not the other are tracked as `universe_drift_count` and excluded from the divergence list.

### 7.2. `scripts/compare_runs.py` (new)

```bash
python -m scripts.compare_runs <baseline_id> <treatment_id> [--top-n 20] [--metrics-only] [--json]
```

Loads both runs via `result_store.load_run`, calls `compare_runs`, prints the output. Exit code 0 for success, 1 if either run not found, 2 if runs are wholly incompatible (different date ranges).

### 7.3. `scripts/inspect_run.py` (new)

```bash
python -m scripts.inspect_run <run_id>
```

Single-run summary: config, prompt fingerprint, all metrics, signal count broken down by analyst type, LLM cache hit rate, git SHA, timestamp. Mirrors the existing `run_backtest.py` print format so old runs can be re-read in the same way.

---

## 8. Phase 3: Variance Probing + Experiment Harness + Statistical Reporting

**Deliverable in one sentence**: After Phase 3 you can run `python -m scripts.run_experiment --preset medium --baseline-bundle X --treatment-bundle Y` and get a one-shot report telling you which metric shifts are real signal versus LLM noise.

### 8.1. `app/modules/backtest/statistics.py` (new)

Pure functions for variance and verdicts. No I/O.

```python
@dataclass
class MetricNoiseFloor:
    metric_name: str
    mean: float
    stddev: float
    n: int
    sample_values: list[float]

@dataclass
class NoiseFloor:
    config_hash: str
    config_label: str
    skill_bundle_hash: str  # which bundle was probed (metadata only)
    n_runs: int
    created_at: datetime
    last_updated_at: datetime
    metrics: dict[str, MetricNoiseFloor]
    sample_run_ids: list[str]

@dataclass
class Verdict:
    metric_name: str
    baseline: float
    treatment: float
    delta: float
    sigma: float | None
    label: str

def compute_metric_stats(values: list[float], name: str) -> MetricNoiseFloor: ...
def compute_noise_floor(probe_runs: list[BacktestRun], config_hash: str, skill_bundle_hash: str) -> NoiseFloor: ...
def compute_verdicts(cmp: RunComparison, nf: NoiseFloor) -> list[Verdict]: ...
def format_verdict_table(verdicts: list[Verdict]) -> str: ...
def hash_experiment_config(config: BacktestConfig) -> str: ...
```

**Verdict thresholds**: `|σ| > 2.0` → `LIKELY SIGNAL`; `1.0 < |σ| ≤ 2.0` → `POSSIBLE SIGNAL`; `|σ| ≤ 1.0` → `WITHIN NOISE`. Edge case: if `stddev == 0` (all probe runs returned identical values), raise `ValueError("ZERO NOISE — probe likely hit cache")` because that's almost always a bug.

**Metrics that get verdicts** (curated subset): `total_return`, `annualized_return`, `sharpe_ratio`, `sortino_ratio`, `max_drawdown`, `alpha`, `win_rate`. Other metrics show in the comparison table but are not labeled.

**`hash_experiment_config`** deliberately **excludes** `skills_bundle` and `use_llm_response_cache` so the noise floor is reusable across prompt variants. It includes: `start_date`, `end_date`, `top_n`, `branch_name`, `rebalance_frequency`, `model`, `temperature`, `slippage_bps`, `commission_per_trade`.

### 8.2. `app/modules/backtest/noise_floor_store.py` (new)

```sql
CREATE TABLE noise_floors (
    config_hash TEXT PRIMARY KEY,
    config_label TEXT NOT NULL,
    skill_bundle_hash TEXT NOT NULL,
    n_runs INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL,
    last_updated_at TIMESTAMP NOT NULL,
    metrics_json TEXT NOT NULL,
    sample_run_ids_json TEXT NOT NULL
);
```

```python
class NoiseFloorStore:
    def __init__(self, db_path: Path = Path("data/noise_floor_cache.db")) -> None: ...
    def get(self, config_hash: str) -> NoiseFloor | None: ...
    def put(self, noise_floor: NoiseFloor) -> None: ...
    def invalidate(self, config_hash: str) -> bool: ...
    def list_all(self) -> list[NoiseFloor]: ...
    def close(self) -> None: ...
```

### 8.3. `app/modules/backtest/config.py` (extended for Phase 3)

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

def config_from_preset(
    preset: BacktestTier,
    branch_name: str,
    end_date: date | None = None,
    **overrides,
) -> BacktestConfig:
    """Build a BacktestConfig from a preset. start_date = end_date - duration_days."""
```

Anchoring on `end_date` (not `today`) means re-running an experiment a week later still uses the same backtest period and the same `config_hash`.

### 8.4. `app/modules/backtest/experiment.py` (new)

```python
@dataclass
class ExperimentResult:
    baseline_run_id: str
    treatment_run_id: str
    noise_floor: NoiseFloor
    noise_floor_age_days: int
    noise_floor_stale: bool          # > 30 days old
    bundle_mismatch_warning: bool    # noise floor probed against different bundle
    comparison: RunComparison
    verdicts: list[Verdict]

class ExperimentRunner:
    def __init__(
        self,
        result_store_path: Path,
        noise_floor_store: NoiseFloorStore,
    ) -> None: ...

    async def run_experiment(
        self,
        config: BacktestConfig,
        baseline_skills_bundle: str | None,
        treatment_skills_bundle: str | None,
    ) -> ExperimentResult: ...

def format_experiment_report(result: ExperimentResult, top_n_signals: int = 10) -> str: ...
def save_experiment_result(
    result: ExperimentResult,
    experiments_dir: Path = Path("data/experiments"),
) -> Path: ...
```

The `LLMResponseCache` is **not** an `ExperimentRunner` constructor argument. The cache lifecycle is owned by `BacktestContext` (per §6.6) and configured via `BacktestConfig.llm_response_cache_path`. The runner just sets `use_llm_response_cache=True` on the configs it passes to the engine and the cache is constructed inside the context.

`save_experiment_result` writes the `ExperimentResult` as a single JSON file (write-once, no read-back required for the basic harness — `inspect_run` is for individual backtest runs, not experiments).

**Flow inside `run_experiment`**:

1. Compute `config_hash` from `config`.
2. Look up noise floor in `NoiseFloorStore`. If absent, raise `RuntimeError` with copy-pastable command:
   `python -m scripts.probe_noise --preset {preset} --branch {branch} --end-date {end_date}`
3. Compute `noise_floor_age_days` from `noise_floor.last_updated_at`. Set `noise_floor_stale=True` if > 30.
4. Run baseline backtest with `skills_bundle=baseline_skills_bundle` and `use_llm_response_cache=True`.
5. Run treatment backtest with `skills_bundle=treatment_skills_bundle` and `use_llm_response_cache=True`.
6. Save both via `result_store.save_run`.
7. Build `RunComparison` via `comparison.compare_runs(baseline, treatment)`.
8. Compute verdicts: `compute_verdicts(comparison, noise_floor)`.
9. Return `ExperimentResult`. Caller (`scripts/run_experiment.py`) calls `save_experiment_result(result)` to persist to `data/experiments/<timestamp>_<short_hash>.json`.

### 8.5. `scripts/probe_noise.py` (new)

```bash
python -m scripts.probe_noise --preset medium --branch growth --end-date 2025-12-31 \
    [--runs 5] [--skills-bundle NAME] [--invalidate] [--force] [--yes]
```

Behavior:

1. Build `BacktestConfig` from preset + flags. Compute `config_hash`.
2. If `--invalidate`: call `NoiseFloorStore.invalidate(config_hash)` and exit.
3. If an entry already exists for this `config_hash` and `--force` is not set: print summary and exit.
4. **Print expected cost** (per the cost model in §10) and prompt for confirmation. `--yes` skips the prompt.
5. Run N backtests with `use_llm_response_cache=False` (each call hits the API fresh — this is the variance source). Each run is saved to `result_store` with a `probe_` prefix on the `run_id`.
6. Call `compute_noise_floor(probe_runs, config_hash, skill_bundle_hash)`, store via `NoiseFloorStore.put`.
7. Print summary of the noise floor (per-metric mean ± stddev).

### 8.6. `scripts/run_experiment.py` (new)

```bash
python -m scripts.run_experiment --preset medium --branch growth \
    --end-date 2025-12-31 \
    --baseline-bundle baseline_pre_critical_reminders \
    --treatment-bundle live \
    [--report-out path/to/report.txt]
```

`live` is a special bundle name meaning "use the current `app/modules/equities/agents/skills/` directory". Otherwise resolves to `data/skill_bundles/<name>/`. The script: instantiates an `ExperimentRunner`, calls `runner.run_experiment(...)`, then `format_experiment_report(result)` for human output, and `save_experiment_result(result)` to persist the JSON. Prints the report to stdout or writes to `--report-out`.

### 8.7. Sample experiment report

```
================================================================
  EXPERIMENT REPORT
  Config:           medium / growth (top 50, 12mo, weekly)
  Period:           2024-12-31 to 2025-12-31
  Model:            claude-sonnet-4-6 @ temperature 0.0
  Baseline skills:  baseline_pre_critical_reminders (sha: a1b2c3...)
  Treatment skills: live (sha: d4e5f6...)
  Noise floor:      N=5, age 5 days  ✓ fresh
================================================================

--- Metric Verdicts ---
  Metric              Baseline    Treatment   Delta      Sigma   Verdict
  Total Return        +18.40%     +19.10%     +0.70%     +0.9σ   WITHIN NOISE
  Sharpe Ratio        1.24        1.31        +0.07      +1.4σ   POSSIBLE SIGNAL
  Sortino Ratio       1.78        1.92        +0.14      +1.8σ   POSSIBLE SIGNAL
  Max Drawdown        -8.20%      -7.90%      +0.30%     +0.5σ   WITHIN NOISE
  Alpha (SPY)         +2.10%      +2.40%      +0.30%     +0.6σ   WITHIN NOISE
  Win Rate            52.3%       53.1%       +0.8%      +0.4σ   WITHIN NOISE

--- Top 5 Signal Divergences ---
  1. NVDA 2025-08-22 fundamentals  6 → 8 (impact 16)
     baseline:  "P/E elevated at 65, growth modest, margin pressure"
     treatment: "Expanded operating margin and FCF growth offset valuation"
  ...

Experiment saved: data/experiments/2026-04-06T14-32-15_abc123de.json
================================================================
```

---

## 9. Data Flow — One Full Experiment Cycle

The canonical workflow:

1. **User snapshots current prompts** (one time, before editing):
   ```
   python -m scripts.bundle_skills baseline_v1
   ```
   Creates `data/skill_bundles/baseline_v1/` from the live `app/.../skills/` directory.

2. **User edits prompts** in `app/.../skills/` as normal git work.

3. **User probes the noise floor** (one time per config, or after Anthropic model update):
   ```
   python -m scripts.probe_noise --preset medium --branch growth --end-date 2025-12-31 --runs 5 --yes
   ```
   - Runs 5 backtests with `use_llm_response_cache=False` against the live skills.
   - Each run hits the API fresh (this is the variance source).
   - Saves each as `data/backtest_runs/probe_<id>.json`.
   - Computes mean ± stddev across the 5 runs.
   - Stores result in `data/noise_floor_cache.db` keyed by `config_hash`.

4. **User runs the experiment**:
   ```
   python -m scripts.run_experiment --preset medium --branch growth --end-date 2025-12-31 \
       --baseline-bundle baseline_v1 --treatment-bundle live
   ```
   - `ExperimentRunner` looks up the noise floor for this `config_hash` → found, age 5 days → not stale.
   - Runs baseline backtest with `skills_bundle=baseline_v1`, `use_llm_response_cache=True`. First time: ~7,800 LLM calls, all stored in `data/llm_response_cache.db`. Saves run.
   - Runs treatment backtest with `skills_bundle=None` (live skills). First time: ~7,800 LLM calls. Most cells miss the cache because the prompt content differs.
   - Calls `compare_runs(baseline, treatment)` → metric deltas + signal divergences.
   - Calls `compute_verdicts(comparison, noise_floor)` → labeled verdicts per metric.
   - Builds `ExperimentResult`, persists to `data/experiments/<timestamp>_<hash>.json`.
   - Prints the formatted report to stdout.

5. **User re-runs the same experiment**: every LLM call hits the persistent cache → 0 new API calls, bit-identical numbers, ~30 seconds wall-clock.

The persistent LLM cache is what makes step 5 free. The noise floor cache is what makes the experiment skip the expensive variance probe.

---

## 10. Cost Analysis

Per-call cost at Sonnet 4.6 with Anthropic prompt caching enabled (system prompt cached at 0.1× rate after the first call in a 5-minute window):

- Input: ~3K user tokens × $3/1M ≈ $0.009 per call
- Output: ~200 tokens × $15/1M ≈ $0.003 per call
- **Effective per-call cost: ~$0.012** (drops to ~$0.007 within the cached window)

**Per-backtest cost** (LLM calls = `top_n × rebalances × 3 analysts`):

| Tier | Universe × Period | Calls | Cost per backtest |
|------|-------------------|-------|-------------------|
| Quick | top 20, 6 months, weekly | ~1,560 | ~$11 |
| Medium | top 50, 1 year, weekly | ~7,800 | ~$55 |
| Full | top 100, 2 years, weekly | ~31,200 | ~$220 |

**Per-experiment cycle cost** (variance probe = N runs cache-disabled, plus 2 cached A/B runs):

| Tier | Probe (N=5) | A/B with cached probe | Marginal A/B (probe reused) |
|------|-------------|----------------------|----------------------------|
| Quick | ~$55 | ~$77 | ~$22 |
| Medium | ~$275 | ~$385 | ~$110 |
| Full | ~$1,100 | ~$1,540 | ~$440 |

The persistent LLM response cache drives the marginal cost of "test another prompt change against the same baseline" down to roughly **`2 × cost_per_backtest`** because the noise floor is reused, the baseline is cached, and only the treatment requires fresh API calls.

---

## 11. Error Handling

| Failure | Handling |
|---|---|
| Anthropic 429 rate limit | Retry with exponential backoff (1s, 2s, 4s, 8s) up to 3 attempts; then neutral fallback via `CachedAnalystWrapper` |
| Anthropic 5xx server error | Same retry policy as 429 |
| Anthropic 401 auth error | Hard fail at run start; surface message clearly |
| Network timeout | Retry once, then neutral fallback |
| `LLMResponseCache` SQLite corruption | Detect at startup via integrity check; log loud warning; offer to recreate (do not auto-delete) |
| Cache disk full | Hard fail with disk usage in error message |
| Skill bundle not found | `ValueError` at `BacktestContext` setup, before any LLM calls |
| Skill bundle missing required files | Same — fail fast |
| Result store run_id not found | `FileNotFoundError` with the exact path checked |
| Saved run JSON parse error | Error with file path; suggest re-running or restoring from backup |
| `compute_verdict` called with `stddev == 0` | Raise `ValueError("ZERO NOISE — probe likely hit cache")` and label that metric distinctly in the report |
| Noise floor missing for `run_experiment` config_hash | Hard error with copy-pastable command: `python -m scripts.probe_noise --preset {preset} --branch {branch} --end-date {end_date}` |
| Noise floor exists but for a different `skill_bundle_hash` | Warning at top of report, proceed with comparison |
| Noise floor older than 30 days | Warning at top of report, proceed with comparison |
| Compare two runs with mismatched configs | Multiple warnings at top of report; proceed but flag every mismatch |
| Compare two runs with identical `skill_bundle_hash` | Warning: "these runs used the same prompts, nothing to attribute"; still print the (empty) diff |
| Universe drift between baseline and treatment | Track count, exclude affected cells from divergence list, log count at end of comparison |
| Yahoo Finance data drift between runs | Outside our control. Cache key includes user prompt content (which contains the formatted data), so changed data naturally invalidates affected cache entries |

The general principle: **fail fast at boundaries** (config, file paths, missing prerequisites), **degrade gracefully on transient errors** (API hiccups), **never silently lose data** (cache corruption is loud, not auto-fixed).

---

## 12. Testing Strategy

### Phase 1 (cache + bundles + persistence)

~20 unit tests + 1 cost-gated integration:

- `LLMResponseCache`: put/get round-trip, hit count tracking, missing key returns `None`, key collision behavior.
- `result_store`: save/load round-trip, list_runs sorting, JSON schema validation.
- `hash_skill_bundle`: same content → same hash, ordering-invariant, detects file removal.
- `bundle_skills` script: snapshot creates dir, refuses overwrite without `--force`, records git SHA.
- `loader.compose_system_prompt(skills_dir=...)`: lru_cache key includes the path; two bundles produce different prompts; default `None` matches existing behavior.
- **Integration (gated on `ANTHROPIC_API_KEY`)**: run `--llm --top-n 3 --save` over a 1-month period, run again with identical args, assert second run is bit-identical and reports 100% cache hits. Cost ≈ $0.50 per CI run.

### Phase 2 (comparison + drill-down)

~15 unit tests, no integration needed:

- `compare_runs`: compatible runs produce expected deltas; incompatible configs generate per-mismatch warnings; identical prompt fingerprints generate the "nothing to attribute" warning; universe drift handled correctly.
- `format_metric_table` / `format_signal_drilldown`: snapshot tests of formatted output.
- Operates entirely on saved JSON; fixtures can be hand-crafted.

### Phase 3 (variance + harness + stats)

~25 unit tests + 1 integration:

- `compute_metric_stats`: standard math, single-element edge case.
- `compute_noise_floor`: aggregates correctly across N runs.
- `compute_verdicts`: all four label cases (LIKELY / POSSIBLE / WITHIN NOISE / ZERO NOISE), delta=0 edge case, very small stddev.
- `hash_experiment_config`: stable across calls; configs differing only in `skills_bundle` produce the same hash; configs differing in `start_date` produce different hashes.
- `NoiseFloorStore`: put/get round-trip, invalidate returns `True`/`False` correctly, list_all.
- `apply_tier` / `config_from_preset`: each preset resolves to expected dates given a fixed end_date.
- `ExperimentRunner.run_experiment`: hand-craft two `BacktestRun` fixtures and a `NoiseFloor` fixture, mock the engine, assert verdicts are computed correctly. **No real LLM calls** — exercises the orchestration logic in isolation.
- **Optional cost-gated integration test** behind `--run-expensive-tests` pytest flag: real probe + experiment on tiny universe. Cost ≈ $5 per run.

### Linting

All new code goes through `ruff check` and `ruff format`, same as existing modules.

---

## 13. Open Questions / Future Work

1. **Auto-cleanup of stale cache entries**. The `llm_response_cache.db` will grow without bound. A future maintenance script could prune entries older than N days or above a size threshold.
2. **Cache sharing across machines**. The persistent cache is local to a developer's machine. Sharing it (S3, shared NFS) would let teams pool cache hits, but introduces concurrency and trust questions.
3. **Multi-variant tournaments**. The current harness compares exactly two bundles. A future version could compare K bundles in a single run with pairwise verdicts.
4. **Continuous experimentation**. Wrapping `scripts/run_experiment.py` in cron and auto-flagging "promotable" prompt changes.
5. **Bootstrap confidence intervals**. Replacing the simple sigma threshold with bootstrapped CIs would be more rigorous (no normality assumption) but harder to interpret. Defer until a verdict turns out to be wrong.
6. **Skill versioning metadata in the skill files themselves**. Item 6 from the original skill-system gap analysis — adding minimal HTML-comment headers per skill file (`<!-- skill: fundamentals.base | version: 2 | updated: 2026-04-06 -->`) so the auto-fingerprint can also surface human-readable version labels.

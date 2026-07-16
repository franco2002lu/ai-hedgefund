# Adaptive Analyst Weights Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feed the weekly per-analyst rank-ICs in `attribution_reports` back into the composite weights — bounded, per-branch, fully persisted, with digest visibility.

**Architecture:** A new pure-policy module (`adaptive_weights.py`) computes an EW rolling IC per analyst, tilts the static prior multiplicatively, and projects the result into floor/max-shift bounds; `run_pipeline` resolves weights per run (session-gated so backtests stay static), passes them to `PortfolioManager`, and persists them on the decision row. The weekly CLI runs attribution *before* trading so weights are one week fresher, and the digest reports weights, EWICs, and ≤0-streak alerts. Attribution additionally records a `"composite"` IC (measurement only).

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy 2 async, Alembic, pytest (mock-session unit style per `test_attribution_engine.py`).

**PROJECT RULE — no commits:** The user commits themselves. Wherever a normal TDD loop would commit, `git add` the files instead (steps below say "Stage"). Never run `git commit`. Use `.venv/bin/pytest` from the repo root (the venv lives in the main checkout: `/Users/franco_lu/Desktop/ai-hedgefund-final/.venv`).

**Spec:** `docs/superpowers/specs/2026-07-16-adaptive-analyst-weights-design.md`

---

## File map

| file | action | responsibility |
|---|---|---|
| `app/modules/equities/config.py` | modify | `AdaptiveWeightsConfig` + `AgentsConfig.adaptive` |
| `app/modules/equities/adaptive_weights.py` | create | policy math, `AnalystWeightsReport`, DB loader `resolve_analyst_weights` |
| `app/modules/equities/agents/portfolio_manager.py` | modify | optional `analyst_weights` override |
| `app/db/models.py` | modify | `PortfolioDecisionModel.analyst_weights` JSONB |
| `app/db/migrations/versions/c4d2a91b7e55_add_analyst_weights_to_portfolio_decisions.py` | create | nullable JSONB column |
| `app/modules/equities/models.py` | modify | `RunResult.analyst_weights_report` |
| `app/modules/equities/service.py` | modify | resolve → PM → persist wiring |
| `app/modules/equities/attribution.py` | modify | `"composite"` IC |
| `app/modules/equities/weekly_runner.py` | modify | summary field + digest lines |
| `scripts/run_weekly_pipeline.py` | modify | attribution before trading |
| `scripts/preview_adaptive_weights.py` | create | read-only prod preview |
| `tests/unit/equities/test_adaptive_weights.py` | create | policy + loader + replay tests |
| `tests/unit/equities/test_config.py` | modify | validator tests |
| `tests/unit/equities/test_portfolio_manager.py` | modify | override tests |
| `tests/unit/equities/test_persist_artifacts.py` | modify | JSONB persisted |
| `tests/unit/equities/test_attribution.py` | modify | composite IC math |
| `tests/unit/equities/test_attribution_engine.py` | modify | composite passed from decision |
| `tests/unit/equities/test_weekly_runner.py` | modify | digest lines + summary carry |
| `tests/unit/scripts/test_weekly_cli_ordering.py` | create | attribution-precedes-trading |
| `CLAUDE.md` | modify | gotchas |

Canonical analyst key order everywhere: `("fundamentals", "news", "technical")`.

---

### Task 1: `AdaptiveWeightsConfig`

**Files:**
- Modify: `app/modules/equities/config.py`
- Test: `tests/unit/equities/test_config.py`

- [ ] **Step 1: Write failing tests** — append to `tests/unit/equities/test_config.py`:

```python
import pytest
from pydantic import ValidationError

from app.modules.equities.config import AdaptiveWeightsConfig, AgentsConfig


class TestAdaptiveWeightsConfig:
    def test_defaults(self):
        cfg = AdaptiveWeightsConfig()
        assert cfg.enabled is True
        assert cfg.lookback_weeks == 12
        assert cfg.half_life_weeks == 4.0
        assert cfg.min_history_weeks == 6
        assert cfg.weight_floor == 0.10
        assert cfg.max_weekly_shift == 0.05
        assert cfg.ic_tilt_scale == 0.40
        assert cfg.alert_streak_weeks == 4

    def test_nested_on_agents_config(self):
        assert AgentsConfig().adaptive.enabled is True

    def test_floor_must_leave_feasible_simplex(self):
        with pytest.raises(ValidationError):
            AdaptiveWeightsConfig(weight_floor=0.40)  # 3*0.40 > 1

    def test_lookback_must_cover_min_history(self):
        with pytest.raises(ValidationError):
            AdaptiveWeightsConfig(lookback_weeks=4, min_history_weeks=6)

    def test_positive_scales(self):
        with pytest.raises(ValidationError):
            AdaptiveWeightsConfig(half_life_weeks=0)
        with pytest.raises(ValidationError):
            AdaptiveWeightsConfig(ic_tilt_scale=0)
        with pytest.raises(ValidationError):
            AdaptiveWeightsConfig(max_weekly_shift=0)
        with pytest.raises(ValidationError):
            AdaptiveWeightsConfig(max_weekly_shift=1.5)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/equities/test_config.py -q`
Expected: FAIL — `ImportError: cannot import name 'AdaptiveWeightsConfig'`

- [ ] **Step 3: Implement** — in `app/modules/equities/config.py`, add above `AgentsConfig`:

```python
class AdaptiveWeightsConfig(BaseModel):
    """IC-feedback policy for composite weights (see 2026-07-16 spec).

    Weights tilt off the static prior by trailing rank-IC evidence:
    target ∝ static · exp(EWIC / ic_tilt_scale), then are clamped to a floor
    and a max weekly move and renormalized.
    """

    enabled: bool = True
    lookback_weeks: int = 12
    half_life_weeks: float = 4.0
    min_history_weeks: int = 6
    weight_floor: float = 0.10
    max_weekly_shift: float = 0.05
    ic_tilt_scale: float = 0.40
    alert_streak_weeks: int = 4

    @model_validator(mode="after")
    def _feasible(self) -> "AdaptiveWeightsConfig":
        if self.half_life_weeks <= 0:
            raise ValueError("half_life_weeks must be > 0")
        if self.ic_tilt_scale <= 0:
            raise ValueError("ic_tilt_scale must be > 0")
        if not (0 < self.max_weekly_shift <= 1):
            raise ValueError("max_weekly_shift must be in (0, 1]")
        if not (0 < self.weight_floor and 3 * self.weight_floor <= 1):
            raise ValueError("weight_floor must be > 0 and 3*floor <= 1")
        if not (1 <= self.min_history_weeks <= self.lookback_weeks):
            raise ValueError("need 1 <= min_history_weeks <= lookback_weeks")
        if self.alert_streak_weeks < 1:
            raise ValueError("alert_streak_weeks must be >= 1")
        return self
```

and inside `AgentsConfig` (after `weight_technical`):

```python
    # IC-feedback loop (2026-07-16 spec); static weight_* above remain the
    # prior and the fallback whenever adaptation cannot run.
    adaptive: AdaptiveWeightsConfig = AdaptiveWeightsConfig()
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/unit/equities/test_config.py -q`
Expected: PASS

- [ ] **Step 5: Stage**

```bash
git add app/modules/equities/config.py tests/unit/equities/test_config.py
```

---

### Task 2: Policy math (`adaptive_weights.py` pure functions)

**Files:**
- Create: `app/modules/equities/adaptive_weights.py`
- Create: `tests/unit/equities/test_adaptive_weights.py`

IC series convention throughout: **freshest first** (`ics[0]` = most recent report), values `float | None`. Decay is by report lag, not by valid-observation index.

- [ ] **Step 1: Write failing tests** — create `tests/unit/equities/test_adaptive_weights.py`:

```python
"""Policy math for adaptive analyst weights (pure functions)."""

import math

from app.modules.equities.adaptive_weights import (
    ANALYSTS,
    alert_streaks,
    compute_adaptive_weights,
    ewma,
    project_weights,
    static_weights,
    tilt_targets,
)
from app.modules.equities.config import AgentsConfig

STATIC = {"fundamentals": 0.60, "news": 0.20, "technical": 0.20}


def _cfg(**adaptive_overrides) -> AgentsConfig:
    base = AgentsConfig()
    adaptive = base.adaptive.model_copy(update=adaptive_overrides)
    return base.model_copy(update={"adaptive": adaptive})


class TestEwma:
    def test_single_value_is_itself(self):
        assert ewma([0.3], half_life=4.0) == 0.3

    def test_known_answer_two_values(self):
        # lam = 0.5 ** (1/1) = 0.5; freshest first: (1.0*0.4 + 0.5*0.2)/1.5
        assert math.isclose(ewma([0.4, 0.2], half_life=1.0), 0.5 / 1.5)

    def test_none_skipped_but_lag_decay_kept(self):
        # freshest is None -> second value still decayed by lam**1
        lam = 0.5 ** (1 / 4.0)
        assert math.isclose(ewma([None, 0.4], half_life=4.0), 0.4)
        # mixed: (lam**0*0.4 skipped None at lag1 + lam**2*0.1)/(1+lam**2)
        expected = (0.4 + lam**2 * 0.1) / (1 + lam**2)
        assert math.isclose(ewma([0.4, None, 0.1], half_life=4.0), expected)

    def test_all_none_or_empty(self):
        assert ewma([], half_life=4.0) is None
        assert ewma([None, None], half_life=4.0) is None


class TestTiltTargets:
    def test_zero_ewic_reproduces_static(self):
        t = tilt_targets({a: 0.0 for a in ANALYSTS}, STATIC, tau=0.4)
        for a in ANALYSTS:
            assert math.isclose(t[a], STATIC[a])

    def test_known_answer(self):
        ewics = {"fundamentals": 0.4, "news": 0.0, "technical": -0.4}
        raw = {a: STATIC[a] * math.exp(ewics[a] / 0.4) for a in ANALYSTS}
        total = sum(raw.values())
        t = tilt_targets(ewics, STATIC, tau=0.4)
        for a in ANALYSTS:
            assert math.isclose(t[a], raw[a] / total)
        assert math.isclose(sum(t.values()), 1.0)


class TestProjectWeights:
    def test_within_bounds_passthrough_and_exact_sum(self):
        prev = dict(STATIC)
        target = {"fundamentals": 0.62, "news": 0.21, "technical": 0.17}
        w = project_weights(target, prev, floor=0.10, max_shift=0.05)
        assert math.isclose(sum(w.values()), 1.0)
        for a in ANALYSTS:
            assert abs(w[a] - prev[a]) <= 0.05 + 1e-9
            assert w[a] >= 0.10 - 1e-9

    def test_shift_cap_binds_and_deficit_redistributes(self):
        prev = dict(STATIC)
        # target pulls fund up hard; fund capped at 0.65, others absorb
        target = {"fundamentals": 0.80, "news": 0.10, "technical": 0.10}
        w = project_weights(target, prev, floor=0.10, max_shift=0.05)
        assert math.isclose(w["fundamentals"], 0.65)
        assert math.isclose(sum(w.values()), 1.0)
        for a in ("news", "technical"):
            assert prev[a] - 0.05 - 1e-9 <= w[a] <= prev[a] + 0.05 + 1e-9

    def test_floor_binds(self):
        prev = {"fundamentals": 0.75, "news": 0.13, "technical": 0.12}
        target = {"fundamentals": 0.85, "news": 0.05, "technical": 0.10}
        w = project_weights(target, prev, floor=0.10, max_shift=0.05)
        assert w["news"] >= 0.10 - 1e-9
        assert math.isclose(sum(w.values()), 1.0)

    def test_infeasible_bounds_fall_back_to_floor_projection(self):
        # floor raised above what prev+shift allows -> one-time jump mode
        prev = {"fundamentals": 0.80, "news": 0.10, "technical": 0.10}
        target = {"fundamentals": 0.50, "news": 0.25, "technical": 0.25}
        w = project_weights(target, prev, floor=0.30, max_shift=0.05)
        assert math.isclose(sum(w.values()), 1.0)
        for a in ANALYSTS:
            assert w[a] >= 0.30 - 1e-9

    def test_output_rounded_and_sums_exactly_one(self):
        prev = dict(STATIC)
        target = {"fundamentals": 1 / 3, "news": 1 / 3, "technical": 1 / 3}
        w = project_weights(target, prev, floor=0.10, max_shift=0.05)
        assert sum(w.values()) == 1.0  # exact after rounding policy
        for v in w.values():
            assert round(v, 6) == v


class TestAlertStreaks:
    def test_streak_counts_only_trustworthy_windows(self):
        # 9 obs all negative, min_history 6 -> evaluations at lags 0..3 -> streak 4
        ics = {"fundamentals": [0.1] * 9, "news": [0.1] * 9, "technical": [-0.2] * 9}
        alerts = alert_streaks(ics, half_life=4.0, min_history=6, streak_threshold=4)
        assert len(alerts) == 1
        a = alerts[0]
        assert a["analyst"] == "technical"
        assert a["streak"] == 4
        assert a["ewic"] < 0

    def test_positive_recent_week_breaks_streak(self):
        # freshest window EWIC > 0 -> no alert
        ics = {
            "fundamentals": [0.1] * 9,
            "news": [0.1] * 9,
            "technical": [0.9, -0.2, -0.2, -0.2, -0.2, -0.2, -0.2, -0.2, -0.2],
        }
        assert alert_streaks(ics, half_life=4.0, min_history=6, streak_threshold=4) == []

    def test_below_min_history_never_alerts(self):
        ics = {"fundamentals": [-0.5] * 4, "news": [-0.5] * 4, "technical": [-0.5] * 4}
        assert alert_streaks(ics, half_life=4.0, min_history=6, streak_threshold=4) == []

    def test_streak_below_threshold_not_reported(self):
        # only 7 obs -> evaluations at lags 0..1 -> streak 2 < 4
        ics = {"fundamentals": [0.1] * 7, "news": [0.1] * 7, "technical": [-0.2] * 7}
        assert alert_streaks(ics, half_life=4.0, min_history=6, streak_threshold=4) == []


class TestComputeAdaptiveWeights:
    def test_insufficient_history_falls_back_static(self):
        ics = {a: [0.2] * 5 for a in ANALYSTS}  # 5 < 6
        report = compute_adaptive_weights(ic_series=ics, prev_weights=dict(STATIC), agents_config=_cfg())
        assert report.mode == "static"
        assert report.reason == "insufficient_history"
        assert report.weights == static_weights(_cfg())
        assert report.valid_weeks == {a: 5 for a in ANALYSTS}

    def test_none_ics_do_not_count_toward_history(self):
        ics = {a: [0.2, None, 0.2, None, 0.2, 0.2, 0.2] for a in ANALYSTS}  # 5 valid
        report = compute_adaptive_weights(ic_series=ics, prev_weights=dict(STATIC), agents_config=_cfg())
        assert report.reason == "insufficient_history"

    def test_adaptive_moves_toward_evidence_within_cap(self):
        # 10 obs so the technical ≤0 streak has 5 trustworthy windows (≥ threshold 4)
        ics = {
            "fundamentals": [0.4] * 10,
            "news": [0.0] * 10,
            "technical": [-0.4] * 10,
        }
        report = compute_adaptive_weights(ic_series=ics, prev_weights=dict(STATIC), agents_config=_cfg())
        assert report.mode == "adaptive"
        assert report.reason == "ok"
        w = report.weights
        assert math.isclose(w["fundamentals"], 0.65)  # +cap binds
        # targets clip to (0.65, 0.15, 0.15); deficit 0.05 redistributes to
        # news/tech's equal headroom -> 0.175 each
        assert math.isclose(w["news"], 0.175)
        assert math.isclose(w["technical"], 0.175)
        assert math.isclose(sum(w.values()), 1.0)
        assert report.ewics["fundamentals"] > 0.39
        # constant negative series alerts
        assert any(a["analyst"] == "technical" for a in report.alerts)

    def test_prev_weights_anchor_progression(self):
        ics = {"fundamentals": [0.4] * 8, "news": [0.0] * 8, "technical": [-0.4] * 8}
        prev = {"fundamentals": 0.65, "news": 0.188253, "technical": 0.161747}
        report = compute_adaptive_weights(ic_series=ics, prev_weights=prev, agents_config=_cfg())
        assert report.weights["fundamentals"] <= 0.70 + 1e-9  # cap from new anchor


class TestReplayRealHistory:
    """Pin the policy against the real prod IC history (2026-05-11..2026-07-06
    decisions, freshest first). Guards against silent policy drift."""

    GROWTH = {
        "fundamentals": [0.3072, 0.2872, -0.1982, 0.0380, 0.0614, -0.2961, 0.2524, -0.1022, 0.2812],
        "news": [0.1563, -0.1538, -0.1982, 0.4369, -0.1806, -0.1373, 0.2259, -0.1098, -0.1150],
        "technical": [-0.0340, -0.5223, -0.3308, 0.7035, 0.0269, -0.0915, -0.0149, -0.0275, -0.2418],
    }

    def test_growth_first_adaptive_week_from_static(self):
        report = compute_adaptive_weights(
            ic_series=self.GROWTH, prev_weights=static_weights(_cfg()), agents_config=_cfg()
        )
        assert report.mode == "adaptive"
        w = report.weights
        # fundamentals EWIC ~ +0.09 -> capped one-week move up to 0.65
        assert math.isclose(w["fundamentals"], 0.65)
        # news EWIC ~ 0, technical ~ -0.08: both give up weight, tech more
        assert w["technical"] < w["news"] < 0.20 + 1e-9
        assert math.isclose(sum(w.values()), 1.0)
        assert min(report.valid_weeks.values()) == 9
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/equities/test_adaptive_weights.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.modules.equities.adaptive_weights'`

- [ ] **Step 3: Implement** — create `app/modules/equities/adaptive_weights.py`:

```python
"""Adaptive composite weights from trailing attribution ICs.

Closes the feedback loop designed in
docs/superpowers/specs/2026-07-16-adaptive-analyst-weights-design.md:
each week's composite weights tilt off the static prior by the
exponentially-weighted rolling mean of each analyst's rank-IC, clamped to a
floor and a max weekly shift, and normalized. Pure math lives in module
functions; `resolve_analyst_weights` owns the DB reads and never raises.

IC series convention: freshest report first; None = IC not computable that
week (skipped, but lag-based decay is preserved).
"""

from __future__ import annotations

import logging
import math
import uuid as _uuid

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import AttributionReportModel, PortfolioDecisionModel
from app.modules.equities.config import AgentsConfig

logger = logging.getLogger(__name__)

ANALYSTS = ("fundamentals", "news", "technical")


class AnalystWeightsReport(BaseModel):
    """The weights a pipeline run actually used, plus how they were derived."""

    weights: dict[str, float]
    mode: str  # "adaptive" | "static"
    reason: str  # "ok" | "disabled" | "insufficient_history" | "no_session" | "error"
    ewics: dict[str, float | None] = Field(default_factory=dict)
    valid_weeks: dict[str, int] = Field(default_factory=dict)
    alerts: list[dict] = Field(default_factory=list)  # {"analyst", "streak", "ewic"}


def static_weights(agents_config: AgentsConfig) -> dict[str, float]:
    return {
        "fundamentals": agents_config.weight_fundamentals,
        "news": agents_config.weight_news,
        "technical": agents_config.weight_technical,
    }


def static_report(agents_config: AgentsConfig, reason: str) -> AnalystWeightsReport:
    return AnalystWeightsReport(weights=static_weights(agents_config), mode="static", reason=reason)


def ewma(ics: list[float | None], *, half_life: float) -> float | None:
    """EW mean of a freshest-first series; None entries skipped, decay by lag."""
    lam = 0.5 ** (1.0 / half_life)
    num = den = 0.0
    for lag, ic in enumerate(ics):
        if ic is None:
            continue
        w = lam**lag
        num += w * ic
        den += w
    return num / den if den > 0 else None


def tilt_targets(ewics: dict[str, float], statics: dict[str, float], *, tau: float) -> dict[str, float]:
    """target ∝ static · exp(EWIC/τ). Zero EWICs reproduce the static prior."""
    raw = {a: statics[a] * math.exp(ewics[a] / tau) for a in ANALYSTS}
    total = sum(raw.values())
    return {a: raw[a] / total for a in ANALYSTS}


def _clip(x: float, lo: float, hi: float) -> float:
    return min(max(x, lo), hi)


def _redistribute(w: dict[str, float], lo: dict[str, float], hi: dict[str, float]) -> dict[str, float]:
    """Restore Σ=1 in one slack-proportional pass without breaching bounds."""
    s = sum(w.values())
    if s < 1.0:
        headroom = {a: hi[a] - w[a] for a in ANALYSTS}
        total = sum(headroom.values())
        w = {a: w[a] + (1.0 - s) * headroom[a] / total for a in ANALYSTS}
    elif s > 1.0:
        slack = {a: w[a] - lo[a] for a in ANALYSTS}
        total = sum(slack.values())
        w = {a: w[a] - (s - 1.0) * slack[a] / total for a in ANALYSTS}
    return w


def _round_exact(w: dict[str, float]) -> dict[str, float]:
    """Round to 6dp and push the residual onto the largest weight so the
    persisted dict sums to exactly 1.0."""
    rounded = {a: round(w[a], 6) for a in ANALYSTS}
    residual = round(1.0 - sum(rounded.values()), 6)
    if residual:
        largest = max(ANALYSTS, key=lambda a: rounded[a])
        rounded[largest] = round(rounded[largest] + residual, 6)
    return rounded


def project_weights(
    target: dict[str, float], prev: dict[str, float], *, floor: float, max_shift: float
) -> dict[str, float]:
    """Project target onto {Σ=1, floor ≤ w, |w−prev| ≤ max_shift}.

    Since Σprev = 1 the bounds are normally feasible. A config change (e.g. a
    raised floor) can make them infeasible; then the shift bounds are dropped
    for one week (jump straight to the floored target) with a warning.
    """
    lo = {a: max(floor, prev[a] - max_shift) for a in ANALYSTS}
    hi = {a: prev[a] + max_shift for a in ANALYSTS}
    if sum(lo.values()) > 1.0 or sum(hi.values()) < 1.0:
        logger.warning("Adaptive weights: shift bounds infeasible (config change?) — one-time jump to floored target")
        lo = {a: floor for a in ANALYSTS}
        hi = {a: 1.0 for a in ANALYSTS}
    w = {a: _clip(target[a], lo[a], hi[a]) for a in ANALYSTS}
    return _round_exact(_redistribute(w, lo, hi))


def alert_streaks(
    ic_series: dict[str, list[float | None]], *, half_life: float, min_history: int, streak_threshold: int
) -> list[dict]:
    """Consecutive most-recent evaluation points with EWIC ≤ 0, counting only
    windows that contain ≥ min_history valid ICs (an EWIC we would not trust
    for adaptation must not count toward an alert either)."""
    alerts = []
    for analyst in ANALYSTS:
        ics = ic_series.get(analyst, [])
        streak = 0
        for lag in range(len(ics)):
            window = ics[lag:]
            if sum(1 for x in window if x is not None) < min_history:
                break
            e = ewma(window, half_life=half_life)
            if e is None or e > 0:
                break
            streak += 1
        if streak >= streak_threshold:
            alerts.append({"analyst": analyst, "streak": streak, "ewic": ewma(ics, half_life=half_life)})
    return alerts


def compute_adaptive_weights(
    *,
    ic_series: dict[str, list[float | None]],
    prev_weights: dict[str, float],
    agents_config: AgentsConfig,
) -> AnalystWeightsReport:
    """Pure policy: series + previous weights + config → report."""
    cfg = agents_config.adaptive
    valid_weeks = {a: sum(1 for x in ic_series.get(a, []) if x is not None) for a in ANALYSTS}
    alerts = alert_streaks(
        ic_series,
        half_life=cfg.half_life_weeks,
        min_history=cfg.min_history_weeks,
        streak_threshold=cfg.alert_streak_weeks,
    )
    ewics = {a: ewma(ic_series.get(a, []), half_life=cfg.half_life_weeks) for a in ANALYSTS}
    if min(valid_weeks.values()) < cfg.min_history_weeks:
        report = static_report(agents_config, "insufficient_history")
        return report.model_copy(update={"ewics": ewics, "valid_weeks": valid_weeks, "alerts": alerts})
    statics = static_weights(agents_config)
    target = tilt_targets({a: ewics[a] for a in ANALYSTS}, statics, tau=cfg.ic_tilt_scale)
    weights = project_weights(target, prev_weights, floor=cfg.weight_floor, max_shift=cfg.max_weekly_shift)
    return AnalystWeightsReport(
        weights=weights, mode="adaptive", reason="ok", ewics=ewics, valid_weeks=valid_weeks, alerts=alerts
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/unit/equities/test_adaptive_weights.py -q`
Expected: PASS (loader tests come in Task 3)

- [ ] **Step 5: Stage**

```bash
git add app/modules/equities/adaptive_weights.py tests/unit/equities/test_adaptive_weights.py
```

---

### Task 3: DB loader `resolve_analyst_weights`

**Files:**
- Modify: `app/modules/equities/adaptive_weights.py`
- Modify: `tests/unit/equities/test_adaptive_weights.py`

- [ ] **Step 1: Write failing tests** — append to `tests/unit/equities/test_adaptive_weights.py`:

```python
import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.modules.equities.adaptive_weights import resolve_analyst_weights

BRANCH_ID = str(uuid.uuid4())


def _report_row(decision_date: date, ics: dict):
    row = MagicMock()
    row.decision_date = decision_date
    row.analyst_ics = ics
    return row


def _decision_row_with_weights(weights_payload):
    row = MagicMock()
    row.analyst_weights = weights_payload
    return row


def _loader_session(report_rows, decision_row):
    """Mock AsyncSession: first execute() returns attribution rows, second the
    latest decision row."""
    session = MagicMock()
    r1 = MagicMock()
    r1.scalars.return_value.all.return_value = report_rows
    r2 = MagicMock()
    r2.scalar_one_or_none.return_value = decision_row
    session.execute = AsyncMock(side_effect=[r1, r2])
    return session


class TestResolveAnalystWeights:
    async def test_disabled_short_circuits(self):
        cfg = _cfg(enabled=False)
        report = await resolve_analyst_weights(session=MagicMock(), branch_id=BRANCH_ID, agents_config=cfg)
        assert (report.mode, report.reason) == ("static", "disabled")

    async def test_no_session_short_circuits(self):
        report = await resolve_analyst_weights(session=None, branch_id=BRANCH_ID, agents_config=_cfg())
        assert (report.mode, report.reason) == ("static", "no_session")

    async def test_query_error_falls_back(self):
        session = MagicMock()
        session.execute = AsyncMock(side_effect=RuntimeError("db down"))
        report = await resolve_analyst_weights(session=session, branch_id=BRANCH_ID, agents_config=_cfg())
        assert (report.mode, report.reason) == ("static", "error")

    async def test_adaptive_path_reads_reports_and_prev_weights(self):
        rows = [
            _report_row(date(2026, 7, 6) - timedelta(weeks=i),
                        {"fundamentals": 0.4, "news": 0.0, "technical": -0.4, "composite": 0.9})
            for i in range(8)
        ]
        prev = {"weights": {"fundamentals": 0.65, "news": 0.19, "technical": 0.16}, "mode": "adaptive"}
        session = _loader_session(rows, _decision_row_with_weights(prev))
        report = await resolve_analyst_weights(session=session, branch_id=BRANCH_ID, agents_config=_cfg())
        assert report.mode == "adaptive"
        # anchored to prev weights: fund can reach at most 0.70
        assert report.weights["fundamentals"] <= 0.70 + 1e-9
        assert report.weights["fundamentals"] > 0.65  # still pushing up
        # the "composite" key must be ignored
        assert set(report.ewics) == set(ANALYSTS)

    async def test_missing_prev_weights_anchor_to_static(self):
        rows = [
            _report_row(date(2026, 7, 6), {"fundamentals": 0.4, "news": 0.0, "technical": -0.4})
            for _ in range(8)
        ]
        session = _loader_session(rows, _decision_row_with_weights(None))
        report = await resolve_analyst_weights(session=session, branch_id=BRANCH_ID, agents_config=_cfg())
        assert report.weights["fundamentals"] <= 0.65 + 1e-9

    async def test_missing_ic_keys_treated_as_none(self):
        rows = [_report_row(date(2026, 7, 6), {"fundamentals": 0.4}) for _ in range(8)]
        session = _loader_session(rows, None)
        report = await resolve_analyst_weights(session=session, branch_id=BRANCH_ID, agents_config=_cfg())
        assert (report.mode, report.reason) == ("static", "insufficient_history")
        assert report.valid_weeks["news"] == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/equities/test_adaptive_weights.py -q`
Expected: FAIL — `ImportError: cannot import name 'resolve_analyst_weights'`

- [ ] **Step 3: Implement** — append to `adaptive_weights.py`:

```python
async def resolve_analyst_weights(*, session, branch_id, agents_config: AgentsConfig) -> AnalystWeightsReport:
    """Resolve the weights for one pipeline run. Never raises — any failure
    returns the static fallback with a machine-readable reason."""
    cfg = agents_config.adaptive
    if not cfg.enabled:
        return static_report(agents_config, "disabled")
    if session is None:
        return static_report(agents_config, "no_session")
    try:
        bid = _uuid.UUID(branch_id) if isinstance(branch_id, str) else branch_id
        stmt = (
            select(AttributionReportModel)
            .where(AttributionReportModel.branch_id == bid)
            .order_by(AttributionReportModel.decision_date.desc())
            .limit(cfg.lookback_weeks)
        )
        rows = (await session.execute(stmt)).scalars().all()
        ic_series: dict[str, list[float | None]] = {a: [] for a in ANALYSTS}
        for row in rows:  # freshest first
            ics = row.analyst_ics or {}
            for a in ANALYSTS:
                v = ics.get(a)
                ic_series[a].append(float(v) if v is not None else None)

        prev_stmt = (
            select(PortfolioDecisionModel)
            .where(PortfolioDecisionModel.branch_id == bid)
            .order_by(PortfolioDecisionModel.decided_at.desc())
            .limit(1)
        )
        prev_row = (await session.execute(prev_stmt)).scalar_one_or_none()
        prev_weights = static_weights(agents_config)
        payload = getattr(prev_row, "analyst_weights", None) if prev_row is not None else None
        if payload and isinstance(payload.get("weights"), dict):
            candidate = {a: float(payload["weights"].get(a, 0.0)) for a in ANALYSTS}
            if all(v > 0 for v in candidate.values()):
                prev_weights = candidate

        return compute_adaptive_weights(ic_series=ic_series, prev_weights=prev_weights, agents_config=agents_config)
    except Exception:
        logger.warning("Adaptive weights resolution failed — falling back to static", exc_info=True)
        return static_report(agents_config, "error")
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/unit/equities/test_adaptive_weights.py -q`
Expected: PASS

- [ ] **Step 5: Stage**

```bash
git add app/modules/equities/adaptive_weights.py tests/unit/equities/test_adaptive_weights.py
```

---

### Task 4: `PortfolioManager` weight override

**Files:**
- Modify: `app/modules/equities/agents/portfolio_manager.py:10-54`
- Test: `tests/unit/equities/test_portfolio_manager.py`

- [ ] **Step 1: Write failing test** — append to `tests/unit/equities/test_portfolio_manager.py` (reuse that file's existing config/signal helpers; if it builds `PortfolioManager(AgentsConfig(), PortfolioConfig())` directly, mirror that):

```python
def test_analyst_weights_override_changes_composite():
    from app.modules.equities.config import AgentsConfig, PortfolioConfig
    from app.modules.equities.models import StockSignal

    signals = [
        StockSignal(symbol="AAPL", analyst_type="fundamentals", bullish_score=10, confidence=10, summary=""),
        StockSignal(symbol="AAPL", analyst_type="news", bullish_score=2, confidence=10, summary=""),
        StockSignal(symbol="AAPL", analyst_type="technical", bullish_score=2, confidence=10, summary=""),
    ]
    default_pm = PortfolioManager(AgentsConfig(), PortfolioConfig())
    override_pm = PortfolioManager(
        AgentsConfig(),
        PortfolioConfig(),
        analyst_weights={"fundamentals": 0.10, "news": 0.45, "technical": 0.45},
    )
    default_score = default_pm.compute_composite_scores(signals)[0].composite_score
    override_score = override_pm.compute_composite_scores(signals)[0].composite_score
    assert default_score == 0.6 * 10 + 0.2 * 2 + 0.2 * 2
    assert override_score == 0.1 * 10 + 0.45 * 2 + 0.45 * 2
```

(Adjust the `StockSignal` constructor kwargs to whatever `app/modules/equities/models.py` defines — check the model first; `summary` may be optional.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/equities/test_portfolio_manager.py -q`
Expected: FAIL — unexpected keyword argument `analyst_weights`

- [ ] **Step 3: Implement** — in `portfolio_manager.py`:

```python
    def __init__(
        self,
        agents_config: AgentsConfig,
        portfolio_config: PortfolioConfig,
        analyst_weights: dict[str, float] | None = None,
    ) -> None:
        self.agents_config = agents_config
        self.portfolio_config = portfolio_config
        # Adaptive weights (2026-07-16 spec) override the static config when
        # provided; both are keyed fundamentals/news/technical and sum to 1.
        self.analyst_weights = analyst_weights or {
            "fundamentals": agents_config.weight_fundamentals,
            "news": agents_config.weight_news,
            "technical": agents_config.weight_technical,
        }
```

and in `compute_composite_scores` replace the inline `weight_map = {...}` block with:

```python
        weight_map = self.analyst_weights
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/unit/equities/test_portfolio_manager.py tests/unit/equities/test_graph.py -q`
Expected: PASS (graph tests construct PM without the new arg — defaults preserve behavior)

- [ ] **Step 5: Stage**

```bash
git add app/modules/equities/agents/portfolio_manager.py tests/unit/equities/test_portfolio_manager.py
```

---

### Task 5: ORM column + migration

**Files:**
- Modify: `app/db/models.py` (`PortfolioDecisionModel`, after `composite_scores`)
- Create: `app/db/migrations/versions/c4d2a91b7e55_add_analyst_weights_to_portfolio_decisions.py`

- [ ] **Step 1: Add ORM column**

```python
    # Weights the composite actually used this run (2026-07-16 adaptive
    # weights spec): {"weights": {...}, "mode", "reason", "ewics", "valid_weeks"}.
    # Nullable: rows before the feature carry NULL.
    analyst_weights: Mapped[dict | None] = mapped_column(JSONB)
```

- [ ] **Step 2: Create migration** (mirror the header style of `b91f2a6c3d44_add_attribution_reports.py`):

```python
"""add analyst_weights to portfolio_decisions

Revision ID: c4d2a91b7e55
Revises: b91f2a6c3d44
Create Date: 2026-07-16
"""

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c4d2a91b7e55"
down_revision: Union[str, None] = "b91f2a6c3d44"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column("portfolio_decisions", sa.Column("analyst_weights", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("portfolio_decisions", "analyst_weights")
```

- [ ] **Step 3: Verify import + suite still green**

Run: `.venv/bin/pytest tests/unit -q`
Expected: PASS (column is additive)

- [ ] **Step 4: Stage**

```bash
git add app/db/models.py app/db/migrations/versions/c4d2a91b7e55_add_analyst_weights_to_portfolio_decisions.py
```

---

### Task 6: Service wiring (resolve → PM → RunResult → persist)

**Files:**
- Modify: `app/modules/equities/models.py` (RunResult)
- Modify: `app/modules/equities/service.py`
- Test: `tests/unit/equities/test_persist_artifacts.py`

- [ ] **Step 1: Write failing test** — append to `tests/unit/equities/test_persist_artifacts.py`:

```python
    async def test_analyst_weights_report_persisted_on_decision(self):
        from app.modules.equities.adaptive_weights import AnalystWeightsReport

        service = EquitiesBranchService(config=EquitiesConfig())
        session = _make_session()
        report = AnalystWeightsReport(
            weights={"fundamentals": 0.65, "news": 0.19, "technical": 0.16},
            mode="adaptive",
            reason="ok",
            ewics={"fundamentals": 0.09, "news": 0.0, "technical": -0.08},
            valid_weeks={"fundamentals": 9, "news": 9, "technical": 9},
        )
        await service._persist_run_artifacts(
            session,
            "growth",
            str(uuid.uuid4()),
            universe=[],
            screened=[],
            signals=[],
            scores=[],
            orders=[],
            current_positions={},
            targets=[],
            analyst_weights_report=report,
        )
        decision = _added_decision(session)
        assert decision.analyst_weights["weights"]["fundamentals"] == 0.65
        assert decision.analyst_weights["mode"] == "adaptive"

    async def test_absent_weights_report_persists_null(self):
        service = EquitiesBranchService(config=EquitiesConfig())
        session = _make_session()
        await service._persist_run_artifacts(
            session,
            "growth",
            str(uuid.uuid4()),
            universe=[],
            screened=[],
            signals=[],
            scores=[],
            orders=[],
            current_positions={},
            targets=[],
        )
        assert _added_decision(session).analyst_weights is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/equities/test_persist_artifacts.py -q`
Expected: FAIL — unexpected keyword argument `analyst_weights_report`

- [ ] **Step 3: Implement**

`app/modules/equities/models.py` — import and field:

```python
from app.modules.equities.adaptive_weights import AnalystWeightsReport
```

(place the import with the other app imports; no circularity: `adaptive_weights` imports only `app.db.models` + `config`), then in `RunResult`:

```python
    analyst_weights_report: AnalystWeightsReport | None = None
```

`app/modules/equities/service.py`:

1. Import: `from app.modules.equities.adaptive_weights import resolve_analyst_weights`
2. In `run_pipeline`, immediately before `pm = PortfolioManager(...)`:

```python
        # Adaptive composite weights (2026-07-16 spec). Resolution never
        # raises; backtests (session=None) and thin history fall back static.
        weights_report = await resolve_analyst_weights(
            session=session, branch_id=branch_id, agents_config=self.config.agents
        )
        if weights_report.mode == "adaptive":
            logger.info(
                "Adaptive weights for %s: %s (EWICs %s)", branch_name, weights_report.weights, weights_report.ewics
            )

        pm = PortfolioManager(
            agents_config=self.config.agents,
            portfolio_config=self.config.portfolio,
            analyst_weights=weights_report.weights,
        )
```

3. Pass through to persistence and result:

```python
            await self._persist_run_artifacts(
                ...,
                targets=targets,
                analyst_weights_report=weights_report,
            )

        return RunResult(
            ...,
            analyst_weights_report=weights_report,
        )
```

4. `_persist_run_artifacts` gains `analyst_weights_report=None` kwarg; when building `PortfolioDecisionModel` add:

```python
                    analyst_weights=(
                        analyst_weights_report.model_dump(mode="json") if analyst_weights_report else None
                    ),
```

- [ ] **Step 4: Run to verify pass + no service regressions**

Run: `.venv/bin/pytest tests/unit/equities -q`
Expected: PASS. Watch `test_service_run_id.py` / `test_service_rankers.py`: they drive `run_pipeline` with mock sessions — `resolve_analyst_weights` must swallow their mock-session weirdness into `reason="error"` (it does: bare `except Exception`).

- [ ] **Step 5: Stage**

```bash
git add app/modules/equities/models.py app/modules/equities/service.py tests/unit/equities/test_persist_artifacts.py
```

---

### Task 7: Composite IC in attribution (measurement only)

**Files:**
- Modify: `app/modules/equities/attribution.py` (`compute_report`, `compute_and_persist`)
- Test: `tests/unit/equities/test_attribution.py`, `tests/unit/equities/test_attribution_engine.py`

- [ ] **Step 1: Write failing tests.** In `test_attribution.py` (match its existing fixtures/style — it calls `compute_report` with explicit kwargs):

```python
def test_composite_ic_computed_from_composite_scores():
    prices = {s: [(date(2026, 6, 1), 100.0), (date(2026, 6, 8), px)] for s, px in
              [("A", 110.0), ("B", 108.0), ("C", 106.0), ("D", 104.0), ("E", 102.0)]}
    composite_scores = {
        "A": {"score": 9.0, "confidence": 9.0},
        "B": {"score": 8.0, "confidence": 8.0},
        "C": {"score": 7.0, "confidence": 7.0},
        "D": {"score": 6.0, "confidence": 6.0},
        "E": {"score": 5.0, "confidence": 5.0},
    }
    report = compute_report(
        branch_name="growth",
        decision_date=date(2026, 6, 1),
        as_of=date(2026, 6, 8),
        weights={"A": 1.0},
        signals=[],
        prices=prices,
        benchmark_symbol="VOOG",
        composite_scores=composite_scores,
    )
    # conviction 81..25 perfectly rank-aligned with returns 10%..2%
    assert report.analyst_ics["composite"] == 1.0


def test_composite_ic_requires_min_samples():
    prices = {s: [(date(2026, 6, 1), 100.0), (date(2026, 6, 8), 101.0)] for s in ("A", "B")}
    report = compute_report(
        branch_name="growth",
        decision_date=date(2026, 6, 1),
        as_of=date(2026, 6, 8),
        weights={"A": 1.0},
        signals=[],
        prices=prices,
        benchmark_symbol="VOOG",
        composite_scores={"A": {"score": 9, "confidence": 9}, "B": {"score": 5, "confidence": 5}},
    )
    assert report.analyst_ics["composite"] is None


def test_no_composite_scores_no_composite_key():
    report = compute_report(
        branch_name="growth",
        decision_date=date(2026, 6, 1),
        as_of=date(2026, 6, 8),
        weights={},
        signals=[],
        prices={},
        benchmark_symbol="VOOG",
    )
    assert "composite" not in report.analyst_ics
```

In `test_attribution_engine.py`, extend `test_compute_returns_report_and_persists_new_row`'s decision fixture (`_decision_row`) — set `row.composite_scores = {s: {"score": i + 1, "confidence": 5} for i, s in enumerate("ABCDE")}` and assert the persisted/returned report has a `"composite"` key in `analyst_ics`.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/equities/test_attribution.py tests/unit/equities/test_attribution_engine.py -q`
Expected: FAIL — unexpected keyword argument `composite_scores`

- [ ] **Step 3: Implement.** `compute_report` gains `composite_scores: dict | None = None`; after the per-analyst IC loop:

```python
    # Composite conviction IC (measurement for the adaptive-weights loop —
    # never read back by the weights policy, which uses the analyst keys only).
    if composite_scores:
        pairs = []
        for sym, cs in composite_scores.items():
            r = _window_return(prices.get(sym, []), decision_date, as_of)
            if r is None or not cs:
                continue
            conviction = float(cs.get("score", 0)) * float(cs.get("confidence", 0))
            pairs.append((conviction, r))
        if len(pairs) < MIN_IC_SAMPLES:
            analyst_ics["composite"] = None
        else:
            analyst_ics["composite"] = spearman([p[0] for p in pairs], [p[1] for p in pairs])
```

In `compute_and_persist`: include composite symbols in the price fetch and pass the scores through:

```python
        symbols = set(weights) | {s["symbol"] for s in signals} | set(decision.composite_scores or {}) | {benchmark, "SPY"}
        ...
        report = compute_report(
            ...,
            composite_scores=decision.composite_scores or {},
        )
```

Digest (in `weekly_runner.render_digest`, same line that prints the ICs) — append composite to the IC segment:

```python
                    f"tech {_fmt_ic(ics.get('technical'))} / "
                    f"comp {_fmt_ic(ics.get('composite'))}"
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/unit/equities/test_attribution.py tests/unit/equities/test_attribution_engine.py tests/unit/equities/test_weekly_runner.py -q`
Expected: PASS (weekly-runner digest tests may assert the old IC line — update those assertions to include `comp n/a` where applicable)

- [ ] **Step 5: Stage**

```bash
git add app/modules/equities/attribution.py app/modules/equities/weekly_runner.py tests/unit/equities/test_attribution.py tests/unit/equities/test_attribution_engine.py tests/unit/equities/test_weekly_runner.py
```

---

### Task 8: Summary + digest lines

**Files:**
- Modify: `app/modules/equities/weekly_runner.py` (`WeeklyRunSummary`, `execute`, `render_digest`)
- Test: `tests/unit/equities/test_weekly_runner.py`

- [ ] **Step 1: Write failing tests** — append to `test_weekly_runner.py` (reuse its existing summary-builder helpers if present):

```python
from app.modules.equities.adaptive_weights import AnalystWeightsReport


def _weights_report(mode="adaptive", reason="ok", alerts=None):
    return AnalystWeightsReport(
        weights={"fundamentals": 0.65, "news": 0.19, "technical": 0.16},
        mode=mode,
        reason=reason,
        ewics={"fundamentals": 0.09, "news": 0.0, "technical": -0.08},
        valid_weeks={"fundamentals": 9, "news": 9, "technical": 9},
        alerts=alerts or [],
    )


def test_digest_renders_adaptive_weights_line():
    s = WeeklyRunSummary(
        run_id="r", branch_name="growth", status="completed", universe_count=1,
        screened_count=1, orders_placed=1, trades_executed=1, duration_seconds=1.0,
        analyst_weights_report=_weights_report(),
    )
    digest = render_digest([s], run_date=date(2026, 7, 20))
    assert "- Weights: fund 0.65 / news 0.19 / tech 0.16 (adaptive, 9 wks; EWIC fund +0.09 / news +0.00 / tech -0.08)" in digest


def test_digest_renders_static_reason():
    s = WeeklyRunSummary(
        run_id="r", branch_name="growth", status="completed", universe_count=1,
        screened_count=1, orders_placed=1, trades_executed=1, duration_seconds=1.0,
        analyst_weights_report=AnalystWeightsReport(
            weights={"fundamentals": 0.60, "news": 0.20, "technical": 0.20},
            mode="static", reason="insufficient_history",
            valid_weeks={"fundamentals": 4, "news": 4, "technical": 4},
        ),
    )
    digest = render_digest([s], run_date=date(2026, 7, 20))
    assert "- Weights: fund 0.60 / news 0.20 / tech 0.20 (static — insufficient_history, min 4 valid wks)" in digest


def test_digest_renders_ic_alert():
    s = WeeklyRunSummary(
        run_id="r", branch_name="value", status="completed", universe_count=1,
        screened_count=1, orders_placed=1, trades_executed=1, duration_seconds=1.0,
        analyst_weights_report=_weights_report(alerts=[{"analyst": "technical", "streak": 4, "ewic": -0.17}]),
    )
    digest = render_digest([s], run_date=date(2026, 7, 20))
    assert "- ⚠️ technical rolling IC ≤ 0 for 4 consecutive weeks (EWIC -0.17)" in digest
```

Note the ASCII hyphen-minus in `-0.08` / `-0.17`: `_fmt_ic` uses `%+.2f`, which renders ASCII minus. Keep assertions consistent with `_fmt_ic`.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/equities/test_weekly_runner.py -q`
Expected: FAIL — unexpected keyword `analyst_weights_report`

- [ ] **Step 3: Implement.**

`WeeklyRunSummary` gains:

```python
    analyst_weights_report: AnalystWeightsReport | None = None
```

with `from app.modules.equities.adaptive_weights import AnalystWeightsReport` imported under `TYPE_CHECKING`... **no** — it's needed at runtime by dataclass default handling only as an annotation; keep the `from __future__ import annotations` string-annotation behavior and import it under `TYPE_CHECKING` alongside `AttributionReport`. In `execute`, after the trading run:

```python
        return WeeklyRunSummary(
            ...,
            analyst_weights_report=getattr(result, "analyst_weights_report", None),
        )
```

`render_digest`, inside the `status == "completed"` branch after the attribution block:

```python
            if s.analyst_weights_report is not None:
                w = s.analyst_weights_report
                ww = w.weights
                weights_str = (
                    f"fund {ww.get('fundamentals', 0):.2f} / news {ww.get('news', 0):.2f} / "
                    f"tech {ww.get('technical', 0):.2f}"
                )
                if w.mode == "adaptive":
                    wks = min(w.valid_weeks.values()) if w.valid_weeks else 0
                    ew = w.ewics
                    lines.append(
                        f"- Weights: {weights_str} (adaptive, {wks} wks; "
                        f"EWIC fund {_fmt_ic(ew.get('fundamentals'))} / "
                        f"news {_fmt_ic(ew.get('news'))} / "
                        f"tech {_fmt_ic(ew.get('technical'))})"
                    )
                else:
                    detail = f" — {w.reason}"
                    if w.reason == "insufficient_history" and w.valid_weeks:
                        detail += f", min {min(w.valid_weeks.values())} valid wks"
                    lines.append(f"- Weights: {weights_str} (static{detail})")
                for alert in w.alerts:
                    lines.append(
                        f"- ⚠️ {alert['analyst']} rolling IC ≤ 0 for {alert['streak']} "
                        f"consecutive weeks (EWIC {alert['ewic']:+.2f})"
                    )
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/unit/equities/test_weekly_runner.py tests/unit/equities/test_digest_portfolio_report.py -q`
Expected: PASS

- [ ] **Step 5: Stage**

```bash
git add app/modules/equities/weekly_runner.py tests/unit/equities/test_weekly_runner.py
```

---

### Task 9: Weekly CLI — attribution before trading

**Files:**
- Modify: `scripts/run_weekly_pipeline.py` (`_run_one_branch`)
- Create: `tests/unit/scripts/test_weekly_cli_ordering.py` (create `tests/unit/scripts/__init__.py` if missing)

- [ ] **Step 1: Write failing test** — create `tests/unit/scripts/test_weekly_cli_ordering.py`:

```python
"""Attribution must run BEFORE the trading run (2026-07-16 spec) and stay non-fatal."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from app.modules.equities.weekly_runner import WeeklyRunSummary


def _summary(status="completed"):
    return WeeklyRunSummary(
        run_id="r", branch_name="growth", status=status, universe_count=0,
        screened_count=0, orders_placed=0, trades_executed=0, duration_seconds=0.0,
    )


def _session_factory():
    ctx = MagicMock()
    session = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    begin = MagicMock()
    begin.__aenter__ = AsyncMock(return_value=None)
    begin.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin)
    return MagicMock(return_value=ctx)


async def test_attribution_runs_before_trading_and_attaches():
    import scripts.run_weekly_pipeline as cli

    calls: list[str] = []
    engine = MagicMock()
    engine.compute_and_persist = AsyncMock(side_effect=lambda *a, **k: calls.append("attribution") or MagicMock())

    runner = MagicMock()

    async def _execute(**kwargs):
        calls.append("trading")
        return _summary()

    runner.execute = AsyncMock(side_effect=_execute)

    with (
        patch.object(cli, "get_equities_service", return_value=MagicMock()),
        patch.object(cli, "async_session_factory", _session_factory()),
        patch.object(cli, "resolve_branch_id", AsyncMock(return_value="b-id")),
        patch.object(cli, "AttributionEngine", return_value=engine),
        patch.object(cli, "WeeklyRunner", return_value=runner) as runner_cls,
        patch.object(cli, "_mark_snapshot_and_report", AsyncMock()),
    ):
        runner_cls.configure_service = MagicMock()
        summary = await cli._run_one_branch(branch_name="growth", top_n=5, force_retry=False, dry_run=False)

    assert calls == ["attribution", "trading"]
    assert summary.attribution is not None


async def test_attribution_failure_does_not_block_trading():
    import scripts.run_weekly_pipeline as cli

    engine = MagicMock()
    engine.compute_and_persist = AsyncMock(side_effect=RuntimeError("yfinance down"))
    runner = MagicMock()
    runner.execute = AsyncMock(return_value=_summary())

    with (
        patch.object(cli, "get_equities_service", return_value=MagicMock()),
        patch.object(cli, "async_session_factory", _session_factory()),
        patch.object(cli, "resolve_branch_id", AsyncMock(return_value="b-id")),
        patch.object(cli, "AttributionEngine", return_value=engine),
        patch.object(cli, "WeeklyRunner", return_value=runner) as runner_cls,
        patch.object(cli, "_mark_snapshot_and_report", AsyncMock()),
    ):
        runner_cls.configure_service = MagicMock()
        summary = await cli._run_one_branch(branch_name="growth", top_n=5, force_retry=False, dry_run=False)

    assert summary.status == "completed"
    assert summary.attribution is None


async def test_dry_run_skips_attribution():
    import scripts.run_weekly_pipeline as cli

    engine = MagicMock()
    engine.compute_and_persist = AsyncMock()

    with (
        patch.object(cli, "get_equities_service", return_value=MagicMock()),
        patch.object(cli, "async_session_factory", _session_factory()),
        patch.object(cli, "resolve_branch_id", AsyncMock(return_value="b-id")),
        patch.object(cli, "AttributionEngine", return_value=engine),
    ):
        summary = await cli._run_one_branch(branch_name="growth", top_n=5, force_retry=False, dry_run=True)

    engine.compute_and_persist.assert_not_awaited()
    assert summary.status == "skipped"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/scripts/test_weekly_cli_ordering.py -q`
Expected: FAIL — `calls == ["trading", "attribution"]` (current order) on the first test

- [ ] **Step 3: Implement.** In `_run_one_branch`, move the Phase-D block from after `runner.execute` to before it (after the `dry_run` early return, after `run_date` is set), storing into a local:

```python
    # Phase D: score last week's decision BEFORE trading (2026-07-16 spec) so
    # this run's adaptive weights see the freshest IC, and so the IC series
    # keeps accruing even when the trading run later fails. Own session;
    # never allowed to block the trading run. Which decision gets scored is
    # unchanged (engine selects decided_at < midnight of run_date).
    attribution_report = None
    try:
        engine = AttributionEngine(data_service=equities_service.data_service)
        async with async_session_factory() as session, session.begin():
            attribution_report = await engine.compute_and_persist(
                session,
                branch_id=branch_id,
                branch_name=branch_name,
                as_of=run_date,
            )
    except Exception:
        logger.warning("Attribution failed for %s — continuing", branch_name, exc_info=True)

    ...  # runner construction + execute (unchanged)

    summary.attribution = attribution_report
```

Delete the old post-run attribution block.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/unit/scripts/test_weekly_cli_ordering.py tests/unit/equities -q`
Expected: PASS

- [ ] **Step 5: Stage**

```bash
git add scripts/run_weekly_pipeline.py tests/unit/scripts/
```

---

### Task 10: Preview script

**Files:**
- Create: `scripts/preview_adaptive_weights.py`

Read-only CLI so the user can see exactly what next Monday would do, against whatever `HEDGE_DATABASE_URL` points at. No unit tests (all logic lives in the tested module); ruff-clean.

- [ ] **Step 1: Implement**

```python
"""Preview the adaptive analyst weights the next weekly run would use.

Read-only: SELECTs attribution_reports + the latest portfolio_decisions row
per branch, runs the same resolution code as the pipeline, prints the result.

    python -m scripts.preview_adaptive_weights --branches growth value
"""

from __future__ import annotations

import argparse
import asyncio

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

from app.db.connection import async_session_factory  # noqa: E402
from app.db.models import AttributionReportModel  # noqa: E402
from app.modules.equities.adaptive_weights import ANALYSTS, resolve_analyst_weights  # noqa: E402
from app.modules.equities.config import EquitiesConfig  # noqa: E402
from scripts.common import resolve_branch_id  # noqa: E402


def _fmt(x) -> str:
    return "  n/a" if x is None else f"{float(x):+.2f}"


async def _preview(branch_name: str) -> None:
    config = EquitiesConfig()
    async with async_session_factory() as session:
        branch_id = await resolve_branch_id(session, branch_name)
        stmt = (
            select(AttributionReportModel)
            .where(AttributionReportModel.branch_id == branch_id)
            .order_by(AttributionReportModel.decision_date.desc())
            .limit(config.agents.adaptive.lookback_weeks)
        )
        rows = (await session.execute(stmt)).scalars().all()
        print(f"\n=== {branch_name} — {len(rows)} attribution report(s) ===")
        print("decision_date  fund   news   tech   comp")
        for row in rows:
            ics = row.analyst_ics or {}
            print(
                f"{row.decision_date}     {_fmt(ics.get('fundamentals'))}  {_fmt(ics.get('news'))}  "
                f"{_fmt(ics.get('technical'))}  {_fmt(ics.get('composite'))}"
            )
        report = await resolve_analyst_weights(session=session, branch_id=branch_id, agents_config=config.agents)
        print(f"mode={report.mode} reason={report.reason} valid_weeks={report.valid_weeks}")
        print("EWICs: " + "  ".join(f"{a} {_fmt(report.ewics.get(a))}" for a in ANALYSTS))
        print("Next-run weights: " + "  ".join(f"{a} {report.weights[a]:.4f}" for a in ANALYSTS))
        for alert in report.alerts:
            print(f"ALERT: {alert['analyst']} rolling IC <= 0 for {alert['streak']} weeks (EWIC {_fmt(alert['ewic'])})")


async def _main(branches: list[str]) -> None:
    for b in branches:
        await _preview(b)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branches", nargs="*", default=["growth", "value"])
    args = parser.parse_args()
    asyncio.run(_main(args.branches))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it imports and lints**

Run: `.venv/bin/python -c "import scripts.preview_adaptive_weights" && .venv/bin/ruff check scripts/preview_adaptive_weights.py`
Expected: no output (clean)

- [ ] **Step 3: Stage**

```bash
git add scripts/preview_adaptive_weights.py
```

---

### Task 11: Docs, full verification, stage everything

**Files:**
- Modify: `CLAUDE.md` (gotchas + weekly-pipeline sections)
- Modify: `docs/superpowers/specs/2026-07-16-adaptive-analyst-weights-design.md` (changelog)

- [ ] **Step 1: CLAUDE.md** — add to Gotchas:

```markdown
- **Composite weights are adaptive per-branch since 2026-07** — `run_pipeline` resolves weights from trailing `attribution_reports` ICs (tilt off the static prior, floor 0.10, max ±0.05/week; `AgentsConfig.adaptive`). Static `weight_*` config is the prior AND the fallback (disabled flag, `session=None` — i.e. all backtests — thin history, or any resolution error). The weights actually used are persisted on `portfolio_decisions.analyst_weights`; preview next Monday's weights with `python -m scripts.preview_adaptive_weights`.
- **Weekly attribution runs BEFORE the trading run** (since 2026-07) so adaptive weights see last week's decision scored through this morning, and the IC series persists even when trading fails. `attribution_reports.analyst_ics` also carries a `"composite"` conviction IC (measurement only — the weights policy reads the three analyst keys).
```

- [ ] **Step 2: Spec changelog** — append:

```markdown
- **2026-07-16** — Implemented (staged for user review; ships in the 2026-07-20 cycle).
```

- [ ] **Step 3: Full verification**

Run: `.venv/bin/pytest tests/unit -q` — expected: all pass (1098 existing + ~30 new)
Run: `.venv/bin/ruff check app/ tests/ scripts/ && .venv/bin/ruff format --check app/ tests/ scripts/` — expected: clean

- [ ] **Step 4: Stage all + review**

```bash
git add CLAUDE.md docs/
git status   # confirm every touched file is staged, nothing unexpected
```

Then run the code-review pass (superpowers:requesting-code-review) on the staged diff and fix findings before handing off.

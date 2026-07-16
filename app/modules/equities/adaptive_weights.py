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
    """Restore Σ=1 in one slack-proportional pass without breaching bounds.

    Works because the deficit (surplus) never exceeds the total headroom
    (slack), so each component's adjustment stays within its own bound.
    """
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
    # Infeasible when the sums can't reach 1 OR any per-component interval is
    # empty (a raised floor can push lo above prev+shift while sums stay fine).
    if sum(lo.values()) > 1.0 or sum(hi.values()) < 1.0 or any(lo[a] > hi[a] for a in ANALYSTS):
        logger.warning("Adaptive weights: shift bounds infeasible (config change?) — one-time jump to floored target")
        lo = {a: floor for a in ANALYSTS}
        hi = {a: 1.0 for a in ANALYSTS}
    w = {a: _clip(target[a], lo[a], hi[a]) for a in ANALYSTS}
    return _round_exact(_redistribute(w, lo, hi))


def alert_streaks(
    ic_series: dict[str, list[float | None]], *, half_life: float, min_history: int, streak_threshold: int
) -> list[dict]:
    """Consecutive most-recent evaluation points with EWIC ≤ 0.

    Only windows containing ≥ min_history valid ICs count — an EWIC we would
    not trust for adaptation must not count toward an alert either.
    """
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
    """Pure policy: IC series + previous weights + config → report."""
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
            # Anchor to the recovered weights only if they look like a sane
            # simplex point; a corrupt row must not distort the shift bounds.
            if all(v > 0 for v in candidate.values()) and 0.99 < sum(candidate.values()) < 1.01:
                prev_weights = candidate

        return compute_adaptive_weights(ic_series=ic_series, prev_weights=prev_weights, agents_config=agents_config)
    except Exception:
        logger.warning("Adaptive weights resolution failed — falling back to static", exc_info=True)
        return static_report(agents_config, "error")

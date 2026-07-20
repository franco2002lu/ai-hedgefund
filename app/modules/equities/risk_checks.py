"""Post-run portfolio invariant checks (Theme A1).

Pure logic: evaluates marked portfolio state against invariants and returns
RiskAlert drafts. Persistence and digest rendering happen in the weekly CLI.
"""

from __future__ import annotations

from app.common.enums import RiskAlertLevel
from app.common.events.risk import RiskAlertEvent
from app.common.interfaces.repositories import EventLogRepository, RiskAlertRepository
from app.common.models.risk import RiskAlert
from app.modules.equities.config import PortfolioConfig

# Check parameters — invariant tolerances, not strategy knobs, so they are
# module constants rather than PortfolioConfig fields.
CASH_PCT_WARN = 0.05
POSITION_WEIGHT_TOLERANCE = 0.005


def evaluate_post_run_invariants(
    *,
    cash: float,
    nav: float,
    position_weights: dict[str, float],
    portfolio_config: PortfolioConfig,
    branch_id: str,
    branch_name: str,
) -> list[RiskAlert]:
    """Evaluate the marked book right after a rebalance.

    Invariants: cash must not be negative (long-only cash account), cash must
    not balloon (the signature of mass BUY rejections), and no position may
    exceed the configured cap beyond fill-slippage tolerance.
    """
    alerts: list[RiskAlert] = []

    # Deliberately zero-tolerance: with the fill-time cash gate + 1% sizing
    # buffer, ANY overdraft (even cents) means an execution-logic flaw, not
    # noise.
    if cash < 0:
        alerts.append(
            RiskAlert(
                level=RiskAlertLevel.CRITICAL,
                source=branch_id,
                metric="cash",
                current_value=cash,
                threshold=0.0,
                message=f"{branch_name}: cash is negative (-${abs(cash):,.2f}) — long-only book must not overdraw",
                action_required="Investigate order sizing/execution (see 2026-06-22 leverage incident).",
                affected_branches=[branch_name],
            )
        )

    if nav > 0 and cash / nav > CASH_PCT_WARN:
        alerts.append(
            RiskAlert(
                level=RiskAlertLevel.WARNING,
                source=branch_id,
                metric="cash_pct",
                current_value=cash / nav,
                threshold=CASH_PCT_WARN,
                message=(
                    f"{branch_name}: cash is {cash / nav:.1%} of NAV — underinvested; possible mass BUY rejections"
                ),
                affected_branches=[branch_name],
            )
        )

    cap = portfolio_config.max_position_weight
    for symbol, weight in sorted(position_weights.items()):
        if weight > cap + POSITION_WEIGHT_TOLERANCE:
            alerts.append(
                RiskAlert(
                    level=RiskAlertLevel.CRITICAL,
                    source=branch_id,
                    metric="position_weight",
                    current_value=weight,
                    threshold=cap,
                    message=f"{branch_name}: {symbol} is {weight:.1%} of NAV (cap {cap:.0%})",
                    affected_branches=[branch_name],
                )
            )

    return alerts


async def persist_alerts(
    alerts: list[RiskAlert],
    *,
    repo: RiskAlertRepository,
    event_log: EventLogRepository,
) -> None:
    """Persist alerts as risk_alerts rows plus risk.alert event-log entries.

    The caller owns transaction scoping (the weekly CLI wraps this in a
    savepoint).
    """
    for alert in alerts:
        await repo.create(alert)
        await event_log.append(
            RiskAlertEvent(
                source=alert.source,
                level=alert.level,
                metric=alert.metric,
                current_value=alert.current_value,
                threshold=alert.threshold,
                message=alert.message,
                action_required=alert.action_required,
                affected_branches=alert.affected_branches,
            )
        )


def to_digest_dicts(alerts: list[RiskAlert]) -> list[dict]:
    """Project alerts to the digest's rendering shape (see PortfolioReport.risk_alerts)."""
    return [{"level": str(alert.level), "message": alert.message} for alert in alerts]

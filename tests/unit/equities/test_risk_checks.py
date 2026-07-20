"""Post-run invariant checks: cash floor, cash-pct ceiling, position cap."""

from app.common.enums import RiskAlertLevel
from app.modules.equities.config import PortfolioConfig
from app.modules.equities.risk_checks import (
    CASH_PCT_WARN,
    POSITION_WEIGHT_TOLERANCE,
    evaluate_post_run_invariants,
)


def _run(cash=10_000.0, nav=1_000_000.0, weights=None, cap=0.50):
    return evaluate_post_run_invariants(
        cash=cash,
        nav=nav,
        position_weights=weights or {},
        portfolio_config=PortfolioConfig(max_position_weight=cap),
        branch_id="b-1",
        branch_name="growth",
    )


def test_clean_portfolio_produces_no_alerts():
    assert _run(cash=10_000.0, weights={"AAPL": 0.05}) == []


def test_negative_cash_is_critical():
    alerts = _run(cash=-55_473.33)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.level == RiskAlertLevel.CRITICAL
    assert a.metric == "cash"
    assert a.current_value == -55_473.33
    assert a.threshold == 0.0
    assert "growth" in a.message
    assert a.affected_branches == ["growth"]


def test_zero_cash_is_fine():
    assert _run(cash=0.0) == []


def test_high_cash_pct_is_warning():
    alerts = _run(cash=60_000.0)  # 6% of 1M > 5%
    assert len(alerts) == 1
    assert alerts[0].level == RiskAlertLevel.WARNING
    assert alerts[0].metric == "cash_pct"
    assert alerts[0].threshold == CASH_PCT_WARN


def test_cash_pct_at_threshold_is_fine():
    assert _run(cash=50_000.0) == []  # exactly 5%: not a breach


def test_position_above_cap_plus_tolerance_is_critical():
    alerts = _run(weights={"BAC": 0.16, "OK": 0.05}, cap=0.10)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.level == RiskAlertLevel.CRITICAL
    assert a.metric == "position_weight"
    assert "BAC" in a.message
    assert a.current_value == 0.16
    assert a.threshold == 0.10


def test_position_within_tolerance_is_fine():
    # cap 0.10 + tolerance 0.005 → 0.104 passes
    assert POSITION_WEIGHT_TOLERANCE == 0.005
    assert _run(weights={"AAPL": 0.104}, cap=0.10) == []


def test_multiple_breaches_stack():
    alerts = _run(cash=-1.0, weights={"A": 0.60, "B": 0.55}, cap=0.50)
    metrics = sorted(a.metric for a in alerts)
    assert metrics == ["cash", "position_weight", "position_weight"]


def test_zero_nav_does_not_divide():
    # cash-pct check requires nav > 0; negative-cash check still fires
    alerts = _run(cash=-5.0, nav=0.0)
    assert [a.metric for a in alerts] == ["cash"]

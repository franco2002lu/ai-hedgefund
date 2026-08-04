"""Post-run invariant checks: cash floor, cash-pct ceiling, position cap."""

from app.common.enums import RiskAlertLevel
from app.modules.equities.config import PortfolioConfig
from app.modules.equities.risk_checks import (
    CASH_PCT_WARN,
    POSITION_WEIGHT_TOLERANCE,
    evaluate_post_run_invariants,
    persist_alerts,
    to_digest_dicts,
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
    assert a.source == "b-1"
    assert "growth" in a.message
    assert "(-$55,473.33)" in a.message
    assert "2026-06-22" in a.action_required
    assert a.affected_branches == ["growth"]


def test_zero_cash_is_fine():
    assert _run(cash=0.0) == []


def test_high_cash_pct_is_warning():
    alerts = _run(cash=60_000.0)  # 6% of 1M > 3% warn
    assert len(alerts) == 1
    assert alerts[0].level == RiskAlertLevel.WARNING
    assert alerts[0].metric == "cash_pct"
    assert alerts[0].current_value == 0.06  # 60_000/1_000_000 is exact in binary
    assert alerts[0].threshold == CASH_PCT_WARN


def test_cash_pct_just_below_threshold_is_fine():
    assert _run(cash=29_000.0) == []  # 2.9%: just under the 3% warn, not a breach


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


class FakeRepo:
    def __init__(self):
        self.created = []

    async def create(self, alert):
        self.created.append(alert)
        return alert


class FakeEventLog:
    def __init__(self):
        self.events = []

    async def append(self, event):
        self.events.append(event)


async def test_persist_alerts_writes_rows_and_events():
    alerts = _run(cash=-1.0, weights={"A": 0.60}, cap=0.50)
    assert len(alerts) == 2
    repo, log = FakeRepo(), FakeEventLog()

    await persist_alerts(alerts, repo=repo, event_log=log)

    assert [a.metric for a in repo.created] == ["cash", "position_weight"]
    assert [e.event_type for e in log.events] == ["risk.alert", "risk.alert"]
    # events.branch_id column is filled from event.branch_id — without it,
    # branch-filtered event-log queries never surface risk alerts
    assert [e.branch_id for e in log.events] == ["b-1", "b-1"]
    assert log.events[0].metric == "cash"
    assert log.events[0].level == RiskAlertLevel.CRITICAL
    assert log.events[0].affected_branches == ["growth"]


async def test_persist_alerts_empty_is_noop():
    repo, log = FakeRepo(), FakeEventLog()
    await persist_alerts([], repo=repo, event_log=log)
    assert repo.created == [] and log.events == []


def test_to_digest_dicts_projects_level_and_message():
    alerts = _run(cash=-1.0, weights={"A": 0.60}, cap=0.50)
    dicts = to_digest_dicts(alerts)
    assert dicts == [
        {"level": "critical", "message": alerts[0].message},
        {"level": "critical", "message": alerts[1].message},
    ]
    assert all(isinstance(d["level"], str) for d in dicts)


def test_evaluated_alert_reaches_rendered_digest_end_to_end():
    """A negative-cash breach must reach the operator-visible digest verbatim."""
    from datetime import date

    from app.modules.equities.weekly_runner import render_digest
    from tests.unit.equities.test_digest_portfolio_report import _report, _summary

    alerts = _run(cash=-55_473.33)
    digest = render_digest(
        [_summary(portfolio_report=_report(risk_alerts=to_digest_dicts(alerts)))],
        run_date=date(2026, 7, 27),
    )
    assert "- ❌ CRITICAL: growth: cash is negative (-$55,473.33)" in digest


def _flow(**kw):
    base = {
        "generated": 0,
        "persisted": 0,
        "filled": 0,
        "rejected": 0,
        "dropped": 0,
        "skipped_unpriced": 0,
        "skipped_below_entry": 0,
        "rejections": [],
        "skips": [],
    }
    base.update(kw)
    return base


class TestOrderFlowChecks:
    def _eval(self, flow):
        return evaluate_post_run_invariants(
            cash=10_000.0,
            nav=1_000_000.0,
            position_weights={},
            portfolio_config=PortfolioConfig(),
            branch_id="b-1",
            branch_name="growth",
            order_flow=flow,
        )

    def test_lost_orders_is_critical_and_names_dropped_symbols(self):
        flow = _flow(
            generated=13,
            persisted=9,
            filled=4,
            rejected=5,
            dropped=4,
            rejections=[
                {"symbol": s, "side": "sell", "kind": "dropped", "reason": "Insufficient position"}
                for s in ("AAPL", "AXP", "NEM", "RTX")
            ]
            + [
                {"symbol": s, "side": "buy", "kind": "rejected", "reason": "Insufficient cash"}
                for s in ("BKNG", "CSCO", "JPM", "MA", "MU")
            ],
        )
        alerts = self._eval(flow)
        lost = [a for a in alerts if a.metric == "orders_lost"]
        assert len(lost) == 1
        assert str(lost[0].level) == "critical"
        assert "4" in lost[0].message
        assert "AAPL, AXP, NEM, RTX" in lost[0].message
        assert "BKNG" not in lost[0].message  # rejected-kind symbols stay out of the CRITICAL

    def test_rejected_orders_is_warning_with_only_rejected_symbols(self):
        flow = _flow(
            generated=2,
            persisted=2,
            filled=1,
            rejected=1,
            rejections=[
                {"symbol": "GONE", "side": "sell", "kind": "dropped", "reason": "x"},
                {"symbol": "MSFT", "side": "buy", "kind": "rejected", "reason": "Insufficient cash"},
            ],
            dropped=1,
        )
        alerts = self._eval(flow)
        rej = [a for a in alerts if a.metric == "orders_rejected"]
        assert len(rej) == 1
        assert str(rej[0].level) == "warning"
        assert "MSFT" in rej[0].message
        assert "GONE" not in rej[0].message

    def test_unpriced_exit_skip_is_warning_naming_the_exit(self):
        flow = _flow(
            skipped_unpriced=1,
            skips=[{"symbol": "AAPL", "reason": "unpriced", "is_exit": True}],
        )
        alerts = self._eval(flow)
        skip = [a for a in alerts if a.metric == "orders_skipped_unpriced"]
        assert len(skip) == 1
        assert str(skip[0].level) == "warning"
        assert "AAPL" in skip[0].message
        assert "stuck" in skip[0].message

    def test_non_exit_unpriced_skip_warns_without_stuck_wording(self):
        flow = _flow(
            skipped_unpriced=1,
            skips=[{"symbol": "NEWP", "reason": "unpriced", "is_exit": False}],
        )
        alerts = self._eval(flow)
        skip = [a for a in alerts if a.metric == "orders_skipped_unpriced"]
        assert len(skip) == 1
        assert "EXIT" not in skip[0].message

    def test_below_entry_skips_do_not_alert(self):
        flow = _flow(
            skipped_below_entry=3,
            skips=[{"symbol": "T", "reason": "below_entry_threshold", "is_exit": False}] * 3,
        )
        assert self._eval(flow) == []

    def test_none_order_flow_keeps_legacy_behavior(self):
        alerts = evaluate_post_run_invariants(
            cash=10_000.0,
            nav=1_000_000.0,
            position_weights={},
            portfolio_config=PortfolioConfig(),
            branch_id="b-1",
            branch_name="growth",
        )
        assert alerts == []

    def test_clean_flow_is_quiet(self):
        assert self._eval(_flow(generated=5, persisted=5, filled=5)) == []


def test_cash_pct_warn_is_now_three_percent():
    alerts = evaluate_post_run_invariants(
        cash=35_000.0,
        nav=1_000_000.0,
        position_weights={},
        portfolio_config=PortfolioConfig(),
        branch_id="b-1",
        branch_name="value",
    )
    assert [a.metric for a in alerts] == ["cash_pct"]  # 3.5% now trips the 3% warn

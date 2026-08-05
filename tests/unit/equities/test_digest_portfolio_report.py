"""Digest rendering with portfolio report; ny_date helper."""

from datetime import UTC, date, datetime

from app.modules.equities.weekly_runner import (
    PortfolioReport,
    WeeklyRunSummary,
    ny_date,
    render_digest,
)


def _summary(**over):
    base = dict(
        run_id="2026-07-20-growth",
        branch_name="growth",
        status="completed",
        universe_count=50,
        screened_count=25,
        orders_placed=8,
        trades_executed=6,
        duration_seconds=95.0,
    )
    base.update(over)
    return WeeklyRunSummary(**base)


def _report(**over):
    base = dict(
        nav=1_020_000.0,
        cash=15_000.0,
        cash_pct=0.0147,
        unrealized_pnl=12_000.0,
        realized_pnl=8_000.0,
        initial_capital=1_000_000.0,
        inception_return_pct=0.02,
        wow_return_pct=0.005,
        top_holdings=[{"symbol": "AAA", "weight": 0.09}, {"symbol": "BBB", "weight": 0.07}],
        trades=[{"symbol": "CCC", "side": "buy", "quantity": 10.0, "price": 50.0, "notional": 500.0}],
        unpriced=0,
    )
    base.update(over)
    return PortfolioReport(**base)


def test_digest_includes_nav_returns_holdings_trades():
    text = render_digest([_summary(portfolio_report=_report())], run_date=date(2026, 7, 20))
    assert "$1,020,000" in text
    assert "+2.00%" in text  # inception return
    assert "+0.50%" in text  # WoW
    assert "AAA 9.0%" in text
    assert "CCC" in text and "buy" in text
    assert "  | Symbol | Side | Qty | Fill | Notional |" in text
    assert "  | CCC | buy | 10 | $50.00 | $500 |" in text
    assert "unpriced" not in text


def test_digest_warns_on_negative_cash_and_unpriced():
    # Negative-cash signaling now comes from risk_alerts (see
    # test_digest_negative_cash_line_comes_from_alerts_not_hardcode), not a
    # hardcoded hint — this test supplies the alert explicitly.
    text = render_digest(
        [
            _summary(
                portfolio_report=_report(
                    cash=-5_000.0,
                    cash_pct=-0.005,
                    unpriced=2,
                    risk_alerts=[{"level": "critical", "message": "growth: cash is negative (-$5,000.00)"}],
                )
            )
        ],
        run_date=date(2026, 7, 20),
    )
    assert "⚠️" in text
    assert "- ❌ CRITICAL: growth: cash is negative (-$5,000.00)" in text
    assert "2 position(s) unpriced" in text


def test_digest_without_report_unchanged_shape():
    text = render_digest([_summary()], run_date=date(2026, 7, 20))
    assert "NAV" not in text


def test_digest_without_baseline_shows_na_and_no_dollar_delta():
    """initial_capital=0 → inception_return_pct=None: the digest must show
    'since inception n/a' and must NOT render the whole NAV as profit
    (the raw `nav - 0` dollar delta)."""
    text = render_digest(
        [_summary(portfolio_report=_report(initial_capital=0.0, inception_return_pct=None))],
        run_date=date(2026, 7, 20),
    )
    assert "since inception n/a)" in text
    assert "n/a / $" not in text


def test_ny_date_converts_utc_evening_to_same_ny_day():
    # 2026-07-20 21:30 UTC == 17:30 ET → NY date 2026-07-20
    assert ny_date(datetime(2026, 7, 20, 21, 30, tzinfo=UTC)) == date(2026, 7, 20)
    # 2026-07-21 01:00 UTC == 2026-07-20 21:00 ET
    assert ny_date(datetime(2026, 7, 21, 1, 0, tzinfo=UTC)) == date(2026, 7, 20)


def test_digest_renders_critical_and_warning_alert_lines():
    text = render_digest(
        [
            _summary(
                portfolio_report=_report(
                    cash=-55_473.33,
                    cash_pct=-0.055,
                    risk_alerts=[
                        {"level": "critical", "message": "growth: cash is negative (-$55,473.33)"},
                        {"level": "warning", "message": "growth: cash is 6.0% of NAV"},
                    ],
                )
            )
        ],
        run_date=date(2026, 7, 20),
    )
    assert "- ❌ CRITICAL: growth: cash is negative (-$55,473.33)" in text
    assert "- ⚠️ WARNING: growth: cash is 6.0% of NAV" in text


def test_digest_negative_cash_line_comes_from_alerts_not_hardcode():
    # No alerts attached → no negative-cash line even though cash < 0
    # (the hardcoded hint is superseded by the alert-driven rendering).
    text = render_digest(
        [_summary(portfolio_report=_report(cash=-5_000.0, cash_pct=-0.005, risk_alerts=[]))],
        run_date=date(2026, 7, 20),
    )
    assert "Negative cash balance" not in text
    assert "CRITICAL" not in text


def test_digest_unknown_alert_level_escalates_to_critical():
    text = render_digest(
        [_summary(portfolio_report=_report(risk_alerts=[{"level": "CRITICAL", "message": "case-drifted level"}]))],
        run_date=date(2026, 7, 27),
    )
    assert "- ❌ CRITICAL: case-drifted level" in text


def test_digest_malformed_alert_does_not_crash_rendering():
    text = render_digest(
        [_summary(portfolio_report=_report(risk_alerts=[{}]))],
        run_date=date(2026, 7, 27),
    )
    assert "- ❌ CRITICAL: <malformed alert>" in text


def _flow(**kw):
    base = {
        "generated": 13,
        "persisted": 13,
        "filled": 8,
        "rejected": 5,
        "dropped": 0,
        "skipped_unpriced": 0,
        "skipped_below_entry": 0,
        "rejections": [
            {"symbol": s, "side": "buy", "kind": "rejected", "reason": "Insufficient cash: cost 9 > available 1"}
            for s in ("BKNG", "CSCO", "JPM", "MA", "MU")
        ],
        "skips": [],
    }
    base.update(kw)
    return base


def test_digest_renders_reconciling_orders_line():
    digest = render_digest([_summary(order_flow=_flow())], run_date=date(2026, 8, 3))
    assert "- Orders: 13 generated / 13 persisted / 8 filled / 5 rejected" in digest
    assert "rejected: BKNG, CSCO, JPM, MA, MU — Insufficient cash" in digest
    assert "Orders placed:" not in digest
    assert "DROPPED" not in digest


def test_digest_shows_dropped_count_and_splits_kinds():
    dropped_symbols = ("AAPL", "AXP", "NEM", "RTX", "GEV", "GOOGL", "BLK", "BAC")
    flow = _flow(
        generated=13,
        persisted=5,
        filled=4,
        rejected=1,
        dropped=8,
        rejections=[
            {
                "symbol": s,
                "side": "sell",
                "kind": "dropped",
                "reason": "Insufficient position: hold 74.0804, tried to sell 74.0814",
            }
            for s in dropped_symbols
        ]
        + [
            {
                "symbol": "MU",
                "side": "buy",
                "kind": "rejected",
                "reason": "Insufficient cash: cost 9 > available 1",
            }
        ],
    )
    digest = render_digest([_summary(order_flow=flow)], run_date=date(2026, 8, 3))
    assert "/ 8 DROPPED" in digest
    assert "dropped (no DB row): AAPL, AXP, NEM, RTX, GEV, GOOGL, BLK, BAC — Insufficient position" in digest
    assert "  - rejected: MU — Insufficient cash" in digest


def test_digest_all_zero_flow_still_renders_flow_line():
    flow = _flow(
        generated=0,
        persisted=0,
        filled=0,
        rejected=0,
        dropped=0,
        rejections=[],
        skips=[],
    )
    digest = render_digest([_summary(order_flow=flow)], run_date=date(2026, 8, 3))
    assert "- Orders: 0 generated / 0 persisted / 0 filled / 0 rejected" in digest
    assert "Orders placed:" not in digest


def test_digest_renders_skips_line_with_exit_flag():
    flow = _flow(
        rejected=0,
        filled=13,
        rejections=[],
        skipped_unpriced=1,
        skips=[{"symbol": "AAPL", "reason": "unpriced", "is_exit": True}],
    )
    digest = render_digest([_summary(order_flow=flow)], run_date=date(2026, 8, 3))
    assert "skipped: AAPL (unpriced, exit)" in digest


def test_digest_legacy_fallback_without_order_flow():
    digest = render_digest([_summary()], run_date=date(2026, 8, 3))
    assert "- Orders placed: 8" in digest


def test_digest_notes_additional_distinct_reasons():
    flow = _flow(
        rejected=2,
        rejections=[
            {
                "symbol": "VZ",
                "side": "sell",
                "kind": "rejected",
                "reason": "Insufficient position: hold 5, tried to sell 10",
            },
            {
                "symbol": "MU",
                "side": "buy",
                "kind": "rejected",
                "reason": "Insufficient cash: cost 9 > available 1",
            },
        ],
    )
    digest = render_digest([_summary(order_flow=flow)], run_date=date(2026, 8, 3))
    assert "rejected: VZ, MU — Insufficient position" in digest
    assert "(+1 more reason(s))" in digest

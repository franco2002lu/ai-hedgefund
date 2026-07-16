"""Known-answer tests for the backfill reconstruction math."""

from datetime import UTC, date, datetime

import pytest

from app.common.enums import ExecutionMode, OrderSide
from app.common.models.trade import Trade
from scripts.backfill_snapshots import reconstruct_daily_states, validate_final_state


def _trade(symbol, side, qty, price, day, commission=0.0):
    return Trade(
        id=f"t-{symbol}-{day}",
        order_id="o",
        branch_id="b-1",
        instrument_id="i",
        symbol=symbol,
        side=side,
        quantity=qty,
        price=price,
        commission=commission,
        execution_mode=ExecutionMode.PAPER,
        executed_at=datetime(day.year, day.month, day.day, 15, 40, tzinfo=UTC),
    )


def test_reconstruction_known_answer():
    d1, d2, d3 = date(2026, 6, 15), date(2026, 6, 16), date(2026, 6, 17)
    trades = [
        _trade("AAA", OrderSide.BUY, 100, 10.0, d1),  # cash 100000-1000=99000
        _trade("AAA", OrderSide.SELL, 50, 12.0, d3),  # proceeds 600
    ]
    closes = {"AAA": {d1: 11.0, d2: 12.0, d3: 12.5}}
    states = reconstruct_daily_states(trades, initial_cash=100_000.0, closes=closes, trading_days=[d1, d2, d3])
    assert [s.day for s in states] == [d1, d2, d3]
    # Day1: cash 99000, AAA 100 @ close 11 → nav 100100, unrealized +100
    assert states[0].cash == pytest.approx(99_000.0)
    assert states[0].nav == pytest.approx(100_100.0)
    assert states[0].unrealized_pnl == pytest.approx(100.0)
    # Day2: no trades; close 12 → nav 99000 + 1200
    assert states[1].nav == pytest.approx(100_200.0)
    # Day3: sell 50 @ 12 → cash 99600; realized (12-10)*50 = 100
    assert states[2].cash == pytest.approx(99_600.0)
    assert states[2].realized_pnl == pytest.approx(100.0)
    # remaining 50 @ close 12.5 → nav 99600 + 625
    assert states[2].nav == pytest.approx(100_225.0)
    assert states[2].positions["AAA"]["quantity"] == pytest.approx(50.0)


def test_reconstruction_carries_forward_missing_close():
    d1, d2 = date(2026, 6, 15), date(2026, 6, 16)
    trades = [_trade("AAA", OrderSide.BUY, 10, 100.0, d1)]
    closes = {"AAA": {d1: 100.0}}  # no close on d2
    states = reconstruct_daily_states(trades, initial_cash=10_000.0, closes=closes, trading_days=[d1, d2])
    assert states[1].nav == pytest.approx(states[0].nav)


def test_reconstruction_commission_reduces_cash_and_realized():
    d1 = date(2026, 6, 15)
    trades = [
        _trade("AAA", OrderSide.BUY, 10, 100.0, d1, commission=1.0),
        _trade("AAA", OrderSide.SELL, 10, 110.0, d1, commission=1.0),
    ]
    closes = {"AAA": {d1: 110.0}}
    states = reconstruct_daily_states(trades, initial_cash=10_000.0, closes=closes, trading_days=[d1])
    # buy cost 1001, sell proceeds 1099; realized = (110 - 100.1)*10 - 1 = 98
    assert states[0].cash == pytest.approx(10_000.0 - 1001.0 + 1099.0)
    assert states[0].realized_pnl == pytest.approx(98.0)


def test_reconstruction_reentry_after_full_exit_uses_fresh_cost_basis():
    d1, d2, d3 = date(2026, 6, 15), date(2026, 6, 16), date(2026, 6, 17)
    trades = [
        _trade("AAA", OrderSide.BUY, 10, 100.0, d1),
        _trade("AAA", OrderSide.SELL, 10, 120.0, d2),  # realized (120-100)*10 = 200, closes the lot
        _trade("AAA", OrderSide.BUY, 5, 130.0, d3),  # re-entry: fresh lot, must not blend with the old one
    ]
    closes = {"AAA": {d1: 100.0, d2: 120.0, d3: 130.0}}
    states = reconstruct_daily_states(trades, initial_cash=10_000.0, closes=closes, trading_days=[d1, d2, d3])
    assert "AAA" not in states[1].positions  # position fully closed on d2
    assert states[1].realized_pnl == pytest.approx(200.0)
    assert states[2].positions["AAA"]["quantity"] == pytest.approx(5.0)
    assert states[2].positions["AAA"]["cost_basis"] == 650.0  # exactly 5 * 130 — no blend with the old lot
    assert states[2].realized_pnl == pytest.approx(200.0)  # d2 sale stays the only realization


def test_reconstruction_rejects_non_long_sides():
    d1 = date(2026, 6, 15)
    with pytest.raises(ValueError):
        reconstruct_daily_states(
            [_trade("AAA", OrderSide.SHORT, 10, 100.0, d1)],
            initial_cash=1_000.0,
            closes={"AAA": {d1: 100.0}},
            trading_days=[d1],
        )


def test_reconstruction_empty_trading_days_returns_empty():
    # Empty-in → empty-out: callers (backfill_branch) rely on this and must guard
    # before touching states[-1].
    d1 = date(2026, 6, 15)
    trades = [_trade("AAA", OrderSide.BUY, 10, 100.0, d1)]
    assert reconstruct_daily_states(trades, initial_cash=10_000.0, closes={}, trading_days=[]) == []


def test_validate_final_state_reports_mismatches():
    d1 = date(2026, 6, 15)
    states = reconstruct_daily_states(
        [_trade("AAA", OrderSide.BUY, 10, 100.0, d1)],
        initial_cash=10_000.0,
        closes={"AAA": {d1: 100.0}},
        trading_days=[d1],
    )
    ok = validate_final_state(states[-1], live_cash=9_000.0, live_positions={"AAA": 10.0})
    assert ok == []
    bad = validate_final_state(states[-1], live_cash=1_234.0, live_positions={"AAA": 99.0})
    assert len(bad) == 2


def test_validate_final_state_cash_tolerance_is_half_dollar():
    d1 = date(2026, 6, 15)
    states = reconstruct_daily_states(
        [_trade("AAA", OrderSide.BUY, 10, 100.0, d1)],
        initial_cash=10_000.0,
        closes={"AAA": {d1: 100.0}},
        trading_days=[d1],
    )
    # Reconstructed cash is 9000.00. Numeric(18,2) rounding residue (sub-$0.50)
    # must pass; real drift (dollars) must fail — reported at 4-decimal precision.
    assert validate_final_state(states[-1], live_cash=9_000.30, live_positions={"AAA": 10.0}) == []
    bad = validate_final_state(states[-1], live_cash=9_000.75, live_positions={"AAA": 10.0})
    assert bad == ["cash mismatch: reconstructed 9000.0000 vs live 9000.7500"]

"""Execution-sequence regressions: full-exit clamp + the 2026-06-22 incident.

Fakes mirror tests/unit/test_trade_execution_cash_check.py but track per-symbol
positions and a price map so multi-symbol sequences can be replayed.
"""

from datetime import UTC, datetime

import pytest

from app.common.enums import OrderSide, OrderType
from app.common.interfaces.broker import OrderResult
from app.common.models.order import OrderRequest
from app.common.models.trade import Trade
from app.modules.trade_execution.service import TradeExecutionService


class FakeOrderRepo:
    def __init__(self):
        self.status_updates = []

    async def create(self, order):
        return order

    async def update_status(self, order_id, status, **kw):
        self.status_updates.append((order_id, status, kw))


class FakeTradeRepo:
    def __init__(self):
        self.created = []

    async def create(self, trade):
        self.created.append(trade)
        return trade


class FakePriceMapBroker:
    """Fills every order at the symbol's mapped price (no slippage/commission)."""

    def __init__(self, prices: dict[str, float]):
        self.prices = prices

    async def submit_order(self, req):
        return OrderResult(
            success=True,
            trade=Trade(
                id=f"t-{req.symbol}",
                order_id="",
                branch_id=req.branch_id,
                instrument_id=req.instrument_id,
                symbol=req.symbol,
                side=req.side,
                quantity=req.quantity,
                price=self.prices[req.symbol],
                commission=0.0,
                slippage=0.0,
                execution_mode="paper",
                executed_at=datetime.now(UTC),
            ),
        )


class _Pos:
    def __init__(self, qty):
        self.long_quantity = qty


class FakeBookPortfolioService:
    """Tracks cash and per-symbol long quantities; records the cash low-water mark."""

    def __init__(self, cash: float, positions: dict[str, float]):
        self._cash = cash
        self.positions = dict(positions)
        self.min_cash_seen = cash

    async def get_portfolio(self, branch_id):
        class S:
            pass

        s = S()
        s.cash = self._cash
        return s

    async def get_position_by_symbol(self, branch_id, symbol):
        qty = self.positions.get(symbol)
        return _Pos(qty) if qty is not None else None

    async def handle_trade_executed(self, trade):
        if trade.side == OrderSide.BUY:
            self._cash -= trade.price * trade.quantity + trade.commission
            self.positions[trade.symbol] = self.positions.get(trade.symbol, 0.0) + trade.quantity
        elif trade.side == OrderSide.SELL:
            self._cash += trade.price * trade.quantity - trade.commission
            remaining = self.positions.get(trade.symbol, 0.0) - trade.quantity
            if remaining == 0.0:
                del self.positions[trade.symbol]  # delete_if_flat
            else:
                self.positions[trade.symbol] = remaining
        self.min_cash_seen = min(self.min_cash_seen, self._cash)


class FakeEventLog:
    def __init__(self):
        self.events = []

    async def append(self, event):
        self.events.append(event)


def _svc(cash, prices, positions=None):
    return TradeExecutionService(
        order_repo=FakeOrderRepo(),
        trade_repo=FakeTradeRepo(),
        broker=FakePriceMapBroker(prices),
        event_log=FakeEventLog(),
        portfolio_service=FakeBookPortfolioService(cash, positions or {}),
    )


def _req(symbol, side, qty):
    return OrderRequest(
        branch_id="b-1",
        instrument_id=f"in-{symbol}",
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        quantity=qty,
    )


async def test_sell_a_hair_above_held_clamps_to_full_exit():
    held = 435.0051
    svc = _svc(cash=0.0, prices={"ACN": 120.0}, positions={"ACN": held})
    result = await svc.submit_order(_req("ACN", OrderSide.SELL, held + 5e-7))
    assert result["success"] is True
    assert svc.trade_repo.created[0].quantity == held  # clamped to exactly held
    assert "ACN" not in svc.portfolio_service.positions  # position closed flat


async def test_sell_exactly_held_closes_flat_without_clamp():
    held = 435.0051
    svc = _svc(cash=0.0, prices={"ACN": 120.0}, positions={"ACN": held})
    result = await svc.submit_order(_req("ACN", OrderSide.SELL, held))
    assert result["success"] is True
    assert svc.trade_repo.created[0].quantity == held
    assert "ACN" not in svc.portfolio_service.positions


async def test_sell_a_hair_below_held_clamps_up_to_full_exit():
    held = 435.0051
    svc = _svc(cash=0.0, prices={"ACN": 120.0}, positions={"ACN": held})
    result = await svc.submit_order(_req("ACN", OrderSide.SELL, held - 5e-7))
    assert result["success"] is True
    assert svc.trade_repo.created[0].quantity == held
    assert "ACN" not in svc.portfolio_service.positions  # no 5e-7 dust row


async def test_sell_materially_above_held_is_rejected():
    svc = _svc(cash=0.0, prices={"ACN": 120.0}, positions={"ACN": 10.0})
    result = await svc.submit_order(_req("ACN", OrderSide.SELL, 10.001))
    assert result["success"] is False
    assert "Insufficient position" in result["message"]


async def test_partial_sell_is_not_clamped():
    svc = _svc(cash=0.0, prices={"ACN": 120.0}, positions={"ACN": 10.0})
    result = await svc.submit_order(_req("ACN", OrderSide.SELL, 4.0))
    assert result["success"] is True
    assert svc.trade_repo.created[0].quantity == 4.0
    assert svc.portfolio_service.positions["ACN"] == pytest.approx(6.0)


# ---------------------------------------------------------------------------
# 2026-06-22 incident regression: the value branch filled $368k of buys BEFORE
# its $132k sell (alphabetical execution, no cash gate) and ran cash to
# -$236,553 (~24% unintended leverage). These scenarios replay the real order
# set and assert the fill-time cash gate + sells-first ordering prevent it.
# Real fills from prod: ACN 435.0051@120.8804, BLK 19.8133@1055.6726,
# CRM 829.952@147.9439, DIS 821.6356@101.6608, T 3984.7789@22.1776,
# SELL SCHW 1434.8511@92.0889; starting cash -499.75.
# ---------------------------------------------------------------------------

_JUN22_PRICES = {
    "ACN": 120.8804,
    "BLK": 1055.6726,
    "CRM": 147.9439,
    "DIS": 101.6608,
    "SCHW": 92.0889,
    "T": 22.1776,
}
_JUN22_ORDERS = {
    "ACN": ("buy", 435.0051),
    "BLK": ("buy", 19.8133),
    "CRM": ("buy", 829.952),
    "DIS": ("buy", 821.6356),
    "SCHW": ("sell", 1434.8511),
    "T": ("buy", 3984.7789),
}
_JUN22_START_CASH = -499.75
_SCHW_PROCEEDS = 1434.8511 * 92.0889  # ≈ 132,133.86


def _jun22_svc():
    return _svc(
        cash=_JUN22_START_CASH,
        prices=_JUN22_PRICES,
        positions={"SCHW": 1434.8511},
    )


async def _submit_all(svc, symbols):
    results = {}
    for sym in symbols:
        side, qty = _JUN22_ORDERS[sym]
        results[sym] = await svc.submit_order(
            _req(sym, OrderSide.BUY if side == "buy" else OrderSide.SELL, qty)
        )
    return results


async def test_jun22_alphabetical_replay_gate_blocks_the_overdraft():
    """Historical submission order (alphabetical). Without the gate this ran
    cash to -$236,553; with it, every buy ahead of the sell is rejected and
    cash never drops below its starting value."""
    svc = _jun22_svc()
    results = await _submit_all(svc, ["ACN", "BLK", "CRM", "DIS", "SCHW", "T"])

    assert results["ACN"]["success"] is False  # cash was -499.75
    assert results["BLK"]["success"] is False
    assert results["CRM"]["success"] is False
    assert results["DIS"]["success"] is False
    assert results["SCHW"]["success"] is True  # sells ignore the cash gate
    assert results["T"]["success"] is True  # funded by the sell proceeds

    book = svc.portfolio_service
    assert book.min_cash_seen == _JUN22_START_CASH  # never went lower
    expected_final = _JUN22_START_CASH + _SCHW_PROCEEDS - 3984.7789 * 22.1776
    assert book._cash == pytest.approx(expected_final, abs=0.01)  # ≈ +43,260
    assert sorted(t.symbol for t in svc.trade_repo.created) == ["SCHW", "T"]


async def test_jun22_sells_first_replay_funds_buys_until_cash_runs_out():
    """Current generate_orders ordering (sells first, alphabetical within
    side). The order SET is unfundable — the gate converts what used to be
    -$236k of leverage into rejections of the unaffordable tail.

    The sells-first ordering itself is pinned in
    tests/unit/equities/test_order_generation_cash.py; these tests hardcode
    the submission order and guard the gate's behavior under it."""
    svc = _jun22_svc()
    results = await _submit_all(svc, ["SCHW", "ACN", "BLK", "CRM", "DIS", "T"])

    assert results["SCHW"]["success"] is True
    assert results["ACN"]["success"] is True  # 52,584 ≤ 131,634
    assert results["BLK"]["success"] is True  # 20,916 ≤ 79,050
    assert results["CRM"]["success"] is False  # 122,787 > 58,134
    assert results["DIS"]["success"] is False
    assert results["T"]["success"] is False

    book = svc.portfolio_service
    assert book.min_cash_seen == _JUN22_START_CASH
    expected_final = (
        _JUN22_START_CASH + _SCHW_PROCEEDS - 435.0051 * 120.8804 - 19.8133 * 1055.6726
    )
    assert book._cash == pytest.approx(expected_final, abs=0.01)  # ≈ +58,134
    assert sorted(t.symbol for t in svc.trade_repo.created) == ["ACN", "BLK", "SCHW"]


async def test_deleverage_from_negative_cash_ends_non_negative():
    """The 2026-07-20 situation: an overdrawn book (growth cash -$55,473) with
    a net-selling rebalance delevers cleanly — sells first, then buys fit.

    The sells-first ordering itself is pinned in
    tests/unit/equities/test_order_generation_cash.py; these tests hardcode
    the submission order and guard the gate's behavior under it."""
    svc = _svc(
        cash=-55_473.33,
        prices={"AAA": 100.0, "BBB": 100.0},
        positions={"AAA": 1_000.0, "BBB": 500.0},
    )
    sell = await svc.submit_order(_req("AAA", OrderSide.SELL, 700.0))  # +70,000
    buy = await svc.submit_order(_req("BBB", OrderSide.BUY, 100.0))  # -10,000

    assert sell["success"] is True and buy["success"] is True
    book = svc.portfolio_service
    assert book._cash == pytest.approx(4_526.67, abs=0.01)
    assert book._cash >= 0


async def test_buy_cents_above_available_cash_is_rejected():
    """Kills tolerance-magnitude regressions: a buy costing a few cents more
    than available cash must reject (the gate's tolerance is 1e-6, not dollars)."""
    svc = _svc(cash=1_000.0, prices={"AAA": 100.005}, positions={})
    result = await svc.submit_order(_req("AAA", OrderSide.BUY, 10.0))  # cost 1_000.05
    assert result["success"] is False
    assert svc.trade_repo.created == []

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

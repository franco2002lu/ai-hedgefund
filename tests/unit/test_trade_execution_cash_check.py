"""BUY orders that would overdraw cash are rejected at fill time."""

from datetime import UTC, datetime

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


class FakeBroker:
    def __init__(self, fill_price):
        self.fill_price = fill_price

    async def submit_order(self, req):
        return OrderResult(
            success=True,
            trade=Trade(
                id="t-1",
                order_id="",
                branch_id=req.branch_id,
                instrument_id=req.instrument_id,
                symbol=req.symbol,
                side=req.side,
                quantity=req.quantity,
                price=self.fill_price,
                commission=0.0,
                slippage=0.0,
                execution_mode="paper",
                executed_at=datetime.now(UTC),
            ),
        )


class FakePortfolioService:
    def __init__(self, cash):
        self._cash = cash
        self.handled = []

    async def get_portfolio(self, branch_id):
        class S:  # minimal duck-typed summary
            pass

        s = S()
        s.cash = self._cash
        return s

    async def get_position_by_symbol(self, branch_id, symbol):
        return None

    async def handle_trade_executed(self, trade):
        self.handled.append(trade)
        # Mirror PortfolioService's cash delta so sequential orders in the same
        # test see a fresh balance, the way the real get_portfolio() re-read
        # in submit_order does against Postgres.
        if trade.side == OrderSide.BUY:
            self._cash -= trade.price * trade.quantity + trade.commission
        elif trade.side == OrderSide.SELL:
            self._cash += trade.price * trade.quantity - trade.commission


class FakeEventLog:
    def __init__(self):
        self.events = []

    async def append(self, event):
        self.events.append(event)


def _svc(cash, fill_price):
    return TradeExecutionService(
        order_repo=FakeOrderRepo(),
        trade_repo=FakeTradeRepo(),
        broker=FakeBroker(fill_price),
        event_log=FakeEventLog(),
        portfolio_service=FakePortfolioService(cash),
    )


def _req(side, qty):
    return OrderRequest(
        branch_id="b-1",
        instrument_id="in-1",
        symbol="AAA",
        side=side,
        order_type=OrderType.MARKET,
        quantity=qty,
    )


async def test_buy_exceeding_cash_is_rejected():
    svc = _svc(cash=1_000.0, fill_price=100.0)
    result = await svc.submit_order(_req(OrderSide.BUY, qty=20))  # cost 2000 > 1000
    assert result["success"] is False
    assert "Insufficient cash" in result["message"]
    assert svc.trade_repo.created == []
    assert svc.portfolio_service.handled == []
    assert any(str(u[1]) == "rejected" for u in svc.order_repo.status_updates)
    # A rejection event was logged
    assert any(getattr(e, "rejection_reason", "").startswith("Insufficient cash") for e in svc.event_log.events)


async def test_buy_exactly_at_cash_fills():
    svc = _svc(cash=2_000.0, fill_price=100.0)
    result = await svc.submit_order(_req(OrderSide.BUY, qty=20))  # cost 2000 == 2000
    assert result["success"] is True
    assert len(svc.trade_repo.created) == 1


async def test_buy_within_cash_fills_and_updates_portfolio():
    svc = _svc(cash=5_000.0, fill_price=100.0)
    result = await svc.submit_order(_req(OrderSide.BUY, qty=20))
    assert result["success"] is True
    assert len(svc.portfolio_service.handled) == 1


async def test_sell_ignores_cash_check():
    svc = _svc(cash=-500.0, fill_price=100.0)

    async def _pos(branch_id, symbol):
        class P:
            long_quantity = 50.0

        return P()

    svc.portfolio_service.get_position_by_symbol = _pos
    result = await svc.submit_order(_req(OrderSide.SELL, qty=10))
    assert result["success"] is True


async def test_sequential_orders_reject_and_fill_on_fresh_cash():
    """Cash is re-read from the portfolio before every order (not cached at
    service construction or across calls), so a fill/reject/fill/reject cycle
    tracks the running balance rather than a stale snapshot:

    cash 1_000 @ fill_price 100 —
      BUY qty 8  (cost 800):  fills,    cash 1_000 -> 200
      BUY qty 8  (cost 800):  rejected, cash stays at 200 (only 200 available)
      SELL qty 8 (proceeds 800): fills, cash   200 -> 1_000
      BUY qty 8  (cost 800):  fills,    cash 1_000 -> 200
    """
    svc = _svc(cash=1_000.0, fill_price=100.0)

    async def _pos(branch_id, symbol):
        class P:
            long_quantity = 8.0

        return P()

    svc.portfolio_service.get_position_by_symbol = _pos

    first_buy = await svc.submit_order(_req(OrderSide.BUY, qty=8))
    assert first_buy["success"] is True
    assert svc.portfolio_service._cash == 200.0

    second_buy = await svc.submit_order(_req(OrderSide.BUY, qty=8))
    assert second_buy["success"] is False
    assert "Insufficient cash" in second_buy["message"]
    assert svc.portfolio_service._cash == 200.0

    sell = await svc.submit_order(_req(OrderSide.SELL, qty=8))
    assert sell["success"] is True
    assert svc.portfolio_service._cash == 1_000.0

    third_buy = await svc.submit_order(_req(OrderSide.BUY, qty=8))
    assert third_buy["success"] is True
    assert svc.portfolio_service._cash == 200.0

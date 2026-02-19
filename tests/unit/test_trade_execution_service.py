"""Unit tests for TradeExecutionService."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from app.common.enums import ExecutionMode, OrderSide, OrderStatus, OrderType, TimeInForce
from app.common.interfaces.broker import OrderResult
from app.common.models.order import Order, OrderRequest
from app.common.models.portfolio import PortfolioSummary
from app.common.models.position import Position
from app.common.models.trade import Trade
from app.modules.trade_execution.service import TradeExecutionService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_order_request(**overrides) -> OrderRequest:
    defaults = dict(
        branch_id="branch-1",
        instrument_id="inst-1",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10.0,
    )
    defaults.update(overrides)
    return OrderRequest(**defaults)


def _make_order(**overrides) -> Order:
    defaults = dict(
        id="order-1",
        branch_id="branch-1",
        instrument_id="inst-1",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10.0,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return Order(**defaults)


def _make_trade(**overrides) -> Trade:
    defaults = dict(
        id="trade-1",
        order_id="order-1",
        branch_id="branch-1",
        instrument_id="inst-1",
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=10.0,
        price=150.0,
        commission=0.0,
        slippage=0.075,
        execution_mode=ExecutionMode.PAPER,
        executed_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return Trade(**defaults)


def _make_portfolio(**overrides) -> PortfolioSummary:
    defaults = dict(
        id="port-1",
        branch_id="branch-1",
        branch_type="equities",
        cash=100_000.0,
        allocated_capital=0.0,
        margin_requirement=0.5,
        margin_used=0.0,
        nav=100_000.0,
        total_long_exposure=0.0,
        total_short_exposure=0.0,
        gross_exposure=0.0,
        net_exposure=0.0,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        positions=[],
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return PortfolioSummary(**defaults)


@pytest.fixture
def deps():
    order_repo = AsyncMock()
    trade_repo = AsyncMock()
    broker = AsyncMock()
    event_log = AsyncMock()
    portfolio_service = AsyncMock()
    return order_repo, trade_repo, broker, event_log, portfolio_service


@pytest.fixture
def service(deps):
    order_repo, trade_repo, broker, event_log, portfolio_service = deps
    return TradeExecutionService(
        order_repo=order_repo,
        trade_repo=trade_repo,
        broker=broker,
        event_log=event_log,
        portfolio_service=portfolio_service,
    )


# ---------------------------------------------------------------------------
# submit_order — success path
# ---------------------------------------------------------------------------

class TestSubmitOrderSuccess:
    @pytest.mark.asyncio
    async def test_successful_buy_returns_fill_info(self, service, deps):
        order_repo, trade_repo, broker, event_log, portfolio_service = deps

        # Validation passes
        portfolio_service.get_portfolio.return_value = _make_portfolio()

        # Order repo returns the persisted order with an ID
        created_order = _make_order()
        order_repo.create.return_value = created_order

        # Broker fills successfully
        fill_trade = _make_trade(price=150.075)
        broker.submit_order.return_value = OrderResult(success=True, trade=fill_trade)

        # Trade repo returns the persisted trade
        trade_repo.create.return_value = fill_trade

        req = _make_order_request()
        result = await service.submit_order(req)

        assert result["success"] is True
        assert result["status"] == "filled"
        assert result["fill_price"] == 150.075
        assert result["order_id"] == "order-1"
        assert result["trade_id"] == "trade-1"

    @pytest.mark.asyncio
    async def test_success_updates_order_to_filled(self, service, deps):
        order_repo, trade_repo, broker, event_log, portfolio_service = deps
        portfolio_service.get_portfolio.return_value = _make_portfolio()
        order_repo.create.return_value = _make_order()
        fill_trade = _make_trade()
        broker.submit_order.return_value = OrderResult(success=True, trade=fill_trade)
        trade_repo.create.return_value = fill_trade

        await service.submit_order(_make_order_request())

        order_repo.update_status.assert_called_once()
        call_args = order_repo.update_status.call_args
        assert call_args[0][1] == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_success_persists_trade(self, service, deps):
        order_repo, trade_repo, broker, event_log, portfolio_service = deps
        portfolio_service.get_portfolio.return_value = _make_portfolio()
        order_repo.create.return_value = _make_order()
        fill_trade = _make_trade()
        broker.submit_order.return_value = OrderResult(success=True, trade=fill_trade)
        trade_repo.create.return_value = fill_trade

        await service.submit_order(_make_order_request())

        trade_repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_calls_portfolio_handle(self, service, deps):
        order_repo, trade_repo, broker, event_log, portfolio_service = deps
        portfolio_service.get_portfolio.return_value = _make_portfolio()
        order_repo.create.return_value = _make_order()
        fill_trade = _make_trade()
        broker.submit_order.return_value = OrderResult(success=True, trade=fill_trade)
        trade_repo.create.return_value = fill_trade

        await service.submit_order(_make_order_request())

        portfolio_service.handle_trade_executed.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_logs_two_events(self, service, deps):
        """TradeRequestedEvent + TradeExecutedEvent."""
        order_repo, trade_repo, broker, event_log, portfolio_service = deps
        portfolio_service.get_portfolio.return_value = _make_portfolio()
        order_repo.create.return_value = _make_order()
        fill_trade = _make_trade()
        broker.submit_order.return_value = OrderResult(success=True, trade=fill_trade)
        trade_repo.create.return_value = fill_trade

        await service.submit_order(_make_order_request())

        assert event_log.append.call_count == 2
        event_types = [call[0][0].event_type for call in event_log.append.call_args_list]
        assert "trade.requested" in event_types
        assert "trade.executed" in event_types


# ---------------------------------------------------------------------------
# submit_order — rejection paths
# ---------------------------------------------------------------------------

class TestSubmitOrderRejection:
    @pytest.mark.asyncio
    async def test_broker_rejection(self, service, deps):
        order_repo, trade_repo, broker, event_log, portfolio_service = deps
        portfolio_service.get_portfolio.return_value = _make_portfolio()
        order_repo.create.return_value = _make_order()
        broker.submit_order.return_value = OrderResult(
            success=False, rejection_reason="Insufficient liquidity"
        )

        result = await service.submit_order(_make_order_request())

        assert result["success"] is False
        assert result["status"] == "rejected"
        assert "Insufficient liquidity" in result["message"]

        # Order updated to REJECTED
        order_repo.update_status.assert_called_once()
        assert order_repo.update_status.call_args[0][1] == OrderStatus.REJECTED

        # TradeRejectedEvent logged (after TradeRequestedEvent)
        event_types = [call[0][0].event_type for call in event_log.append.call_args_list]
        assert "trade.rejected" in event_types

    @pytest.mark.asyncio
    async def test_validation_failure_no_portfolio(self, service, deps):
        _, _, _, _, portfolio_service = deps
        portfolio_service.get_portfolio.return_value = None

        result = await service.submit_order(_make_order_request())

        assert result["success"] is False
        assert result["status"] == "rejected"
        assert "No portfolio" in result["message"]


# ---------------------------------------------------------------------------
# _validate_order
# ---------------------------------------------------------------------------

class TestValidateOrder:
    @pytest.mark.asyncio
    async def test_buy_with_portfolio_passes(self, service, deps):
        _, _, _, _, portfolio_service = deps
        portfolio_service.get_portfolio.return_value = _make_portfolio()

        req = _make_order_request(side=OrderSide.BUY)
        result = await service._validate_order(req)
        assert result is None

    @pytest.mark.asyncio
    async def test_sell_sufficient_position_passes(self, service, deps):
        _, _, _, _, portfolio_service = deps
        portfolio_service.get_portfolio.return_value = _make_portfolio()
        pos = Position(
            id="p1", portfolio_id="port-1", instrument_id="inst-1", symbol="AAPL",
            long_quantity=20.0, updated_at=datetime.now(timezone.utc),
        )
        portfolio_service.get_position_by_symbol.return_value = pos

        req = _make_order_request(side=OrderSide.SELL, quantity=10.0)
        result = await service._validate_order(req)
        assert result is None

    @pytest.mark.asyncio
    async def test_sell_insufficient_position_fails(self, service, deps):
        _, _, _, _, portfolio_service = deps
        portfolio_service.get_portfolio.return_value = _make_portfolio()
        pos = Position(
            id="p1", portfolio_id="port-1", instrument_id="inst-1", symbol="AAPL",
            long_quantity=5.0, updated_at=datetime.now(timezone.utc),
        )
        portfolio_service.get_position_by_symbol.return_value = pos

        req = _make_order_request(side=OrderSide.SELL, quantity=10.0)
        result = await service._validate_order(req)
        assert result is not None
        assert "Insufficient position" in result

    @pytest.mark.asyncio
    async def test_sell_no_position_fails(self, service, deps):
        _, _, _, _, portfolio_service = deps
        portfolio_service.get_portfolio.return_value = _make_portfolio()
        portfolio_service.get_position_by_symbol.return_value = None

        req = _make_order_request(side=OrderSide.SELL, quantity=10.0)
        result = await service._validate_order(req)
        assert result is not None
        assert "Insufficient position" in result

    @pytest.mark.asyncio
    async def test_cover_insufficient_short_fails(self, service, deps):
        _, _, _, _, portfolio_service = deps
        portfolio_service.get_portfolio.return_value = _make_portfolio()
        pos = Position(
            id="p1", portfolio_id="port-1", instrument_id="inst-1", symbol="AAPL",
            short_quantity=3.0, updated_at=datetime.now(timezone.utc),
        )
        portfolio_service.get_position_by_symbol.return_value = pos

        req = _make_order_request(side=OrderSide.COVER, quantity=10.0)
        result = await service._validate_order(req)
        assert result is not None
        assert "Insufficient short position" in result

    @pytest.mark.asyncio
    async def test_no_portfolio_fails(self, service, deps):
        _, _, _, _, portfolio_service = deps
        portfolio_service.get_portfolio.return_value = None

        req = _make_order_request()
        result = await service._validate_order(req)
        assert result is not None
        assert "No portfolio" in result

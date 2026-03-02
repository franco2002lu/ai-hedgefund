"""Unit tests for PaperTradingAdapter."""

from unittest.mock import AsyncMock

import pytest

from app.common.enums import OrderSide, OrderType
from app.common.models.order import OrderRequest
from app.modules.trade_execution.adapters.paper import PaperTradingAdapter


def _make_request(**overrides) -> OrderRequest:
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


@pytest.fixture
def data_service():
    return AsyncMock()


@pytest.fixture
def adapter(data_service):
    return PaperTradingAdapter(
        data_platform_service=data_service,
        slippage_bps=10.0,  # 10 bps = 0.1%
        commission_per_trade=1.50,
    )


# ---------------------------------------------------------------------------
# Market orders — slippage
# ---------------------------------------------------------------------------


class TestMarketOrders:
    async def test_buy_slippage_increases_price(self, adapter, data_service):
        data_service.get_current_price.return_value = 100.0

        result = await adapter.submit_order(_make_request(side=OrderSide.BUY))

        assert result.success is True
        # 100 * (1 + 10/10000) = 100.10
        assert result.trade.price == pytest.approx(100.10, abs=0.01)
        assert result.trade.commission == 1.50

    async def test_sell_slippage_decreases_price(self, adapter, data_service):
        data_service.get_current_price.return_value = 100.0

        result = await adapter.submit_order(_make_request(side=OrderSide.SELL))

        assert result.success is True
        # 100 * (1 - 10/10000) = 99.90
        assert result.trade.price == pytest.approx(99.90, abs=0.01)

    async def test_short_slippage_decreases_price(self, adapter, data_service):
        data_service.get_current_price.return_value = 100.0

        result = await adapter.submit_order(_make_request(side=OrderSide.SHORT))

        assert result.success is True
        assert result.trade.price == pytest.approx(99.90, abs=0.01)

    async def test_cover_slippage_increases_price(self, adapter, data_service):
        data_service.get_current_price.return_value = 100.0

        result = await adapter.submit_order(_make_request(side=OrderSide.COVER))

        assert result.success is True
        assert result.trade.price == pytest.approx(100.10, abs=0.01)

    async def test_slippage_value_recorded(self, adapter, data_service):
        data_service.get_current_price.return_value = 100.0

        result = await adapter.submit_order(_make_request(side=OrderSide.BUY))

        assert result.trade.slippage == pytest.approx(0.10, abs=0.01)


# ---------------------------------------------------------------------------
# Limit orders
# ---------------------------------------------------------------------------


class TestLimitOrders:
    async def test_limit_buy_rejected_when_price_above_limit(self, adapter, data_service):
        data_service.get_current_price.return_value = 105.0

        req = _make_request(
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=100.0,
        )
        result = await adapter.submit_order(req)

        assert result.success is False
        assert "Limit price" in result.rejection_reason

    async def test_limit_buy_accepted_fills_at_limit(self, adapter, data_service):
        data_service.get_current_price.return_value = 99.0

        req = _make_request(
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=100.0,
        )
        result = await adapter.submit_order(req)

        assert result.success is True
        # Slippage price = 99 * 1.001 = 99.099, limit = 100 → fill at min = 99.099
        assert result.trade.price <= 100.0

    async def test_limit_sell_rejected_when_price_below_limit(self, adapter, data_service):
        data_service.get_current_price.return_value = 95.0

        req = _make_request(
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            limit_price=100.0,
        )
        result = await adapter.submit_order(req)

        assert result.success is False
        assert "Limit price" in result.rejection_reason


# ---------------------------------------------------------------------------
# Price unavailable
# ---------------------------------------------------------------------------


class TestPriceUnavailable:
    async def test_no_price_returns_rejection(self, adapter, data_service):
        data_service.get_current_price.return_value = None

        result = await adapter.submit_order(_make_request())

        assert result.success is False
        assert "Could not fetch price" in result.rejection_reason


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfiguration:
    async def test_zero_slippage(self, data_service):
        adapter = PaperTradingAdapter(data_service, slippage_bps=0.0, commission_per_trade=0.0)
        data_service.get_current_price.return_value = 100.0

        result = await adapter.submit_order(_make_request(side=OrderSide.BUY))

        assert result.trade.price == 100.0
        assert result.trade.slippage == 0.0
        assert result.trade.commission == 0.0

    async def test_trade_has_correct_metadata(self, adapter, data_service):
        data_service.get_current_price.return_value = 100.0

        req = _make_request(side=OrderSide.BUY, quantity=25.0, symbol="TSLA")
        result = await adapter.submit_order(req)

        assert result.trade.symbol == "TSLA"
        assert result.trade.quantity == 25.0
        assert result.trade.side == OrderSide.BUY
        assert result.trade.branch_id == "branch-1"

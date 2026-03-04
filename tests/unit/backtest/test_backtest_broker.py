"""Tests for BacktestBrokerAdapter."""

from datetime import date

import pytest

from app.common.enums import OrderSide, OrderType
from app.common.models.order import OrderRequest
from app.modules.backtest.adapters.backtest_broker import BacktestBrokerAdapter
from app.modules.backtest.adapters.historical_data import HistoricalPriceStore
from app.modules.backtest.time_provider import BacktestTimeProvider

from .conftest import _make_price_bar


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


def _make_broker(
    sim_date: date = date(2024, 3, 15),
    price: float = 100.0,
    slippage_bps: float = 10.0,
    commission_per_trade: float = 1.50,
) -> BacktestBrokerAdapter:
    """Create a broker with a store containing a single bar at sim_date."""
    tp = BacktestTimeProvider(sim_date)
    store = HistoricalPriceStore()
    bar = _make_price_bar(timestamp=sim_date, close=price)
    store._data["AAPL"] = {sim_date: bar}
    store._sorted["AAPL"] = [bar]
    return BacktestBrokerAdapter(
        store=store,
        time_provider=tp,
        slippage_bps=slippage_bps,
        commission_per_trade=commission_per_trade,
    )


# ---------------------------------------------------------------------------
# Market orders — slippage
# ---------------------------------------------------------------------------


class TestMarketOrders:
    async def test_buy_slippage_increases_price(self):
        broker = _make_broker(price=100.0, slippage_bps=10.0)
        result = await broker.submit_order(_make_order_request(side=OrderSide.BUY))

        assert result.success is True
        # 100 * (1 + 10/10000) = 100.10
        assert result.trade.price == pytest.approx(100.10, abs=0.01)

    async def test_sell_slippage_decreases_price(self):
        broker = _make_broker(price=100.0, slippage_bps=10.0)
        result = await broker.submit_order(_make_order_request(side=OrderSide.SELL))

        assert result.success is True
        # 100 * (1 - 10/10000) = 99.90
        assert result.trade.price == pytest.approx(99.90, abs=0.01)

    async def test_short_slippage_decreases_price(self):
        broker = _make_broker(price=100.0, slippage_bps=10.0)
        result = await broker.submit_order(_make_order_request(side=OrderSide.SHORT))

        assert result.success is True
        assert result.trade.price == pytest.approx(99.90, abs=0.01)

    async def test_cover_slippage_increases_price(self):
        broker = _make_broker(price=100.0, slippage_bps=10.0)
        result = await broker.submit_order(_make_order_request(side=OrderSide.COVER))

        assert result.success is True
        assert result.trade.price == pytest.approx(100.10, abs=0.01)

    async def test_slippage_value_recorded(self):
        broker = _make_broker(price=100.0, slippage_bps=10.0)
        result = await broker.submit_order(_make_order_request(side=OrderSide.BUY))

        # slippage = 100 * (10/10000) = 0.10
        assert result.trade.slippage == pytest.approx(0.10, abs=0.01)

    async def test_commission_recorded(self):
        broker = _make_broker(price=100.0, commission_per_trade=2.50)
        result = await broker.submit_order(_make_order_request(side=OrderSide.BUY))

        assert result.trade.commission == pytest.approx(2.50)

    async def test_timestamp_from_time_provider(self):
        broker = _make_broker(sim_date=date(2024, 6, 15))
        result = await broker.submit_order(_make_order_request(side=OrderSide.BUY))

        assert result.trade.executed_at.date() == date(2024, 6, 15)

    async def test_trade_metadata_correct(self):
        broker = _make_broker()
        req = _make_order_request(symbol="AAPL", side=OrderSide.BUY, quantity=25.0)
        result = await broker.submit_order(req)

        assert result.trade.symbol == "AAPL"
        assert result.trade.quantity == 25.0
        assert result.trade.side == OrderSide.BUY


# ---------------------------------------------------------------------------
# Limit orders
# ---------------------------------------------------------------------------


class TestLimitOrders:
    async def test_limit_buy_rejected_above_limit(self):
        broker = _make_broker(price=105.0, slippage_bps=10.0)
        req = _make_order_request(
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=100.0,
        )
        result = await broker.submit_order(req)

        assert result.success is False

    async def test_limit_buy_accepted_at_or_below_limit(self):
        broker = _make_broker(price=99.0, slippage_bps=10.0)
        req = _make_order_request(
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=100.0,
        )
        result = await broker.submit_order(req)

        assert result.success is True
        assert result.trade.price <= 100.0

    async def test_limit_sell_rejected_below_limit(self):
        broker = _make_broker(price=95.0, slippage_bps=10.0)
        req = _make_order_request(
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            limit_price=100.0,
        )
        result = await broker.submit_order(req)

        assert result.success is False


# ---------------------------------------------------------------------------
# Price unavailable
# ---------------------------------------------------------------------------


class TestPriceUnavailable:
    async def test_no_price_returns_rejection(self):
        """When no price is available for the date, order should be rejected."""
        broker = _make_broker(sim_date=date(2024, 3, 15))
        # Request for a symbol with no data
        req = _make_order_request(symbol="UNKNOWN")
        result = await broker.submit_order(req)

        assert result.success is False

    async def test_uses_most_recent_close_when_exact_date_missing(self):
        """When the exact simulated date has no bar (e.g., weekend gap),
        the broker should use the most recent available close."""
        # March 15, 2024 is a Friday — create data for it
        friday = date(2024, 3, 15)
        monday = date(2024, 3, 18)  # the following Monday
        tp = BacktestTimeProvider(monday)
        store = HistoricalPriceStore()
        bar = _make_price_bar(timestamp=friday, close=100.0)
        store._data["AAPL"] = {friday: bar}
        store._sorted["AAPL"] = [bar]
        broker = BacktestBrokerAdapter(
            store=store, time_provider=tp, slippage_bps=5.0, commission_per_trade=0.0
        )

        req = _make_order_request(symbol="AAPL")
        result = await broker.submit_order(req)

        # Should succeed using Friday's close, not reject
        assert result.success is True
        # Fill price = 100.0 * (1 + 5/10000) = 100.05
        assert result.trade.price == pytest.approx(100.05, abs=0.01)

    async def test_no_data_at_all_for_symbol_returns_rejection(self):
        """Store has data for other symbols but not this one — still rejects."""
        tp = BacktestTimeProvider(date(2024, 6, 15))
        store = HistoricalPriceStore()
        # No data loaded for any symbol
        broker = BacktestBrokerAdapter(
            store=store, time_provider=tp, slippage_bps=5.0, commission_per_trade=0.0
        )

        req = _make_order_request(symbol="AAPL")
        result = await broker.submit_order(req)

        assert result.success is False


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfiguration:
    async def test_zero_slippage_and_commission(self):
        broker = _make_broker(price=100.0, slippage_bps=0.0, commission_per_trade=0.0)
        result = await broker.submit_order(_make_order_request(side=OrderSide.BUY))

        assert result.trade.price == pytest.approx(100.0)
        assert result.trade.slippage == pytest.approx(0.0)
        assert result.trade.commission == pytest.approx(0.0)

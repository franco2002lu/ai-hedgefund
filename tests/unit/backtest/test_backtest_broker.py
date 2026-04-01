"""Tests for BacktestBrokerAdapter.

The broker fills at T+1 open (market-on-open) to avoid look-ahead bias.
Slippage is applied on top of the open price using a square-root market impact model:
  effective_slippage_bps = base_slippage_bps * sqrt(participation_rate / 0.01)
  participation_rate = order_quantity / daily_volume (using day T volume)

With default test bars (volume=1,000,000) and quantity=10:
  participation_rate = 10 / 1,000,000 = 0.00001
  effective_slippage_bps = base_bps * sqrt(0.00001 / 0.01) = base_bps * sqrt(0.001)
                         = base_bps * 0.031623

When volume is unavailable (bar.volume = 0 or no bar), falls back to base_slippage_bps.
"""

import math
from datetime import date, timedelta

import pytest

from app.common.enums import OrderSide, OrderType
from app.common.models.order import OrderRequest
from app.modules.backtest.adapters.backtest_broker import BacktestBrokerAdapter
from app.modules.backtest.adapters.historical_data import HistoricalPriceStore
from app.modules.backtest.time_provider import BacktestTimeProvider

from .conftest import _make_price_bar

# Default conftest bar has volume = 1,000,000
DEFAULT_VOLUME = 1_000_000
DEFAULT_QUANTITY = 10.0
# T+1 open price used for fills (T close = 100.0 by default)
DEFAULT_NEXT_OPEN = 100.5


def _expected_effective_bps(base_bps: float, quantity: float = DEFAULT_QUANTITY, volume: int = DEFAULT_VOLUME) -> float:
    """Compute effective slippage bps using the square-root market impact model."""
    participation = quantity / volume
    return base_bps * math.sqrt(participation / 0.01)


def _make_order_request(**overrides) -> OrderRequest:
    defaults = dict(
        branch_id="branch-1",
        instrument_id="inst-1",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=DEFAULT_QUANTITY,
    )
    defaults.update(overrides)
    return OrderRequest(**defaults)


def _make_broker(
    sim_date: date = date(2024, 3, 15),
    price: float = 100.0,
    next_open: float = DEFAULT_NEXT_OPEN,
    slippage_bps: float = 10.0,
    commission_per_trade: float = 1.50,
    volume: int = DEFAULT_VOLUME,
) -> BacktestBrokerAdapter:
    """Create a broker with bars at sim_date (T) and next business day (T+1).

    The broker fills at T+1's open price (market-on-open execution).
    """
    tp = BacktestTimeProvider(sim_date)
    store = HistoricalPriceStore()
    bar_t = _make_price_bar(timestamp=sim_date, close=price, volume=volume)
    # Find the next weekday for T+1
    next_day = sim_date + timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += timedelta(days=1)
    bar_t1 = _make_price_bar(timestamp=next_day, open=next_open, close=next_open + 0.5, volume=volume)
    store._data["AAPL"] = {sim_date: bar_t, next_day: bar_t1}
    store._sorted["AAPL"] = sorted([bar_t, bar_t1], key=lambda b: b.timestamp)
    return BacktestBrokerAdapter(
        store=store,
        time_provider=tp,
        slippage_bps=slippage_bps,
        commission_per_trade=commission_per_trade,
    )


# ---------------------------------------------------------------------------
# Market orders — volume-aware slippage (square-root model)
# ---------------------------------------------------------------------------


class TestMarketOrders:
    async def test_buy_slippage_increases_price(self):
        broker = _make_broker(price=100.0, next_open=100.5, slippage_bps=10.0)
        result = await broker.submit_order(_make_order_request(side=OrderSide.BUY))

        assert result.success is True
        eff_bps = _expected_effective_bps(10.0)
        expected_price = DEFAULT_NEXT_OPEN * (1 + eff_bps / 10000)
        assert result.trade.price == pytest.approx(expected_price, rel=1e-6)
        # Buy price must be above the T+1 open
        assert result.trade.price > DEFAULT_NEXT_OPEN

    async def test_sell_slippage_decreases_price(self):
        broker = _make_broker(price=100.0, next_open=100.5, slippage_bps=10.0)
        result = await broker.submit_order(_make_order_request(side=OrderSide.SELL))

        assert result.success is True
        eff_bps = _expected_effective_bps(10.0)
        expected_price = DEFAULT_NEXT_OPEN * (1 - eff_bps / 10000)
        assert result.trade.price == pytest.approx(expected_price, rel=1e-6)
        # Sell price must be below the T+1 open
        assert result.trade.price < DEFAULT_NEXT_OPEN

    async def test_short_slippage_decreases_price(self):
        broker = _make_broker(price=100.0, next_open=100.5, slippage_bps=10.0)
        result = await broker.submit_order(_make_order_request(side=OrderSide.SHORT))

        assert result.success is True
        eff_bps = _expected_effective_bps(10.0)
        expected_price = DEFAULT_NEXT_OPEN * (1 - eff_bps / 10000)
        assert result.trade.price == pytest.approx(expected_price, rel=1e-6)

    async def test_cover_slippage_increases_price(self):
        broker = _make_broker(price=100.0, next_open=100.5, slippage_bps=10.0)
        result = await broker.submit_order(_make_order_request(side=OrderSide.COVER))

        assert result.success is True
        eff_bps = _expected_effective_bps(10.0)
        expected_price = DEFAULT_NEXT_OPEN * (1 + eff_bps / 10000)
        assert result.trade.price == pytest.approx(expected_price, rel=1e-6)

    async def test_slippage_value_recorded(self):
        broker = _make_broker(price=100.0, next_open=100.5, slippage_bps=10.0)
        result = await broker.submit_order(_make_order_request(side=OrderSide.BUY))

        eff_bps = _expected_effective_bps(10.0)
        expected_slippage = DEFAULT_NEXT_OPEN * eff_bps / 10000
        assert result.trade.slippage == pytest.approx(expected_slippage, rel=1e-6)

    async def test_higher_participation_increases_slippage(self):
        """Larger orders relative to volume produce more slippage."""
        # Small order: quantity=10, volume=1M
        broker_small = _make_broker(price=100.0, slippage_bps=10.0, volume=1_000_000)
        result_small = await broker_small.submit_order(
            _make_order_request(side=OrderSide.BUY, quantity=10.0)
        )

        # Larger order: quantity=10000, volume=1M (1% participation)
        broker_large = _make_broker(price=100.0, slippage_bps=10.0, volume=1_000_000)
        result_large = await broker_large.submit_order(
            _make_order_request(side=OrderSide.BUY, quantity=10000.0)
        )

        assert result_large.trade.slippage > result_small.trade.slippage

    async def test_no_volume_falls_back_to_base_slippage(self):
        """When bar has volume=0, broker uses base slippage as fallback."""
        broker = _make_broker(price=100.0, next_open=100.5, slippage_bps=10.0, volume=0)
        result = await broker.submit_order(_make_order_request(side=OrderSide.BUY))

        assert result.success is True
        # With no volume data, effective_slippage_bps = base_slippage_bps
        expected_price = DEFAULT_NEXT_OPEN * (1 + 10.0 / 10000)
        assert result.trade.price == pytest.approx(expected_price, rel=1e-6)

    async def test_commission_recorded(self):
        broker = _make_broker(price=100.0, commission_per_trade=2.50)
        result = await broker.submit_order(_make_order_request(side=OrderSide.BUY))

        assert result.trade.commission == pytest.approx(2.50)

    async def test_timestamp_from_time_provider(self):
        broker = _make_broker(sim_date=date(2024, 6, 14))
        result = await broker.submit_order(_make_order_request(side=OrderSide.BUY))

        assert result.trade.executed_at.date() == date(2024, 6, 14)

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
        broker = _make_broker(price=105.0, next_open=105.5, slippage_bps=10.0)
        req = _make_order_request(
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=100.0,
        )
        result = await broker.submit_order(req)

        assert result.success is False

    async def test_limit_buy_accepted_at_or_below_limit(self):
        broker = _make_broker(price=99.0, next_open=99.5, slippage_bps=10.0)
        req = _make_order_request(
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=100.0,
        )
        result = await broker.submit_order(req)

        assert result.success is True
        assert result.trade.price <= 100.0

    async def test_limit_sell_rejected_below_limit(self):
        broker = _make_broker(price=95.0, next_open=95.5, slippage_bps=10.0)
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

    async def test_uses_next_day_open_for_fill(self):
        """Broker fills at T+1 open, not at T's close."""
        friday = date(2024, 3, 15)
        monday = date(2024, 3, 18)
        tp = BacktestTimeProvider(friday)
        store = HistoricalPriceStore()
        bar_fri = _make_price_bar(timestamp=friday, close=100.0)
        bar_mon = _make_price_bar(timestamp=monday, open=101.0, close=102.0)
        store._data["AAPL"] = {friday: bar_fri, monday: bar_mon}
        store._sorted["AAPL"] = [bar_fri, bar_mon]
        broker = BacktestBrokerAdapter(
            store=store, time_provider=tp, slippage_bps=0.0, commission_per_trade=0.0
        )

        req = _make_order_request(symbol="AAPL")
        result = await broker.submit_order(req)

        # Should fill at Monday's open (101.0), not Friday's close (100.0)
        assert result.success is True
        assert result.trade.price == pytest.approx(101.0, abs=0.01)

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
        broker = _make_broker(price=100.0, next_open=100.5, slippage_bps=0.0, commission_per_trade=0.0)
        result = await broker.submit_order(_make_order_request(side=OrderSide.BUY))

        # With zero slippage, fill price = T+1 open exactly
        assert result.trade.price == pytest.approx(DEFAULT_NEXT_OPEN)
        assert result.trade.slippage == pytest.approx(0.0)
        assert result.trade.commission == pytest.approx(0.0)

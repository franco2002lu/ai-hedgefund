"""Backtest broker adapter that simulates order fills using historical prices.

Fills at T+1 open (market-on-open) to avoid look-ahead bias:
  - Orders placed on day T are filled at T+1's opening price.
  - Slippage is applied on top of the open price using a square-root
    market impact model based on day T's volume.

Square-root impact model:
  impact_bps = base_slippage_bps × sqrt(participation_rate / 0.01)

This means:
  - participation_rate < 1%  → impact ≈ base slippage (10 bps default)
  - participation_rate = 10% → impact ≈ 3× base slippage
  - participation_rate > max_participation_rate → order rejected
"""

import math
from uuid import uuid4

from app.common.enums import ExecutionMode, OrderSide, OrderType
from app.common.interfaces.broker import BrokerAdapter, OrderResult
from app.common.models.order import Order, OrderRequest
from app.common.models.trade import Trade
from app.modules.backtest.adapters.historical_data import HistoricalPriceStore
from app.modules.backtest.time_provider import BacktestTimeProvider


class BacktestBrokerAdapter(BrokerAdapter):
    """Simulates order execution against historical prices with volume-aware slippage."""

    def __init__(
        self,
        store: HistoricalPriceStore,
        time_provider: BacktestTimeProvider,
        slippage_bps: float = 10.0,
        commission_per_trade: float = 0.0,
        max_participation_rate: float = 0.10,
    ) -> None:
        self._store = store
        self._time_provider = time_provider
        self._slippage_bps = slippage_bps
        self._commission_per_trade = commission_per_trade
        self._max_participation_rate = max_participation_rate

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        today = self._time_provider.today()

        # T+1 execution: fill at next trading day's open (market-on-open)
        next_day = self._store.get_next_trading_day(order.symbol, today)
        if next_day is None:
            return OrderResult(success=False, rejection_reason="No next trading day for T+1 execution")

        open_price = self._store.get_open(order.symbol, next_day)
        if open_price is None:
            return OrderResult(success=False, rejection_reason="No open price for next trading day")

        # Use today's volume for market impact estimation (known at order time)
        bar = self._store.get_bar(order.symbol, today)
        daily_volume = bar.volume if bar and bar.volume > 0 else None

        # Compute volume-aware slippage using square-root market impact model
        if daily_volume and daily_volume > 0:
            participation_rate = order.quantity / daily_volume

            # Reject if order is too large relative to daily volume
            if participation_rate > self._max_participation_rate:
                return OrderResult(
                    success=False,
                    rejection_reason=(
                        f"Exceeds max participation rate: "
                        f"{participation_rate:.1%} > {self._max_participation_rate:.0%} "
                        f"(qty={order.quantity:.0f}, vol={daily_volume:,.0f})"
                    ),
                )

            # Square-root model: impact scales with sqrt of participation
            # Normalized so that 1% participation = base slippage
            effective_slippage_bps = self._slippage_bps * math.sqrt(
                participation_rate / 0.01
            )
        else:
            # No volume data: use base slippage as fallback
            effective_slippage_bps = self._slippage_bps

        # Apply slippage direction on top of T+1 open price
        if order.side in (OrderSide.BUY, OrderSide.COVER):
            fill_price = open_price * (1 + effective_slippage_bps / 10000)
        else:
            fill_price = open_price * (1 - effective_slippage_bps / 10000)

        # Limit order checks
        if order.order_type == OrderType.LIMIT and order.limit_price is not None:
            if order.side in (OrderSide.BUY, OrderSide.COVER):
                if fill_price > order.limit_price:
                    return OrderResult(
                        success=False,
                        rejection_reason="Limit price exceeded",
                    )
            else:
                if fill_price < order.limit_price:
                    return OrderResult(
                        success=False,
                        rejection_reason="Limit price not met",
                    )

        slippage_amount = abs(fill_price - open_price)

        trade = Trade(
            id=str(uuid4()),
            order_id=str(uuid4()),
            branch_id=order.branch_id,
            instrument_id=order.instrument_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            commission=self._commission_per_trade,
            slippage=slippage_amount,
            execution_mode=ExecutionMode.PAPER,
            executed_at=self._time_provider.now(),
        )

        return OrderResult(
            success=True,
            order_id=str(uuid4()),
            trade=trade,
        )

    async def cancel_order(self, order_id: str) -> bool:
        return False

    async def get_order_status(self, order_id: str) -> Order:
        raise NotImplementedError("Backtest broker does not support get_order_status")

    async def get_account_info(self):
        raise NotImplementedError("Backtest broker does not support get_account_info")

    def supports_asset_class(self, asset_class: str) -> bool:
        return asset_class == "equity"

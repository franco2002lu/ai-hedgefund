"""Backtest broker adapter that simulates order fills using historical prices."""

from uuid import uuid4

from app.common.enums import ExecutionMode, OrderSide, OrderType
from app.common.interfaces.broker import BrokerAdapter, OrderResult
from app.common.models.order import Order, OrderRequest
from app.common.models.trade import Trade
from app.modules.backtest.adapters.historical_data import HistoricalPriceStore
from app.modules.backtest.time_provider import BacktestTimeProvider


class BacktestBrokerAdapter(BrokerAdapter):
    """Simulates order execution against historical close prices with slippage."""

    def __init__(
        self,
        store: HistoricalPriceStore,
        time_provider: BacktestTimeProvider,
        slippage_bps: float = 5.0,
        commission_per_trade: float = 0.0,
    ) -> None:
        self._store = store
        self._time_provider = time_provider
        self._slippage_bps = slippage_bps
        self._commission_per_trade = commission_per_trade

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        close = self._store.get_latest_close(order.symbol, self._time_provider.today())
        if close is None:
            return OrderResult(success=False, rejection_reason="No price available")

        # Apply slippage
        if order.side in (OrderSide.BUY, OrderSide.COVER):
            fill_price = close * (1 + self._slippage_bps / 10000)
        else:
            fill_price = close * (1 - self._slippage_bps / 10000)

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

        slippage_amount = close * (self._slippage_bps / 10000)

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

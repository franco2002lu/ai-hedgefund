"""Paper trading adapter — simulates trade execution."""

import uuid
from datetime import UTC, datetime

from app.common.enums import ExecutionMode, OrderSide, OrderType
from app.common.interfaces.broker import AccountInfo, BrokerAdapter, OrderResult
from app.common.models.order import Order, OrderRequest
from app.common.models.trade import Trade
from app.modules.data_platform.service import DataPlatformService


class PaperTradingAdapter(BrokerAdapter):
    """
    Simulates trade execution for paper trading mode.
    - Market orders: fill immediately at current price + slippage
    - Limit orders: fill if current price meets limit condition
    """

    def __init__(
        self,
        data_platform_service: DataPlatformService,
        slippage_bps: float = 5.0,
        commission_per_trade: float = 0.0,
    ):
        self.data_service = data_platform_service
        self.slippage_bps = slippage_bps
        self.commission = commission_per_trade

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        # Fetch current market price
        current_price = await self.data_service.get_current_price(order.symbol)
        if current_price is None:
            return OrderResult(
                success=False,
                rejection_reason=f"Could not fetch price for {order.symbol}",
            )

        # Apply slippage
        slippage_mult = self.slippage_bps / 10000
        if order.side in (OrderSide.BUY, OrderSide.COVER):
            fill_price = current_price * (1 + slippage_mult)  # Pay more on buys
        else:
            fill_price = current_price * (1 - slippage_mult)  # Receive less on sells
        fill_price = round(fill_price, 4)
        slippage = round(abs(fill_price - current_price), 6)

        # Check limit order conditions
        if order.order_type == OrderType.LIMIT and order.limit_price is not None:
            if order.side in (OrderSide.BUY, OrderSide.COVER):
                if current_price > order.limit_price:
                    return OrderResult(
                        success=False,
                        rejection_reason=f"Limit price {order.limit_price} below market {current_price}",
                    )
                fill_price = min(fill_price, order.limit_price)
            else:
                if current_price < order.limit_price:
                    return OrderResult(
                        success=False,
                        rejection_reason=f"Limit price {order.limit_price} above market {current_price}",
                    )
                fill_price = max(fill_price, order.limit_price)

        trade = Trade(
            id=str(uuid.uuid4()),
            order_id="",  # Will be set by service
            branch_id=order.branch_id,
            instrument_id=order.instrument_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            commission=self.commission,
            slippage=slippage,
            execution_mode=ExecutionMode.PAPER,
            executed_at=datetime.now(UTC),
        )

        return OrderResult(
            success=True,
            trade=trade,
        )

    async def cancel_order(self, order_id: str) -> bool:
        return True  # Paper mode: all cancellations succeed

    async def get_order_status(self, order_id: str) -> Order:
        raise NotImplementedError("Paper adapter does not track order state")

    async def get_account_info(self) -> AccountInfo:
        return AccountInfo(
            buying_power=0,
            cash=0,
            portfolio_value=0,
            positions=[],
        )

    def supports_asset_class(self, asset_class: str) -> bool:
        return True  # Paper adapter supports everything

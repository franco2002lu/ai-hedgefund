"""Shared test builders for the backtest module."""

from datetime import date, timedelta

from app.common.interfaces.price_data import PriceBar
from app.modules.backtest.config import BacktestConfig
from app.modules.backtest.models import BacktestTrade, DailySnapshot


def _make_price_bar(**overrides) -> PriceBar:
    defaults = dict(
        timestamp=date(2024, 6, 14),
        open=150.0,
        high=155.0,
        low=149.0,
        close=153.0,
        volume=1_000_000,
    )
    defaults.update(overrides)
    return PriceBar(**defaults)


def _make_price_series(
    symbol: str = "AAPL",
    start: date = date(2024, 1, 2),
    days: int = 252,
    base_price: float = 100.0,
    daily_return: float = 0.0004,
) -> dict[date, PriceBar]:
    """Generate deterministic daily bars, skipping weekends.

    Returns {date: PriceBar} dict for a single symbol.
    """
    result: dict[date, PriceBar] = {}
    current = start
    price = base_price
    generated = 0
    while generated < days:
        # Skip weekends
        while current.weekday() >= 5:
            current += timedelta(days=1)
        close = round(price, 4)
        bar = PriceBar(
            timestamp=current,
            open=round(close * 0.999, 4),
            high=round(close * 1.005, 4),
            low=round(close * 0.995, 4),
            close=close,
            volume=1_000_000,
        )
        result[current] = bar
        price *= 1 + daily_return
        current += timedelta(days=1)
        generated += 1
    return result


def _make_backtest_config(**overrides) -> BacktestConfig:
    defaults = dict(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 6, 28),
        initial_capital=1_000_000.0,
        rebalance_frequency="weekly",
        branch_name="growth",
        use_llm_agents=False,
        slippage_bps=10.0,
        commission_per_trade=0.0,
        benchmark_symbols=["SPY"],
    )
    defaults.update(overrides)
    return BacktestConfig(**defaults)


def _make_daily_snapshot(**overrides) -> DailySnapshot:
    defaults = dict(
        date=date(2024, 6, 15),
        nav=1_050_000.0,
        cash=200_000.0,
        total_long_exposure=850_000.0,
        total_short_exposure=0.0,
        unrealized_pnl=50_000.0,
        realized_pnl=0.0,
        position_count=15,
        positions={"AAPL": 100_000.0, "MSFT": 80_000.0},
        daily_return=0.005,
        cumulative_return=0.05,
    )
    defaults.update(overrides)
    return DailySnapshot(**defaults)


def _make_backtest_trade(**overrides) -> BacktestTrade:
    defaults = dict(
        trade_date=date(2024, 6, 15),
        symbol="AAPL",
        side="buy",
        quantity=100.0,
        price=153.0,
        commission=0.0,
        slippage=0.0765,
        reason="new_position",
    )
    defaults.update(overrides)
    return BacktestTrade(**defaults)

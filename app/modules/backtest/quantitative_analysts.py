"""Deterministic quantitative analyst implementations for backtesting.

These replace the LLM-based analysts with rule-based scoring using
price data and fundamental metrics.
"""

import logging
import math
from datetime import timedelta

from app.modules.backtest.time_provider import BacktestTimeProvider
from app.modules.equities.models import StockSignal, UniverseStock

logger = logging.getLogger(__name__)


def _neutral_fallback(symbol: str, analyst_type: str, reason: str) -> StockSignal:
    """Return a neutral signal with minimal confidence on unexpected errors."""
    return StockSignal(
        symbol=symbol,
        analyst_type=analyst_type,
        bullish_score=5,
        confidence=1,
        summary=f"Analysis failed — {reason}",
    )


class QuantitativeNewsAnalyst:
    """Uses 1-month price momentum as a crude proxy for news sentiment."""

    def __init__(self, data_service, time_provider: BacktestTimeProvider) -> None:
        self.data_service = data_service
        self.time_provider = time_provider

    async def analyze(self, stock: UniverseStock) -> StockSignal:
        try:
            return await self._analyze_impl(stock)
        except Exception:
            logger.warning("News analyst failed for %s", stock.symbol, exc_info=True)
            return _neutral_fallback(stock.symbol, "news", "neutral fallback signal")

    async def _analyze_impl(self, stock: UniverseStock) -> StockSignal:
        end_date = self.time_provider.today()
        start_date = end_date - timedelta(days=30)
        prices = await self.data_service.get_prices(stock.symbol, start_date, end_date)

        if not prices or len(prices) <= 1:
            return StockSignal(
                symbol=stock.symbol,
                analyst_type="news",
                bullish_score=5,
                confidence=3,
                summary="Insufficient price data for momentum calculation",
            )

        first_close = prices[0].close
        last_close = prices[-1].close
        change = (last_close - first_close) / first_close

        if change > 0.03:
            score = 6
        elif change < -0.03:
            score = 4
        else:
            score = 5

        return StockSignal(
            symbol=stock.symbol,
            analyst_type="news",
            bullish_score=score,
            confidence=3,
            summary=f"1-month momentum: {change:.1%}",
        )

    async def analyze_batch(self, stocks: list[UniverseStock], **kwargs) -> list[StockSignal]:
        return [await self.analyze(stock) for stock in stocks]


class QuantitativeFundamentalsAnalyst:
    """Scores stocks based on fundamental metrics with continuous linear scaling."""

    def __init__(self, data_service, time_provider: BacktestTimeProvider) -> None:
        self.data_service = data_service
        self.time_provider = time_provider

    async def analyze(self, stock: UniverseStock) -> StockSignal:
        try:
            return await self._analyze_impl(stock)
        except Exception:
            logger.warning("Fundamentals analyst failed for %s", stock.symbol, exc_info=True)
            return _neutral_fallback(stock.symbol, "fundamentals", "neutral fallback signal")

    async def _analyze_impl(self, stock: UniverseStock) -> StockSignal:
        result = await self.data_service.get_metrics(stock.symbol, end_date=self.time_provider.today())

        # DataPlatformService wraps adapter output: {"metrics": [...], "symbol": ...}
        metrics_list = result.get("metrics", []) if isinstance(result, dict) else result

        if not metrics_list:
            return StockSignal(
                symbol=stock.symbol,
                analyst_type="fundamentals",
                bullish_score=5,
                confidence=6,
                summary="No fundamental metrics available",
            )

        m = metrics_list[0]
        base = 5.0

        # Revenue growth: linear scale [-10%, +30%] -> [-2, +2]
        rev_growth = m.get("revenue_growth", 0.0)
        rev_component = (rev_growth - (-0.10)) / (0.30 - (-0.10)) * 4 - 2

        # ROE: linear scale [0%, 25%] -> [-1, +2]
        roe = m.get("return_on_equity", 0.0)
        roe_component = (roe - 0.0) / (0.25 - 0.0) * 3 - 1

        # Debt/Equity: linear scale [2.0, 0.3] -> [-1, +1] (inverted)
        debt_eq = m.get("debt_to_equity", 1.0)
        debt_component = (2.0 - debt_eq) / (2.0 - 0.3) * 2 - 1

        # Earnings growth: linear scale [-10%, +30%] -> [-1, +1]
        earn_growth = m.get("earnings_growth", 0.0)
        earn_component = (earn_growth - (-0.10)) / (0.30 - (-0.10)) * 2 - 1

        # FCF yield: tiered - > 5%: +1, > 10%: +2
        fcf_yield = m.get("free_cash_flow_yield", 0.0)
        fcf_component = 0.0
        if fcf_yield > 0.10:
            fcf_component = 2.0
        elif fcf_yield > 0.05:
            fcf_component = 1.0

        total = base + rev_component + roe_component + debt_component + earn_component + fcf_component
        score = max(1, min(10, int(math.floor(total + 0.5))))

        return StockSignal(
            symbol=stock.symbol,
            analyst_type="fundamentals",
            bullish_score=score,
            confidence=6,
            summary=f"Fundamental score: {total:.2f}",
        )

    async def analyze_batch(self, stocks: list[UniverseStock], **kwargs) -> list[StockSignal]:
        return [await self.analyze(stock) for stock in stocks]


class QuantitativeTechnicalAnalyst:
    """Scores stocks based on technical indicators with continuous scaling."""

    def __init__(self, data_service, time_provider: BacktestTimeProvider) -> None:
        self.data_service = data_service
        self.time_provider = time_provider

    async def analyze(self, stock: UniverseStock) -> StockSignal:
        try:
            return await self._analyze_impl(stock)
        except Exception:
            logger.warning("Technical analyst failed for %s", stock.symbol, exc_info=True)
            return _neutral_fallback(stock.symbol, "technical", "neutral fallback signal")

    async def _analyze_impl(self, stock: UniverseStock) -> StockSignal:
        end_date = self.time_provider.today()
        start_date = end_date - timedelta(days=int(252 * 1.5))
        prices = await self.data_service.get_prices(stock.symbol, start_date, end_date)

        if not prices:
            return StockSignal(
                symbol=stock.symbol,
                analyst_type="technical",
                bullish_score=5,
                confidence=5,
                summary="No price data available",
            )

        closes = [p.close for p in prices]
        base = 5.0

        # 6-month return (using ~126 bars if available)
        lookback_6m = min(126, len(closes) - 1)
        if lookback_6m > 0:
            ret_6m = (closes[-1] - closes[-1 - lookback_6m]) / closes[-1 - lookback_6m]
            # Linear scale [-20%, +30%] -> [-2, +3]
            ret_component = (ret_6m - (-0.20)) / (0.30 - (-0.20)) * 5 - 2
        else:
            ret_component = 0.0

        # Volatility: daily returns std, annualized
        if len(closes) >= 2:
            daily_returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
            mean_ret = sum(daily_returns) / len(daily_returns)
            variance = sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)
            daily_std = math.sqrt(variance)
            annualized_vol = daily_std * math.sqrt(252)
            # Linear scale [20%, 50%] -> [+0.5, -2] (low vol is rewarded)
            vol_component = 0.5 - ((annualized_vol - 0.20) / (0.50 - 0.20) * 2.5)
            vol_component = max(-2, min(0.5, vol_component))
        else:
            daily_returns = []
            vol_component = 0.0

        # RSI (14-day)
        rsi_component = 0.0
        if len(closes) >= 15:
            gains = []
            losses = []
            for i in range(len(closes) - 14, len(closes)):
                diff = closes[i] - closes[i - 1]
                if diff > 0:
                    gains.append(diff)
                    losses.append(0.0)
                else:
                    gains.append(0.0)
                    losses.append(abs(diff))
            avg_gain = sum(gains) / 14
            avg_loss = sum(losses) / 14
            if avg_loss > 0:
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
            else:
                rsi = 100.0
            if rsi > 70:
                rsi_component = -1.0
            elif rsi < 30:
                rsi_component = 1.0

        # 50/200 SMA crossover
        sma_component = 0.0
        current_price = closes[-1]
        if len(closes) >= 50:
            sma_50 = sum(closes[-50:]) / 50
            sma_component += 0.5 if current_price > sma_50 else -0.5
        if len(closes) >= 200:
            sma_200 = sum(closes[-200:]) / 200
            sma_component += 0.5 if current_price > sma_200 else -0.5

        total = base + ret_component + vol_component + rsi_component + sma_component
        score = max(1, min(10, int(math.floor(total + 0.5))))

        return StockSignal(
            symbol=stock.symbol,
            analyst_type="technical",
            bullish_score=score,
            confidence=5,
            summary=f"Technical score: {total:.2f}",
        )

    async def analyze_batch(self, stocks: list[UniverseStock], **kwargs) -> list[StockSignal]:
        return [await self.analyze(stock) for stock in stocks]

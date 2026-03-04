"""Tests for the quantitative (non-LLM) analyst implementations.

Each analyst produces deterministic signals based on price data and fundamentals,
with continuous scoring via linear interpolation.
"""

from datetime import date
from unittest.mock import AsyncMock

from app.modules.backtest.quantitative_analysts import (
    QuantitativeFundamentalsAnalyst,
    QuantitativeNewsAnalyst,
    QuantitativeTechnicalAnalyst,
)
from app.modules.backtest.time_provider import BacktestTimeProvider
from app.modules.equities.models import UniverseStock


def _make_stock(symbol: str = "AAPL") -> UniverseStock:
    return UniverseStock(
        symbol=symbol,
        company_name=f"{symbol} Inc.",
        weight=0.05,
        sector="Technology",
        industry="Consumer Electronics",
    )


# ---------------------------------------------------------------------------
# QuantitativeNewsAnalyst
# ---------------------------------------------------------------------------


class TestQuantitativeNewsAnalyst:
    def _make_analyst(
        self,
        sim_date: date = date(2024, 6, 15),
        prices: dict | None = None,
    ) -> QuantitativeNewsAnalyst:
        tp = BacktestTimeProvider(sim_date)
        data_service = AsyncMock()
        if prices is not None:
            data_service.get_prices = AsyncMock(return_value=prices)
        return QuantitativeNewsAnalyst(data_service=data_service, time_provider=tp)

    async def test_positive_momentum_returns_score_6(self):
        """Price change > 3% → bullish_score = 6."""
        analyst = self._make_analyst()
        # Mock: 1-month price went up 5%
        analyst.data_service.get_prices = AsyncMock(
            return_value=[
                type("Bar", (), {"close": 100.0})(),  # start
                type("Bar", (), {"close": 105.0})(),  # end (5% gain)
            ]
        )
        signal = await analyst.analyze(_make_stock())
        assert signal.bullish_score == 6

    async def test_negative_momentum_returns_score_4(self):
        """Price change < -3% → bullish_score = 4."""
        analyst = self._make_analyst()
        analyst.data_service.get_prices = AsyncMock(
            return_value=[
                type("Bar", (), {"close": 100.0})(),
                type("Bar", (), {"close": 95.0})(),  # -5%
            ]
        )
        signal = await analyst.analyze(_make_stock())
        assert signal.bullish_score == 4

    async def test_flat_momentum_returns_score_5(self):
        """Price change between -3% and +3% → bullish_score = 5."""
        analyst = self._make_analyst()
        analyst.data_service.get_prices = AsyncMock(
            return_value=[
                type("Bar", (), {"close": 100.0})(),
                type("Bar", (), {"close": 101.0})(),  # +1%, flat
            ]
        )
        signal = await analyst.analyze(_make_stock())
        assert signal.bullish_score == 5

    async def test_confidence_always_3(self):
        """News analyst confidence is low (3) since momentum is a crude proxy."""
        analyst = self._make_analyst()
        analyst.data_service.get_prices = AsyncMock(
            return_value=[
                type("Bar", (), {"close": 100.0})(),
                type("Bar", (), {"close": 110.0})(),
            ]
        )
        signal = await analyst.analyze(_make_stock())
        assert signal.confidence == 3

    async def test_analyst_type_is_news(self):
        analyst = self._make_analyst()
        analyst.data_service.get_prices = AsyncMock(
            return_value=[
                type("Bar", (), {"close": 100.0})(),
                type("Bar", (), {"close": 100.0})(),
            ]
        )
        signal = await analyst.analyze(_make_stock())
        assert signal.analyst_type == "news"

    async def test_missing_data_returns_neutral(self):
        """When no price data available, return neutral signal."""
        analyst = self._make_analyst()
        analyst.data_service.get_prices = AsyncMock(return_value=[])
        signal = await analyst.analyze(_make_stock())
        assert signal.bullish_score == 5
        assert signal.confidence == 3

    async def test_analyze_batch_returns_all_signals(self):
        analyst = self._make_analyst()
        analyst.data_service.get_prices = AsyncMock(
            return_value=[
                type("Bar", (), {"close": 100.0})(),
                type("Bar", (), {"close": 105.0})(),
            ]
        )
        stocks = [_make_stock("AAPL"), _make_stock("MSFT"), _make_stock("GOOGL")]
        signals = await analyst.analyze_batch(stocks)
        assert len(signals) == 3
        assert all(s.analyst_type == "news" for s in signals)

    async def test_missing_data_single_price_returns_neutral(self):
        """If only one price bar, can't compute momentum."""
        analyst = self._make_analyst()
        analyst.data_service.get_prices = AsyncMock(
            return_value=[type("Bar", (), {"close": 100.0})()]
        )
        signal = await analyst.analyze(_make_stock())
        assert signal.bullish_score == 5


# ---------------------------------------------------------------------------
# QuantitativeFundamentalsAnalyst
# ---------------------------------------------------------------------------


class TestQuantitativeFundamentalsAnalyst:
    def _make_analyst(
        self,
        sim_date: date = date(2024, 6, 15),
    ) -> QuantitativeFundamentalsAnalyst:
        tp = BacktestTimeProvider(sim_date)
        data_service = AsyncMock()
        return QuantitativeFundamentalsAnalyst(data_service=data_service, time_provider=tp)

    def _mock_metrics(self, analyst, metrics: dict):
        """Set up data_service.get_metrics to return given metric dict."""
        analyst.data_service.get_metrics = AsyncMock(
            return_value=[metrics]
        )

    async def test_strong_fundamentals_high_score(self):
        """Multi-metric scoring with inline formula.

        Base = 5
        revenue_growth = 25% → linear [−10%, +30%] → [−2, +2]: (25-(-10))/(30-(-10))*4-2 = 35/40*4-2 = 1.5
        roe = 22% → linear [0%, 25%] → [−1, +2]: (22-0)/(25-0)*3-1 = 0.88*3-1 = 1.64
        debt_equity = 0.4 → linear [2.0, 0.3] → [−1, +1]: (2.0-0.4)/(2.0-0.3)*2-1 = 1.6/1.7*2-1 = 0.88
        earnings_growth = 20% → linear [−10%, +30%] → [−1, +1]: (20-(-10))/(30-(-10))*2-1 = 30/40*2-1 = 0.5
        fcf_yield = 12% → > 10%: +2
        Total = 5 + 1.5 + 1.64 + 0.88 + 0.5 + 2 = 11.52 → clamped to 10
        """
        analyst = self._make_analyst()
        self._mock_metrics(analyst, {
            "revenue_growth": 0.25,
            "return_on_equity": 0.22,
            "debt_to_equity": 0.4,
            "earnings_growth": 0.20,
            "free_cash_flow_yield": 0.12,
        })
        signal = await analyst.analyze(_make_stock())
        assert signal.bullish_score == 10  # Clamped to max

    async def test_weak_fundamentals_low_score(self):
        """Weak metrics produce a low score.

        Base = 5
        revenue_growth = -8% → roughly -1.9
        roe = 2% → roughly -0.76
        debt_equity = 1.8 → roughly -0.76
        earnings_growth = -8% → roughly -0.9
        fcf_yield = 1% → 0
        Total ≈ 5 - 1.9 - 0.76 - 0.76 - 0.9 + 0 = 0.68 → clamped to 1
        """
        analyst = self._make_analyst()
        self._mock_metrics(analyst, {
            "revenue_growth": -0.08,
            "return_on_equity": 0.02,
            "debt_to_equity": 1.8,
            "earnings_growth": -0.08,
            "free_cash_flow_yield": 0.01,
        })
        signal = await analyst.analyze(_make_stock())
        assert signal.bullish_score == 1  # Clamped to min

    async def test_average_fundamentals_around_5(self):
        """Average metrics produce score near 5."""
        analyst = self._make_analyst()
        self._mock_metrics(analyst, {
            "revenue_growth": 0.10,
            "return_on_equity": 0.12,
            "debt_to_equity": 1.0,
            "earnings_growth": 0.10,
            "free_cash_flow_yield": 0.03,
        })
        signal = await analyst.analyze(_make_stock())
        assert 4 <= signal.bullish_score <= 7

    async def test_confidence_is_6(self):
        """Fundamentals confidence is 6 (higher than news)."""
        analyst = self._make_analyst()
        self._mock_metrics(analyst, {
            "revenue_growth": 0.10,
            "return_on_equity": 0.15,
            "debt_to_equity": 0.8,
            "earnings_growth": 0.10,
            "free_cash_flow_yield": 0.05,
        })
        signal = await analyst.analyze(_make_stock())
        assert signal.confidence == 6

    async def test_analyst_type_is_fundamentals(self):
        analyst = self._make_analyst()
        self._mock_metrics(analyst, {
            "revenue_growth": 0.10,
            "return_on_equity": 0.15,
            "debt_to_equity": 0.8,
            "earnings_growth": 0.10,
            "free_cash_flow_yield": 0.05,
        })
        signal = await analyst.analyze(_make_stock())
        assert signal.analyst_type == "fundamentals"

    async def test_missing_metrics_returns_neutral(self):
        """When get_metrics returns no data, return neutral."""
        analyst = self._make_analyst()
        analyst.data_service.get_metrics = AsyncMock(return_value=[])
        signal = await analyst.analyze(_make_stock())
        assert signal.bullish_score == 5

    async def test_get_metrics_called_with_end_date(self):
        """Verifies data_service.get_metrics is called with end_date=time_provider.today()."""
        analyst = self._make_analyst(sim_date=date(2024, 3, 15))
        self._mock_metrics(analyst, {
            "revenue_growth": 0.10,
            "return_on_equity": 0.15,
            "debt_to_equity": 0.8,
            "earnings_growth": 0.10,
            "free_cash_flow_yield": 0.05,
        })
        await analyst.analyze(_make_stock("AAPL"))
        call_kwargs = analyst.data_service.get_metrics.call_args
        # Should pass end_date matching the simulated date
        assert call_kwargs.kwargs.get("end_date") == date(2024, 3, 15) or \
            (len(call_kwargs.args) > 1 and call_kwargs.args[1] == date(2024, 3, 15))

    async def test_fcf_yield_tiered_scoring(self):
        """FCF yield: > 5%: +1, > 10%: +2."""
        analyst = self._make_analyst()
        # 7% FCF yield → +1
        self._mock_metrics(analyst, {
            "revenue_growth": 0.0,
            "return_on_equity": 0.0,
            "debt_to_equity": 1.15,
            "earnings_growth": 0.0,
            "free_cash_flow_yield": 0.07,
        })
        signal_medium = await analyst.analyze(_make_stock())

        # 12% FCF yield → +2
        self._mock_metrics(analyst, {
            "revenue_growth": 0.0,
            "return_on_equity": 0.0,
            "debt_to_equity": 1.15,
            "earnings_growth": 0.0,
            "free_cash_flow_yield": 0.12,
        })
        signal_high = await analyst.analyze(_make_stock())

        assert signal_high.bullish_score > signal_medium.bullish_score

    async def test_score_clamped_to_1_10(self):
        """Score should never exceed [1, 10] range."""
        analyst = self._make_analyst()
        # Extremely negative metrics
        self._mock_metrics(analyst, {
            "revenue_growth": -0.50,
            "return_on_equity": -0.20,
            "debt_to_equity": 5.0,
            "earnings_growth": -0.50,
            "free_cash_flow_yield": -0.10,
        })
        signal = await analyst.analyze(_make_stock())
        assert 1 <= signal.bullish_score <= 10

    async def test_analyze_batch_returns_all_signals(self):
        analyst = self._make_analyst()
        self._mock_metrics(analyst, {
            "revenue_growth": 0.10,
            "return_on_equity": 0.15,
            "debt_to_equity": 0.8,
            "earnings_growth": 0.10,
            "free_cash_flow_yield": 0.05,
        })
        stocks = [_make_stock("AAPL"), _make_stock("MSFT")]
        signals = await analyst.analyze_batch(stocks)
        assert len(signals) == 2


# ---------------------------------------------------------------------------
# QuantitativeTechnicalAnalyst
# ---------------------------------------------------------------------------


class TestQuantitativeTechnicalAnalyst:
    def _make_analyst(
        self,
        sim_date: date = date(2024, 6, 15),
    ) -> QuantitativeTechnicalAnalyst:
        tp = BacktestTimeProvider(sim_date)
        data_service = AsyncMock()
        return QuantitativeTechnicalAnalyst(data_service=data_service, time_provider=tp)

    def _mock_prices(self, analyst, bars: list):
        """Set up data_service.get_prices to return bars."""
        analyst.data_service.get_prices = AsyncMock(return_value=bars)

    async def test_strong_uptrend_high_score(self):
        """Strong 6-month return + favorable technicals → high score."""
        analyst = self._make_analyst()
        # Generate uptrending prices
        bars = []
        for i in range(252):
            price = 100.0 * (1.001 ** i)  # steady uptrend
            bars.append(type("Bar", (), {"close": price, "timestamp": None})())
        self._mock_prices(analyst, bars)
        signal = await analyst.analyze(_make_stock())
        assert signal.bullish_score >= 7

    async def test_strong_downtrend_low_score(self):
        """Strong negative return → low score."""
        analyst = self._make_analyst()
        bars = []
        for i in range(252):
            price = 100.0 * (0.999 ** i)  # steady downtrend
            bars.append(type("Bar", (), {"close": price, "timestamp": None})())
        self._mock_prices(analyst, bars)
        signal = await analyst.analyze(_make_stock())
        assert signal.bullish_score <= 4

    async def test_confidence_is_5(self):
        """Technical confidence is 5 (medium — backward-looking)."""
        analyst = self._make_analyst()
        bars = [type("Bar", (), {"close": 100.0, "timestamp": None})() for _ in range(252)]
        self._mock_prices(analyst, bars)
        signal = await analyst.analyze(_make_stock())
        assert signal.confidence == 5

    async def test_analyst_type_is_technical(self):
        analyst = self._make_analyst()
        bars = [type("Bar", (), {"close": 100.0, "timestamp": None})() for _ in range(252)]
        self._mock_prices(analyst, bars)
        signal = await analyst.analyze(_make_stock())
        assert signal.analyst_type == "technical"

    async def test_uses_time_provider_for_lookback(self):
        """Verifies the analyst uses time_provider.today() not date.today()."""
        analyst = self._make_analyst(sim_date=date(2024, 3, 15))
        bars = [type("Bar", (), {"close": 100.0, "timestamp": None})() for _ in range(252)]
        self._mock_prices(analyst, bars)
        await analyst.analyze(_make_stock())
        # get_prices should be called with dates relative to 2024-03-15
        call_args = analyst.data_service.get_prices.call_args
        # The end_date should not be today's actual date
        # It should be relative to the simulated date (2024-03-15)
        if call_args and call_args.kwargs.get("end_date"):
            assert call_args.kwargs["end_date"] <= date(2024, 3, 15)

    async def test_missing_data_returns_neutral(self):
        analyst = self._make_analyst()
        self._mock_prices(analyst, [])
        signal = await analyst.analyze(_make_stock())
        assert signal.bullish_score == 5

    async def test_score_clamped_to_1_10(self):
        analyst = self._make_analyst()
        # Extreme data
        bars = []
        for i in range(252):
            price = 100.0 * (1.01 ** i)  # extreme uptrend
            bars.append(type("Bar", (), {"close": price, "timestamp": None})())
        self._mock_prices(analyst, bars)
        signal = await analyst.analyze(_make_stock())
        assert 1 <= signal.bullish_score <= 10

    async def test_analyze_batch_returns_all_signals(self):
        analyst = self._make_analyst()
        bars = [type("Bar", (), {"close": 100.0, "timestamp": None})() for _ in range(252)]
        self._mock_prices(analyst, bars)
        stocks = [_make_stock("AAPL"), _make_stock("MSFT")]
        signals = await analyst.analyze_batch(stocks)
        assert len(signals) == 2

    async def test_high_volatility_reduces_score(self):
        """High volatility should reduce the score."""
        analyst_volatile = self._make_analyst()
        bars_volatile = []
        for i in range(252):
            # Alternating prices creating high volatility with flat return
            price = 100.0 + (10.0 if i % 2 == 0 else -10.0)
            bars_volatile.append(type("Bar", (), {"close": price, "timestamp": None})())
        self._mock_prices(analyst_volatile, bars_volatile)
        signal_volatile = await analyst_volatile.analyze(_make_stock())

        analyst_stable = self._make_analyst()
        bars_stable = [type("Bar", (), {"close": 100.0, "timestamp": None})() for _ in range(252)]
        self._mock_prices(analyst_stable, bars_stable)
        signal_stable = await analyst_stable.analyze(_make_stock())

        # Higher volatility should produce lower or equal score
        assert signal_volatile.bullish_score <= signal_stable.bullish_score


# ---------------------------------------------------------------------------
# Safety: unexpected errors → neutral fallback
# ---------------------------------------------------------------------------


class TestNewsAnalystSafety:
    async def test_data_service_error_returns_neutral_fallback(self):
        """If data_service.get_prices raises, analyze() returns neutral (score=5, confidence=1)."""
        tp = BacktestTimeProvider(date(2024, 6, 15))
        ds = AsyncMock()
        ds.get_prices = AsyncMock(side_effect=RuntimeError("network timeout"))
        analyst = QuantitativeNewsAnalyst(data_service=ds, time_provider=tp)

        signal = await analyst.analyze(_make_stock())
        assert signal.bullish_score == 5
        assert signal.confidence == 1


class TestFundamentalsAnalystSafety:
    async def test_data_service_error_returns_neutral_fallback(self):
        """If data_service.get_metrics raises, analyze() returns neutral (score=5, confidence=1)."""
        tp = BacktestTimeProvider(date(2024, 6, 15))
        ds = AsyncMock()
        ds.get_metrics = AsyncMock(side_effect=RuntimeError("network timeout"))
        analyst = QuantitativeFundamentalsAnalyst(data_service=ds, time_provider=tp)

        signal = await analyst.analyze(_make_stock())
        assert signal.bullish_score == 5
        assert signal.confidence == 1


class TestTechnicalAnalystSafety:
    async def test_data_service_error_returns_neutral_fallback(self):
        """If data_service.get_prices raises, analyze() returns neutral (score=5, confidence=1)."""
        tp = BacktestTimeProvider(date(2024, 6, 15))
        ds = AsyncMock()
        ds.get_prices = AsyncMock(side_effect=RuntimeError("network timeout"))
        analyst = QuantitativeTechnicalAnalyst(data_service=ds, time_provider=tp)

        signal = await analyst.analyze(_make_stock())
        assert signal.bullish_score == 5
        assert signal.confidence == 1

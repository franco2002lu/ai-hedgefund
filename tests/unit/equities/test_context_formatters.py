"""Unit tests for the context normalization layer (TDD — tests written first).

Tests cover:
- _fmt_number: number formatting helper
- _render_table: markdown table rendering
- _compute_sma, _compute_rsi, _compute_macd: technical indicators
- _compute_volume_trend, _find_support_resistance, _compute_returns: more indicators
- format_fundamentals_context: full fundamentals markdown output
- format_technical_context: full technical markdown output
- format_news_context: full news markdown output
"""

from datetime import UTC, date, datetime

import pytest

from app.modules.equities.agents.context_formatters import (
    _compute_macd,
    _compute_returns,
    _compute_rsi,
    _compute_sma,
    _compute_volume_trend,
    _find_support_resistance,
    _fmt_number,
    _render_table,
    format_fundamentals_context,
    format_news_context,
    format_technical_context,
)

# ---------------------------------------------------------------------------
# Fixtures / Builders
# ---------------------------------------------------------------------------


def _make_metrics(**overrides) -> dict:
    """Build a realistic metrics dict matching YahooFinanceAdapter output."""
    defaults = {
        "symbol": "AAPL",
        "report_period": "2026-03-20",
        "period": "ttm",
        "currency": "USD",
        "market_cap": 2_850_000_000_000,
        "enterprise_value": 2_900_000_000_000,
        "pe_ratio": 28.5,
        "forward_pe": 24.1,
        "peg_ratio": 1.8,
        "price_to_book": 45.2,
        "price_to_sales": 8.1,
        "revenue": 390_000_000_000,
        "revenue_growth": 0.081,
        "gross_margins": 0.462,
        "operating_margins": 0.315,
        "profit_margins": 0.253,
        "earnings_growth": 0.125,
        "return_on_equity": 1.574,
        "return_on_assets": 0.281,
        "debt_to_equity": 1.87,
        "current_ratio": 0.99,
        "free_cash_flow": 111_400_000_000,
        "operating_cash_flow": 118_300_000_000,
        "dividend_yield": 0.0044,
        "beta": 1.24,
        "52_week_high": 260.10,
        "52_week_low": 164.08,
        "fcfYield": 0.039,
    }
    defaults.update(overrides)
    return defaults


class _FakeEarning:
    """Mimics SEC Edgar earnings objects used by the analyst."""

    def __init__(self, fiscal_quarter="Q4 2025", eps=2.40, revenue=124_300_000_000):
        self.fiscal_quarter = fiscal_quarter
        self.eps = eps
        self.revenue = revenue


def _make_earnings(count=4) -> list[_FakeEarning]:
    data = [
        ("Q4 2025", 2.40, 124_300_000_000),
        ("Q3 2025", 1.64, 85_800_000_000),
        ("Q2 2025", 1.53, 81_800_000_000),
        ("Q1 2025", 2.18, 119_600_000_000),
    ]
    return [_FakeEarning(*d) for d in data[:count]]


def _make_bars(num_bars=252, base_price=200.0, base_volume=50_000_000) -> list[dict]:
    """Generate synthetic OHLCV bars with a mild uptrend."""
    bars = []
    for i in range(num_bars):
        close = base_price + i * 0.2
        bars.append({
            "timestamp": str(date(2025, 1, 2).toordinal() + i),  # placeholder
            "open": close - 1.0,
            "high": close + 1.5,
            "low": close - 1.5,
            "close": close,
            "volume": base_volume + i * 1000,
        })
    return bars


def _make_articles(count=5, base_date=None) -> list[dict]:
    """Generate synthetic news article dicts matching DataPlatformService output."""
    base = base_date or datetime(2026, 3, 18, 12, 0, 0, tzinfo=UTC)
    titles = [
        "Apple unveils new M4 MacBook Air lineup",
        "Apple services revenue hits record quarterly run rate",
        "iPhone 16 sales outpace iPhone 15 by 12%",
        "EU antitrust probe into Apple Pay expands",
        "Apple increases share buyback authorization",
        "Apple AI features drive App Store growth",
        "Warren Buffett trims Apple stake by 2%",
        "Apple car project officially shelved",
    ]
    sources = ["Reuters", "Bloomberg", "CNBC", "WSJ", "Reuters", "Bloomberg", "CNBC", "WSJ"]
    articles = []
    for i in range(min(count, len(titles))):
        pub = datetime(
            base.year, base.month, max(1, base.day - i * 3),
            12, 0, 0, tzinfo=UTC,
        )
        articles.append({
            "title": titles[i],
            "author": sources[i],
            "source": "yahoo_finance",
            "published_at": pub.isoformat(),
            "url": f"https://example.com/article-{i}",
            "symbols": ["AAPL"],
        })
    return articles


# ===========================================================================
# _fmt_number tests
# ===========================================================================


class TestFmtNumber:
    def test_none_returns_dash(self):
        assert _fmt_number(None, "auto") == "--"
        assert _fmt_number(None, "pct") == "--"
        assert _fmt_number(None, "dollar") == "--"
        assert _fmt_number(None, "ratio") == "--"

    def test_trillions(self):
        result = _fmt_number(2_850_000_000_000, "dollar")
        assert "T" in result
        assert "$" in result
        assert "2.85" in result

    def test_billions(self):
        result = _fmt_number(111_400_000_000, "dollar")
        assert "B" in result
        assert "$" in result

    def test_millions(self):
        result = _fmt_number(52_300_000, "auto")
        assert "M" in result

    def test_small_number(self):
        result = _fmt_number(1234, "auto")
        assert "1,234" in result

    def test_percentage_positive(self):
        result = _fmt_number(0.081, "pct")
        assert "+" in result
        assert "8.1%" in result

    def test_percentage_negative(self):
        result = _fmt_number(-0.034, "pct")
        assert "-" in result
        assert "3.4%" in result

    def test_percentage_near_zero(self):
        result = _fmt_number(0.0001, "pct")
        assert "0.0%" in result

    def test_ratio(self):
        result = _fmt_number(28.5, "ratio")
        assert "28.50" in result

    def test_zero(self):
        result = _fmt_number(0, "auto")
        assert result == "0"

    def test_negative_dollar(self):
        result = _fmt_number(-5_000_000_000, "dollar")
        assert "-" in result
        assert "B" in result


# ===========================================================================
# _render_table tests
# ===========================================================================


class TestRenderTable:
    def test_basic_two_column(self):
        result = _render_table(["Metric", "Value"], [("P/E", "28.50"), ("ROE", "15.7%")])
        lines = result.strip().split("\n")
        assert len(lines) == 4  # header + separator + 2 data rows
        assert "| Metric | Value |" in lines[0]
        assert "|---|---|" in lines[1]
        assert "| P/E | 28.50 |" in lines[2]

    def test_three_column(self):
        result = _render_table(
            ["Metric", "Value", "Sector"],
            [("P/E", "28.50", "25.20")],
        )
        assert "| Metric | Value | Sector |" in result
        assert "|---|---|---|" in result

    def test_empty_rows(self):
        result = _render_table(["Metric", "Value"], [])
        # Should still render header
        assert "| Metric | Value |" in result

    def test_special_characters_in_values(self):
        result = _render_table(["Metric", "Value"], [("52-Week", "$164.08 — $260.10")])
        assert "$164.08" in result


# ===========================================================================
# Technical indicator tests
# ===========================================================================


class TestComputeSma:
    def test_basic_sma(self):
        closes = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert _compute_sma(closes, 3) == pytest.approx(40.0)  # (30+40+50)/3
        assert _compute_sma(closes, 5) == pytest.approx(30.0)  # (10+20+30+40+50)/5

    def test_insufficient_data(self):
        assert _compute_sma([10.0, 20.0], 5) is None

    def test_single_period(self):
        assert _compute_sma([42.0], 1) == pytest.approx(42.0)


class TestComputeRsi:
    def test_all_gains_returns_100(self):
        # 15 consecutive up days
        closes = [float(i) for i in range(100, 116)]
        rsi = _compute_rsi(closes, 14)
        assert rsi == pytest.approx(100.0)

    def test_all_losses_returns_near_zero(self):
        # 15 consecutive down days
        closes = [float(i) for i in range(115, 99, -1)]
        rsi = _compute_rsi(closes, 14)
        assert rsi == pytest.approx(0.0, abs=0.1)

    def test_mixed_returns_between_0_and_100(self):
        closes = [100.0, 102.0, 101.0, 103.0, 102.0, 104.0, 103.0,
                  105.0, 104.0, 106.0, 105.0, 107.0, 106.0, 108.0, 107.0]
        rsi = _compute_rsi(closes, 14)
        assert 0 < rsi < 100

    def test_insufficient_data(self):
        assert _compute_rsi([100.0, 101.0], 14) is None


class TestComputeMacd:
    def test_returns_three_values(self):
        # Need at least slow+signal = 26+9 = 35 bars minimum
        closes = [100.0 + i * 0.5 for i in range(50)]
        result = _compute_macd(closes)
        assert result is not None
        macd_line, signal_line, histogram = result
        assert isinstance(macd_line, float)
        assert isinstance(signal_line, float)
        assert histogram == pytest.approx(macd_line - signal_line)

    def test_insufficient_data(self):
        closes = [100.0 + i for i in range(10)]
        assert _compute_macd(closes) is None

    def test_uptrend_positive_macd(self):
        # Strong uptrend should produce positive MACD
        closes = [100.0 + i * 2.0 for i in range(60)]
        result = _compute_macd(closes)
        assert result is not None
        assert result[0] > 0  # MACD line positive in uptrend


class TestComputeReturns:
    def test_basic_returns(self):
        # 253 bars: need len > 252 for 12-month lookback
        closes = [100.0] * 253
        closes[-1] = 110.0  # 10% total return
        result = _compute_returns(closes)
        assert "return_12m" in result
        assert result["return_12m"] == pytest.approx(0.10)

    def test_insufficient_data_partial_returns(self):
        closes = [100.0, 105.0, 110.0]
        result = _compute_returns(closes)
        assert "return_1w" in result
        # Should have return_1w but not return_12m
        assert result.get("return_12m") is None

    def test_single_bar(self):
        result = _compute_returns([100.0])
        # All returns should be None with 1 bar
        assert all(v is None for v in result.values())


class TestComputeVolumeTrend:
    def test_above_average(self):
        # Recent volumes higher than longer-term average
        volumes = [1_000_000] * 50 + [2_000_000] * 20
        result = _compute_volume_trend(volumes)
        assert result["volume_ratio"] > 1.0
        assert result["trend"] == "above average"

    def test_below_average(self):
        # Recent volumes lower than longer-term average
        volumes = [2_000_000] * 50 + [1_000_000] * 20
        result = _compute_volume_trend(volumes)
        assert result["volume_ratio"] < 1.0
        assert result["trend"] == "below average"

    def test_normal_volume(self):
        # Similar volumes throughout
        volumes = [1_000_000] * 70
        result = _compute_volume_trend(volumes)
        assert result["volume_ratio"] == pytest.approx(1.0)
        assert result["trend"] == "normal"

    def test_insufficient_data(self):
        result = _compute_volume_trend([1_000_000] * 5)
        assert result["avg_volume_20d"] is None


class TestFindSupportResistance:
    def test_returns_levels(self):
        # Create data with clear high and low points
        highs = [105.0 + (i % 10) for i in range(60)]
        lows = [95.0 + (i % 10) for i in range(60)]
        closes = [100.0 + (i % 10) for i in range(60)]
        result = _find_support_resistance(highs, lows, closes)
        assert "resistance_1" in result
        assert "support_1" in result
        assert result["resistance_1"] >= result["support_1"]

    def test_insufficient_data(self):
        result = _find_support_resistance([100.0], [99.0], [99.5])
        assert result["resistance_1"] is None
        assert result["support_1"] is None


# ===========================================================================
# format_fundamentals_context tests
# ===========================================================================


class TestFormatFundamentalsContext:
    def test_contains_header(self):
        result = format_fundamentals_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            metrics=_make_metrics(),
            earnings=[],
        )
        assert "AAPL" in result
        assert "Apple Inc." in result
        assert "Fundamentals" in result
        assert "Technology" in result

    def test_contains_valuation_section(self):
        result = format_fundamentals_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            metrics=_make_metrics(),
            earnings=[],
        )
        assert "## Valuation" in result
        assert "P/E (TTM)" in result
        assert "28.50" in result

    def test_contains_profitability_section(self):
        result = format_fundamentals_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            metrics=_make_metrics(),
            earnings=[],
        )
        assert "## Profitability" in result
        assert "Gross Margin" in result
        assert "46.2%" in result

    def test_contains_growth_section(self):
        result = format_fundamentals_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            metrics=_make_metrics(),
            earnings=[],
        )
        assert "## Growth" in result
        assert "Revenue Growth (YoY)" in result
        assert "+8.1%" in result

    def test_contains_financial_health_section(self):
        result = format_fundamentals_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            metrics=_make_metrics(),
            earnings=[],
        )
        assert "## Financial Health" in result
        assert "Debt/Equity" in result
        assert "1.87" in result

    def test_contains_dividends_risk_section(self):
        result = format_fundamentals_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            metrics=_make_metrics(),
            earnings=[],
        )
        assert "## Dividends" in result
        assert "Beta" in result
        assert "1.24" in result

    def test_contains_earnings_table(self):
        result = format_fundamentals_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            metrics=_make_metrics(),
            earnings=_make_earnings(4),
        )
        assert "## Recent Earnings" in result
        assert "Q4 2025" in result
        assert "$2.40" in result
        assert "$124.3B" in result

    def test_empty_metrics(self):
        result = format_fundamentals_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            metrics={},
            earnings=[],
        )
        assert "AAPL" in result
        assert "No fundamental metrics" in result

    def test_include_sections_filters(self):
        result = format_fundamentals_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            metrics=_make_metrics(),
            earnings=[],
            include_sections=frozenset({"valuation", "growth"}),
        )
        assert "## Valuation" in result
        assert "## Growth" in result
        assert "## Profitability" not in result
        assert "## Financial Health" not in result
        assert "## Dividends" not in result

    def test_sector_medians_adds_column(self):
        result = format_fundamentals_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            metrics=_make_metrics(),
            earnings=[],
            sector_medians={"pe_ratio": 25.2},
        )
        assert "Sector Median" in result
        assert "25.20" in result

    def test_none_metrics_omitted(self):
        """Metrics with None values should not appear in the table."""
        metrics = _make_metrics(forward_pe=None, peg_ratio=None)
        result = format_fundamentals_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            metrics=metrics,
            earnings=[],
        )
        assert "Forward P/E" not in result
        assert "PEG Ratio" not in result
        # But other valuation metrics should still be present
        assert "P/E (TTM)" in result

    def test_uses_markdown_tables(self):
        result = format_fundamentals_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            metrics=_make_metrics(),
            earnings=[],
        )
        assert "|" in result
        assert "|---|" in result

    def test_no_sector(self):
        result = format_fundamentals_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector=None,
            metrics=_make_metrics(),
            earnings=[],
        )
        assert "AAPL" in result
        # Should handle None sector gracefully
        assert "None" not in result or "Unknown" in result


# ===========================================================================
# format_technical_context tests
# ===========================================================================


class TestFormatTechnicalContext:
    def test_contains_header(self):
        result = format_technical_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            bars=_make_bars(252),
        )
        assert "AAPL" in result
        assert "Apple Inc." in result
        assert "Technical" in result

    def test_contains_price_summary(self):
        result = format_technical_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            bars=_make_bars(252),
        )
        assert "## Price Summary" in result
        assert "Return" in result

    def test_contains_moving_averages(self):
        result = format_technical_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            bars=_make_bars(252),
        )
        assert "## Moving Averages" in result
        assert "SMA 20" in result
        assert "SMA 50" in result
        assert "SMA 200" in result
        # Should show vs Price signal
        assert "above" in result or "below" in result

    def test_contains_momentum(self):
        result = format_technical_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            bars=_make_bars(252),
        )
        assert "## Momentum" in result
        assert "RSI" in result

    def test_contains_volume_analysis(self):
        result = format_technical_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            bars=_make_bars(252),
        )
        assert "## Volume" in result

    def test_contains_support_resistance(self):
        result = format_technical_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            bars=_make_bars(252),
        )
        assert "## Support" in result

    def test_contains_recent_price_action(self):
        result = format_technical_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            bars=_make_bars(252),
        )
        assert "Recent Price Action" in result
        # Should have OHLCV columns
        assert "Open" in result
        assert "High" in result
        assert "Low" in result
        assert "Close" in result
        assert "Volume" in result

    def test_contains_current_price(self):
        bars = _make_bars(10, base_price=245.0)
        result = format_technical_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            bars=bars,
        )
        assert "Current Price" in result

    def test_empty_bars(self):
        result = format_technical_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            bars=[],
        )
        assert "AAPL" in result
        assert "No price data" in result

    def test_few_bars_omits_long_sma(self):
        """With < 200 bars, SMA 200 should not appear."""
        result = format_technical_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            bars=_make_bars(30),
        )
        assert "SMA 20" in result
        assert "SMA 200" not in result

    def test_as_of_date_in_header(self):
        result = format_technical_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            bars=_make_bars(10),
            as_of_date=date(2026, 3, 20),
        )
        assert "2026-03-20" in result

    def test_uses_markdown_tables(self):
        result = format_technical_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            bars=_make_bars(252),
        )
        assert "|" in result
        assert "|---|" in result

    def test_bar_count_in_header(self):
        result = format_technical_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            bars=_make_bars(252),
        )
        assert "252" in result


# ===========================================================================
# format_news_context tests
# ===========================================================================


class TestFormatNewsContext:
    def test_contains_header(self):
        result = format_news_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            articles=_make_articles(5),
        )
        assert "AAPL" in result
        assert "Apple Inc." in result
        assert "News" in result
        assert "Technology" in result

    def test_contains_article_count(self):
        result = format_news_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            articles=_make_articles(5),
        )
        assert "5" in result

    def test_contains_time_buckets(self):
        result = format_news_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            articles=_make_articles(8, base_date=datetime(2026, 3, 18, 12, 0, 0, tzinfo=UTC)),
        )
        # Should have at least one bucket header
        has_bucket = (
            "Last 7 Days" in result
            or "Last 30 Days" in result
            or "Older" in result
        )
        assert has_bucket

    def test_contains_source_attribution(self):
        result = format_news_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            articles=_make_articles(3),
        )
        assert "Reuters" in result or "Bloomberg" in result or "CNBC" in result

    def test_contains_headlines(self):
        result = format_news_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            articles=_make_articles(3),
        )
        assert "Apple unveils new M4 MacBook Air lineup" in result

    def test_empty_articles(self):
        result = format_news_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            articles=[],
        )
        assert "AAPL" in result
        assert "No recent news" in result

    def test_articles_sorted_chronologically(self):
        """Articles should be ordered newest-first within each bucket."""
        articles = _make_articles(3)
        result = format_news_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            articles=articles,
        )
        # First article title should appear before third article title
        idx_first = result.index("Apple unveils new M4 MacBook Air lineup")
        idx_third = result.index("iPhone 16 sales outpace")
        assert idx_first < idx_third

    def test_uses_markdown_tables(self):
        result = format_news_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            articles=_make_articles(3),
        )
        assert "|" in result
        assert "|---|" in result

    def test_no_sector(self):
        result = format_news_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector=None,
            articles=_make_articles(2),
        )
        assert "AAPL" in result
        assert "None" not in result or "Unknown" in result

    def test_articles_with_missing_fields(self):
        """Articles missing author/published_at should still render."""
        articles = [
            {"title": "Some headline", "source": "yahoo_finance", "symbols": ["AAPL"]},
        ]
        result = format_news_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            articles=articles,
        )
        assert "Some headline" in result

    def test_compact_date_format(self):
        """Dates should use compact format like 'Mar 18', not ISO."""
        articles = _make_articles(1, base_date=datetime(2026, 3, 18, 12, 0, 0, tzinfo=UTC))
        result = format_news_context(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            articles=articles,
        )
        assert "Mar 18" in result
        # Should NOT contain full ISO format
        assert "2026-03-18T" not in result

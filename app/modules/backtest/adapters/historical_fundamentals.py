"""Historical Fundamentals Store — point-in-time fundamental metrics from SEC EDGAR.

Combines SEC EDGAR XBRL quarterly filings with historical price data to produce
point-in-time fundamental metrics for any simulated date.
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta

from app.modules.backtest.adapters.historical_data import HistoricalPriceStore
from app.modules.backtest.adapters.xbrl_mapping import (
    FLOW_ITEMS,
    STOCK_ITEMS,
    QuarterlySnapshot,
    build_quarterly_snapshots,
    compute_ttm,
    get_all_xbrl_concept_names,
    get_latest_stock_value,
)
from app.modules.data_platform.adapters.sec_edgar_client import (
    SECEdgarClient,
    parse_xbrl_filings,
)

logger = logging.getLogger(__name__)


class HistoricalFundamentalsStore:
    """Point-in-time fundamentals store backed by SEC EDGAR quarterly filings.

    Preloads all XBRL data for a stock universe, then serves metrics
    for any date using only data that was publicly available at that time
    (based on SEC filing dates).
    """

    def __init__(self, price_store: HistoricalPriceStore) -> None:
        self._price_store = price_store
        self._snapshots: dict[str, list[QuarterlySnapshot]] = {}
        self._company_info: dict[str, dict] = {}  # ticker → {entityName, ...}
        self._ticker_cik: dict[str, int] = {}
        self.failed_symbols: set[str] = set()

    @property
    def loaded_symbols(self) -> set[str]:
        return set(self._snapshots.keys())

    async def preload(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
    ) -> None:
        """Fetch and parse SEC EDGAR data for all symbols.

        Args:
            symbols: Ticker symbols to load.
            start_date: Backtest start date.
            end_date: Backtest end date.
        """
        client = SECEdgarClient()

        # 1. Fetch ticker → CIK mapping
        logger.info("Fetching SEC ticker-CIK mapping...")
        try:
            cik_map = await client.fetch_ticker_cik_map()
        except Exception:
            logger.error("Failed to fetch SEC ticker-CIK mapping", exc_info=True)
            self.failed_symbols = set(symbols)
            return

        self._ticker_cik = {s: cik_map[s] for s in symbols if s in cik_map}
        missing_cik = set(symbols) - set(self._ticker_cik.keys())
        if missing_cik:
            logger.warning("No CIK found for %d symbols: %s", len(missing_cik), sorted(missing_cik)[:10])
            self.failed_symbols.update(missing_cik)

        # 2. Fetch company facts in batches
        ticker_cik_pairs = list(self._ticker_cik.items())
        logger.info("Fetching SEC EDGAR facts for %d symbols...", len(ticker_cik_pairs))

        all_facts = await client.fetch_company_facts_batch(ticker_cik_pairs)
        logger.info("SEC EDGAR: received facts for %d/%d symbols", len(all_facts), len(ticker_cik_pairs))

        # 3. Parse XBRL into QuarterlySnapshots
        concepts = get_all_xbrl_concept_names()
        # Fetch filings from 2 years before start to ensure TTM data is available
        filter_start = start_date - timedelta(days=730)

        for ticker, facts in all_facts.items():
            try:
                self._company_info[ticker] = {
                    "entityName": facts.get("entityName", ticker),
                }
                records = parse_xbrl_filings(facts, concepts, filter_start, end_date)
                snapshots = build_quarterly_snapshots(records)
                if snapshots:
                    self._snapshots[ticker] = snapshots
                else:
                    self.failed_symbols.add(ticker)
                    logger.debug("No quarterly snapshots parsed for %s", ticker)
            except Exception:
                self.failed_symbols.add(ticker)
                logger.debug("Failed to parse XBRL for %s", ticker, exc_info=True)

        self.failed_symbols.update(set(self._ticker_cik.keys()) - set(all_facts.keys()))

        loaded = len(self._snapshots)
        total = len(symbols)
        logger.info(
            "Historical fundamentals loaded: %d/%d symbols (%d failed)",
            loaded,
            total,
            len(self.failed_symbols),
        )

    def get_metrics_as_of(self, symbol: str, as_of_date: date) -> list[dict]:
        """Get fundamental metrics for a symbol as of a specific date.

        Only uses data from SEC filings that were publicly available
        (filed_date <= as_of_date). Combines with historical price data
        to compute valuation ratios.

        Args:
            symbol: Ticker symbol.
            as_of_date: The simulated date (point-in-time).

        Returns:
            List containing one metrics dict (same schema as YahooFinanceAdapter),
            or empty list if no data available.
        """
        snapshots = self._snapshots.get(symbol)
        if not snapshots:
            return []

        # --- Extract fundamental values ---
        metrics_raw: dict[str, float | None] = {}

        # Flow items: TTM
        for key in FLOW_ITEMS:
            metrics_raw[key] = compute_ttm(snapshots, key, as_of_date)

        # Stock items: latest value
        for key in STOCK_ITEMS:
            metrics_raw[key] = get_latest_stock_value(snapshots, key, as_of_date)

        # Check we have at least some data
        if all(v is None for v in metrics_raw.values()):
            return []

        # --- Price-derived metrics from HistoricalPriceStore ---
        current_price = self._price_store.get_latest_close(symbol, as_of_date)
        shares = metrics_raw.get("shares_outstanding")

        market_cap: float | None = None
        if current_price and shares and shares > 0:
            market_cap = current_price * shares

        # Compute volume, volatility, return, 52-week range from OHLCV
        price_metrics = self._compute_price_derived_metrics(symbol, as_of_date)

        # --- Compute ratios ---
        revenue = metrics_raw.get("revenue")
        net_income = metrics_raw.get("net_income")
        gross_profit = metrics_raw.get("gross_profit")
        operating_income = metrics_raw.get("operating_income")
        equity = metrics_raw.get("stockholders_equity")
        total_assets = metrics_raw.get("total_assets")
        total_liabilities = metrics_raw.get("total_liabilities")
        long_term_debt = metrics_raw.get("long_term_debt")
        current_assets = metrics_raw.get("current_assets")
        current_liabilities = metrics_raw.get("current_liabilities")
        operating_cf = metrics_raw.get("operating_cash_flow")
        capex = metrics_raw.get("capex")
        eps = metrics_raw.get("eps_diluted")
        dividends_ps = metrics_raw.get("dividends_per_share")

        # Valuation ratios
        pe_ratio = _safe_div(current_price, eps) if current_price and eps else None
        price_to_book = None
        if current_price and equity and shares and shares > 0:
            bvps = equity / shares
            price_to_book = _safe_div(current_price, bvps) if bvps else None
        price_to_sales = _safe_div(market_cap, revenue) if market_cap and revenue else None

        # Profitability
        gross_margins = _safe_div(gross_profit, revenue) if gross_profit and revenue else None
        operating_margins = _safe_div(operating_income, revenue) if operating_income and revenue else None
        profit_margins = _safe_div(net_income, revenue) if net_income and revenue else None
        roe = _safe_div(net_income, equity) if net_income and equity else None
        roa = _safe_div(net_income, total_assets) if net_income and total_assets else None

        # Financial health
        total_debt = long_term_debt or 0.0
        debt_to_equity_raw = _safe_div(total_debt, equity) if total_liabilities and equity else None
        current_ratio = (
            _safe_div(current_assets, current_liabilities) if current_assets and current_liabilities else None
        )

        # Cash flow
        fcf = (operating_cf - (capex or 0.0)) if operating_cf is not None else None
        fcf_yield = _safe_div(fcf, market_cap) if fcf is not None and market_cap else None

        # Growth (TTM vs prior year TTM)
        revenue_growth = self._compute_yoy_growth(snapshots, "revenue", as_of_date)
        earnings_growth = self._compute_yoy_growth(snapshots, "eps_diluted", as_of_date)

        # PEG ratio = P/E / (earnings growth % × 100)
        peg_ratio = None
        if pe_ratio is not None and earnings_growth is not None and earnings_growth > 0:
            peg_ratio = pe_ratio / (earnings_growth * 100)

        # Dividend yield
        dividend_yield = _safe_div(dividends_ps, current_price) if dividends_ps and current_price else None

        # Days since last filing
        days_since_earnings = self._days_since_last_filing(snapshots, as_of_date)

        # Gross margin declining quarters (for GrossMarginTrendFilter)
        gross_margin_declining_quarters = self._compute_gross_margin_declining_quarters(
            snapshots, as_of_date,
        )

        # Earnings surprise % (for EarningsSurpriseFilter)
        latest_eps, prior_eps, _ = self.get_latest_eps(symbol, as_of_date)
        earnings_surprise_pct: float | None = None
        if latest_eps is not None and prior_eps is not None and prior_eps != 0:
            earnings_surprise_pct = (latest_eps - prior_eps) / abs(prior_eps)

        # --- Build output dict matching YahooFinanceAdapter schema ---
        result = {
            "symbol": symbol,
            "report_period": as_of_date.isoformat(),
            "period": "ttm",
            "currency": "USD",
            # Snake_case for LLM agents
            "market_cap": market_cap,
            "pe_ratio": pe_ratio,
            "price_to_book": price_to_book,
            "price_to_sales": price_to_sales,
            "revenue": revenue,
            "revenue_growth": revenue_growth,
            "gross_margins": gross_margins,
            "operating_margins": operating_margins,
            "profit_margins": profit_margins,
            "earnings_growth": earnings_growth,
            "return_on_equity": roe,
            "return_on_assets": roa,
            "debt_to_equity": debt_to_equity_raw,
            "current_ratio": current_ratio,
            "free_cash_flow": fcf,
            "operating_cash_flow": operating_cf,
            "dividend_yield": dividend_yield,
            "52_week_high": price_metrics.get("52_week_high"),
            "52_week_low": price_metrics.get("52_week_low"),
            # CamelCase for screener filters
            "marketCap": market_cap,
            "averageDailyVolume10Day": price_metrics.get("avg_volume_10d"),
            "debtToEquity": debt_to_equity_raw,
            "peRatio": pe_ratio,
            "pbRatio": price_to_book,
            "returnOnEquity": roe,
            "pegRatio": peg_ratio,
            "dividendYield": dividend_yield,
            "revenueGrowthYoy": revenue_growth,
            "earningsGrowthYoy": earnings_growth,
            "currentPrice": current_price,
            "fiftyTwoWeekHigh": price_metrics.get("52_week_high"),
            "fiftyTwoWeekLow": price_metrics.get("52_week_low"),
            # Derived fields
            "fcfYield": fcf_yield,
            "volatility90d": price_metrics.get("volatility_90d"),
            "return6m": price_metrics.get("return_6m"),
            "daysSinceLastEarnings": days_since_earnings,
            "grossMarginDecliningQuarters": gross_margin_declining_quarters,
            "lastEarningsSurprisePct": earnings_surprise_pct,
        }

        # Remove None values to match YahooFinanceAdapter behavior
        return [{k: v for k, v in result.items() if v is not None}]

    def get_company_info(self, symbol: str) -> dict:
        """Get basic company info from SEC EDGAR data."""
        return self._company_info.get(symbol, {})

    def get_latest_eps(self, symbol: str, as_of_date: date) -> tuple[float | None, float | None, date | None]:
        """Get the latest and prior-year EPS for earnings surprise calculation.

        Returns:
            (latest_eps, prior_year_same_quarter_eps, filing_date) or (None, None, None)
        """
        snapshots = self._snapshots.get(symbol)
        if not snapshots:
            return None, None, None

        # Find the most recent quarterly filing
        available = [s for s in snapshots if s.filed_date <= as_of_date and "eps_diluted" in s.values]
        if not available:
            return None, None, None

        latest = available[-1]
        latest_eps = latest.values.get("eps_diluted")

        # Find same-quarter prior year
        target_period_end = latest.period_end - timedelta(days=365)
        prior_eps: float | None = None
        for snap in available:
            # Match within ~30 days of the same quarter last year
            if abs((snap.period_end - target_period_end).days) < 45 and "eps_diluted" in snap.values:
                prior_eps = snap.values["eps_diluted"]

        return latest_eps, prior_eps, latest.filed_date

    def _compute_price_derived_metrics(self, symbol: str, as_of_date: date) -> dict:
        """Compute metrics from historical OHLCV data."""
        result: dict[str, float | None] = {}

        # 10-day average volume
        bars_10d = self._price_store.get_bars(symbol, as_of_date - timedelta(days=20), as_of_date)
        if bars_10d:
            recent_bars = bars_10d[-10:]
            result["avg_volume_10d"] = sum(b.volume for b in recent_bars) / len(recent_bars)

        # 252-day lookback for 52-week range and volatility
        bars_252 = self._price_store.get_bars(symbol, as_of_date - timedelta(days=380), as_of_date)
        if bars_252:
            closes = [b.close for b in bars_252]
            result["52_week_high"] = max(closes[-252:]) if len(closes) >= 252 else max(closes)
            result["52_week_low"] = min(closes[-252:]) if len(closes) >= 252 else min(closes)

            # 90-day volatility
            recent_closes = closes[-90:] if len(closes) >= 90 else closes
            if len(recent_closes) >= 2:
                daily_returns = [
                    (recent_closes[i] - recent_closes[i - 1]) / recent_closes[i - 1]
                    for i in range(1, len(recent_closes))
                ]
                if daily_returns:
                    mean_r = sum(daily_returns) / len(daily_returns)
                    variance = sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns)
                    result["volatility_90d"] = math.sqrt(variance) * math.sqrt(252)

            # 6-month return
            if len(closes) >= 126:
                result["return_6m"] = (closes[-1] - closes[-126]) / closes[-126]
            elif len(closes) >= 2:
                result["return_6m"] = (closes[-1] - closes[0]) / closes[0]

        return result

    def _compute_yoy_growth(
        self,
        snapshots: list[QuarterlySnapshot],
        metric_key: str,
        as_of_date: date,
    ) -> float | None:
        """Compute year-over-year growth for a TTM metric."""
        current_ttm = compute_ttm(snapshots, metric_key, as_of_date)
        if current_ttm is None:
            return None

        # Prior year: use data available 1 year ago
        prior_date = as_of_date - timedelta(days=365)
        prior_ttm = compute_ttm(snapshots, metric_key, prior_date)
        if prior_ttm is None or prior_ttm == 0:
            return None

        return (current_ttm - prior_ttm) / abs(prior_ttm)

    def _days_since_last_filing(
        self,
        snapshots: list[QuarterlySnapshot],
        as_of_date: date,
    ) -> int | None:
        """Days since the most recent SEC filing."""
        for snap in reversed(snapshots):
            if snap.filed_date <= as_of_date:
                return (as_of_date - snap.filed_date).days
        return None

    @staticmethod
    def _compute_gross_margin_declining_quarters(
        snapshots: list[QuarterlySnapshot],
        as_of_date: date,
    ) -> int:
        """Count consecutive most-recent quarters of declining gross margin.

        Walks backward from the most recent available quarter. A quarter
        counts as "declining" if its gross margin is lower than the
        previous quarter's. Stops at the first non-decline.
        """
        available = [
            s for s in snapshots
            if s.filed_date <= as_of_date
            and "gross_profit" in s.values
            and "revenue" in s.values
            and s.values["revenue"] not in (0, None)
        ]
        if len(available) < 2:
            return 0

        margins = [s.values["gross_profit"] / s.values["revenue"] for s in available]
        declining = 0
        for i in range(len(margins) - 1, 0, -1):
            if margins[i] < margins[i - 1]:
                declining += 1
            else:
                break
        return declining


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    """Safe division returning None on zero/None/NaN."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    result = numerator / denominator
    if math.isnan(result) or math.isinf(result):
        return None
    return result

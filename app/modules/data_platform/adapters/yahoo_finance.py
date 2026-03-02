"""Yahoo Finance adapter — primary data source for Phase 1."""

import asyncio
from datetime import UTC, date, datetime

import yfinance as yf

from app.common.interfaces.fundamentals import FundamentalsAdapter
from app.common.interfaces.news import NewsAdapter, NewsArticle
from app.common.interfaces.price_data import PriceBar, PriceDataAdapter


class YahooFinanceAdapter(PriceDataAdapter, FundamentalsAdapter, NewsAdapter):
    """
    Unified adapter for Yahoo Finance via the yfinance library.
    Implements price, fundamentals, and news interfaces.

    Note: yfinance is synchronous, so we run calls in a thread executor.
    """

    name = "yahoo_finance"

    async def get_prices(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        interval: str = "1d",
    ) -> list[PriceBar]:
        def _fetch():
            ticker = yf.Ticker(symbol)
            df = ticker.history(
                start=start_date.isoformat(),
                end=end_date.isoformat(),
                interval=interval,
            )
            if df.empty:
                return []

            bars = []
            for idx, row in df.iterrows():
                bars.append(
                    PriceBar(
                        timestamp=idx.date() if hasattr(idx, "date") else idx,
                        open=round(row["Open"], 4),
                        high=round(row["High"], 4),
                        low=round(row["Low"], 4),
                        close=round(row["Close"], 4),
                        volume=int(row["Volume"]),
                    )
                )
            return bars

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _fetch)

    async def get_current_price(self, symbol: str) -> float | None:
        """Get the latest available price for a symbol."""

        def _fetch():
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            return float(info.last_price) if hasattr(info, "last_price") and info.last_price else None

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _fetch)

    def supported_asset_classes(self) -> list[str]:
        return ["equity", "crypto", "commodity", "futures"]

    # --- Fundamentals ---

    async def get_metrics(
        self,
        symbol: str,
        end_date: date,
        period: str = "ttm",
        limit: int = 10,
    ) -> list[dict]:
        def _fetch():
            ticker = yf.Ticker(symbol)
            info = ticker.info
            if not info:
                return []

            # Extract key financial metrics from yfinance info dict
            # Includes both snake_case (for LLM agents) and camelCase (for screener filters)
            metrics = {
                "symbol": symbol,
                "report_period": end_date.isoformat(),
                "period": period,
                "currency": info.get("currency", "USD"),
                # --- Core fundamentals (snake_case for agents) ---
                "market_cap": info.get("marketCap"),
                "enterprise_value": info.get("enterpriseValue"),
                "pe_ratio": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "peg_ratio": info.get("pegRatio", info.get("trailingPegRatio")),
                "price_to_book": info.get("priceToBook"),
                "price_to_sales": info.get("priceToSalesTrailing12Months"),
                "revenue": info.get("totalRevenue"),
                "revenue_growth": info.get("revenueGrowth"),
                "gross_margins": info.get("grossMargins"),
                "operating_margins": info.get("operatingMargins"),
                "profit_margins": info.get("profitMargins"),
                "earnings_growth": info.get("earningsGrowth"),
                "return_on_equity": info.get("returnOnEquity"),
                "return_on_assets": info.get("returnOnAssets"),
                "debt_to_equity": info.get("debtToEquity", 0) / 100.0 if info.get("debtToEquity") is not None else None,
                "current_ratio": info.get("currentRatio"),
                "free_cash_flow": info.get("freeCashflow"),
                "operating_cash_flow": info.get("operatingCashflow"),
                "dividend_yield": info.get("dividendYield"),
                "beta": info.get("beta"),
                "52_week_high": info.get("fiftyTwoWeekHigh"),
                "52_week_low": info.get("fiftyTwoWeekLow"),
                # --- Screener keys (camelCase, matching filter lookups) ---
                "marketCap": info.get("marketCap"),
                "averageDailyVolume10Day": info.get("averageDailyVolume10Day", info.get("averageVolume10days")),
                "debtToEquity": info.get("debtToEquity", 0) / 100.0 if info.get("debtToEquity") is not None else None,
                "pegRatio": info.get("pegRatio", info.get("trailingPegRatio")),
                "peRatio": info.get("trailingPE"),
                "pbRatio": info.get("priceToBook"),
                "returnOnEquity": info.get("returnOnEquity"),
                "dividendYield": info.get("dividendYield"),
                "revenueGrowthYoy": info.get("revenueGrowth"),
                "earningsGrowthYoy": info.get("earningsGrowth"),
                "currentPrice": info.get("currentPrice", info.get("regularMarketPrice")),
                "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
                "fiftyTwoWeekLow": info.get("fiftyTwoWeekLow"),
            }

            # Derived: daysSinceLastEarnings from earningsTimestamp
            earnings_ts = info.get("earningsTimestamp")
            if earnings_ts:
                try:
                    earnings_dt = datetime.fromtimestamp(earnings_ts, tz=UTC)
                    metrics["daysSinceLastEarnings"] = (datetime.now(UTC) - earnings_dt).days
                except (OSError, ValueError):
                    pass

            # Derived: fcfYield = freeCashFlow / marketCap
            fcf = info.get("freeCashflow")
            mcap = info.get("marketCap")
            if fcf is not None and mcap and mcap > 0:
                metrics["fcfYield"] = fcf / mcap

            # Derived: lastEarningsSurprisePct from earningsQuarterlyGrowth (best proxy)
            surprise = info.get("earningsQuarterlyGrowth")
            if surprise is not None:
                metrics["lastEarningsSurprisePct"] = surprise

            # Derived: return6m approximation from 200-day average vs current price
            current = info.get("currentPrice", info.get("regularMarketPrice"))
            avg_200d = info.get("twoHundredDayAverage")
            if current and avg_200d and avg_200d > 0:
                metrics["return6m"] = (current - avg_200d) / avg_200d

            # Derived: volatility90d from beta (scale beta to annualized vol estimate)
            # Market vol ~15-20%; stock vol ≈ beta * market_vol
            beta_val = info.get("beta")
            if beta_val is not None:
                metrics["volatility90d"] = abs(beta_val) * 0.18

            # Remove None values
            return [{k: v for k, v in metrics.items() if v is not None}]

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _fetch)

    async def search_line_items(
        self,
        symbol: str,
        items: list[str],
        end_date: date,
        period: str = "ttm",
        limit: int = 10,
    ) -> list[dict]:
        def _fetch():
            ticker = yf.Ticker(symbol)
            results = []

            # Map common line items to yfinance financials
            financials = ticker.financials
            balance_sheet = ticker.balance_sheet
            cashflow = ticker.cashflow

            for item in items:
                item_lower = item.lower().replace("_", " ")
                found = False

                for df, source_name in [
                    (financials, "income_statement"),
                    (balance_sheet, "balance_sheet"),
                    (cashflow, "cash_flow"),
                ]:
                    if df is not None and not df.empty:
                        for row_label in df.index:
                            if item_lower in str(row_label).lower():
                                for col in df.columns[:limit]:
                                    results.append(
                                        {
                                            "item": str(row_label),
                                            "source": source_name,
                                            "period": col.isoformat() if hasattr(col, "isoformat") else str(col),
                                            "value": float(df.loc[row_label, col])
                                            if df.loc[row_label, col] == df.loc[row_label, col]
                                            else None,
                                        }
                                    )
                                found = True
                                break
                    if found:
                        break

            return results

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _fetch)

    async def get_company_facts(self, symbol: str) -> dict:
        def _fetch():
            ticker = yf.Ticker(symbol)
            info = ticker.info
            return {
                "symbol": symbol,
                "name": info.get("longName") or info.get("shortName", symbol),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "country": info.get("country"),
                "website": info.get("website"),
                "description": info.get("longBusinessSummary"),
                "employees": info.get("fullTimeEmployees"),
                "exchange": info.get("exchange"),
                "currency": info.get("currency", "USD"),
            }

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _fetch)

    # --- News ---

    async def get_news(
        self,
        symbols: list[str] | None = None,
        query: str | None = None,
        since: date | None = None,
        limit: int = 100,
    ) -> list[NewsArticle]:
        def _fetch():
            articles = []
            target_symbols = symbols or []
            if not target_symbols and query:
                target_symbols = [query]

            for symbol in target_symbols[:5]:  # Limit to avoid rate limiting
                ticker = yf.Ticker(symbol)
                news = ticker.news or []
                for item in news[:limit]:
                    content = item.get("content", {})
                    pub_date = content.get("pubDate")
                    if pub_date:
                        try:
                            published = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                        except (ValueError, TypeError):
                            published = datetime.now(UTC)
                    else:
                        published = datetime.now(UTC)

                    if since and published.date() < since:
                        continue

                    articles.append(
                        NewsArticle(
                            title=content.get("title", ""),
                            author=content.get("provider", {}).get("displayName"),
                            source="yahoo_finance",
                            published_at=published,
                            url=content.get("canonicalUrl", {}).get("url", ""),
                            symbols=[symbol],
                        )
                    )

            return articles[:limit]

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _fetch)

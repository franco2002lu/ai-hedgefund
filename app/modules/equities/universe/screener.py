from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from app.modules.data_platform.service import DataPlatformService
from app.modules.equities.models import UniverseStock

logger = logging.getLogger(__name__)


class ScreeningFilter(ABC):
    """Base class for all screening filters."""

    @abstractmethod
    async def apply(
        self,
        stocks: list[UniverseStock],
        data_service: DataPlatformService,
    ) -> list[UniverseStock]: ...

    @property
    @abstractmethod
    def name(self) -> str: ...


class Screener:
    """Runs a configurable pipeline of filters sequentially."""

    def __init__(self, filters: list[ScreeningFilter]) -> None:
        self.filters = filters

    async def screen(
        self,
        stocks: list[UniverseStock],
        data_service: DataPlatformService,
    ) -> list[UniverseStock]:
        remaining = stocks
        for f in self.filters:
            before = len(remaining)
            remaining = await f.apply(remaining, data_service)
            logger.info("%s: %d -> %d", f.name, before, len(remaining))
        return remaining


async def _get_metric(data_service: DataPlatformService, symbol: str, key: str, default=None):
    try:
        result = await data_service.get_metrics(symbol)
        metrics = result.get("metrics", [])
        if metrics and isinstance(metrics[0], dict):
            return metrics[0].get(key, default)
        return default
    except Exception:
        return default


# --- Shared Filters ---


class LiquidityFilter(ScreeningFilter):
    def __init__(self, min_avg_daily_volume: int = 500_000) -> None:
        self.min_avg_daily_volume = min_avg_daily_volume

    @property
    def name(self) -> str:
        return "LiquidityFilter"

    async def apply(self, stocks: list[UniverseStock], data_service: DataPlatformService) -> list[UniverseStock]:
        result = []
        for stock in stocks:
            vol = await _get_metric(data_service, stock.symbol, "averageDailyVolume10Day", 0)
            if vol >= self.min_avg_daily_volume:
                result.append(stock)
        return result


class MarketCapFilter(ScreeningFilter):
    def __init__(self, min_market_cap: float = 2_000_000_000) -> None:
        self.min_market_cap = min_market_cap

    @property
    def name(self) -> str:
        return "MarketCapFilter"

    async def apply(self, stocks: list[UniverseStock], data_service: DataPlatformService) -> list[UniverseStock]:
        result = []
        for stock in stocks:
            cap = await _get_metric(data_service, stock.symbol, "marketCap", 0)
            if cap >= self.min_market_cap:
                result.append(stock)
        return result


class EarningsRecencyFilter(ScreeningFilter):
    def __init__(self, max_days_since_earnings: int = 120) -> None:
        self.max_days_since_earnings = max_days_since_earnings

    @property
    def name(self) -> str:
        return "EarningsRecencyFilter"

    async def apply(self, stocks: list[UniverseStock], data_service: DataPlatformService) -> list[UniverseStock]:
        result = []
        for stock in stocks:
            days = await _get_metric(data_service, stock.symbol, "daysSinceLastEarnings")
            if days is not None and days <= self.max_days_since_earnings:
                result.append(stock)
        return result


class VolatilityFilter(ScreeningFilter):
    def __init__(self, max_volatility_percentile: float = 95.0) -> None:
        self.max_volatility_percentile = max_volatility_percentile

    @property
    def name(self) -> str:
        return "VolatilityFilter"

    async def apply(self, stocks: list[UniverseStock], data_service: DataPlatformService) -> list[UniverseStock]:
        if not stocks:
            return []
        vols = []
        for stock in stocks:
            vol = await _get_metric(data_service, stock.symbol, "volatility90d", 0.0)
            vols.append((stock, vol))
        sorted_vols = sorted([v for _, v in vols])
        n = len(sorted_vols)
        keep_count = max(1, int(n * self.max_volatility_percentile / 100.0))
        if keep_count >= n:
            return [stock for stock, _ in vols]
        cutoff_val = sorted_vols[keep_count - 1]
        return [stock for stock, vol in vols if vol <= cutoff_val]


class LeverageFilter(ScreeningFilter):
    def __init__(self, max_debt_to_equity: float = 5.0) -> None:
        self.max_debt_to_equity = max_debt_to_equity

    @property
    def name(self) -> str:
        return "LeverageFilter"

    async def apply(self, stocks: list[UniverseStock], data_service: DataPlatformService) -> list[UniverseStock]:
        result = []
        for stock in stocks:
            dte = await _get_metric(data_service, stock.symbol, "debtToEquity", None)
            if dte is not None and dte <= self.max_debt_to_equity:
                result.append(stock)
        return result


# --- Growth-Specific Filters ---


class RevenueGrowthFilter(ScreeningFilter):
    def __init__(self, min_revenue_growth_yoy: float = 0.05) -> None:
        self.min_revenue_growth_yoy = min_revenue_growth_yoy

    @property
    def name(self) -> str:
        return "RevenueGrowthFilter"

    async def apply(self, stocks: list[UniverseStock], data_service: DataPlatformService) -> list[UniverseStock]:
        result = []
        for stock in stocks:
            growth = await _get_metric(data_service, stock.symbol, "revenueGrowthYoy", 0.0)
            if growth >= self.min_revenue_growth_yoy:
                result.append(stock)
        return result


class EarningsGrowthFilter(ScreeningFilter):
    def __init__(self, min_earnings_growth_yoy: float = 0.0) -> None:
        self.min_earnings_growth_yoy = min_earnings_growth_yoy

    @property
    def name(self) -> str:
        return "EarningsGrowthFilter"

    async def apply(self, stocks: list[UniverseStock], data_service: DataPlatformService) -> list[UniverseStock]:
        result = []
        for stock in stocks:
            growth = await _get_metric(data_service, stock.symbol, "earningsGrowthYoy", 0.0)
            if growth >= self.min_earnings_growth_yoy:
                result.append(stock)
        return result


class GrossMarginTrendFilter(ScreeningFilter):
    def __init__(self, margin_declining_quarters: int = 2) -> None:
        self.margin_declining_quarters = margin_declining_quarters

    @property
    def name(self) -> str:
        return "GrossMarginTrendFilter"

    async def apply(self, stocks: list[UniverseStock], data_service: DataPlatformService) -> list[UniverseStock]:
        result = []
        for stock in stocks:
            quarters = await _get_metric(data_service, stock.symbol, "grossMarginDecliningQuarters", 0)
            if quarters < self.margin_declining_quarters:
                result.append(stock)
        return result


class EarningsSurpriseFilter(ScreeningFilter):
    def __init__(self, min_surprise_pct: float = -0.05) -> None:
        self.min_surprise_pct = min_surprise_pct

    @property
    def name(self) -> str:
        return "EarningsSurpriseFilter"

    async def apply(self, stocks: list[UniverseStock], data_service: DataPlatformService) -> list[UniverseStock]:
        result = []
        for stock in stocks:
            surprise = await _get_metric(data_service, stock.symbol, "lastEarningsSurprisePct", 0.0)
            if surprise >= self.min_surprise_pct:
                result.append(stock)
        return result


class MomentumFilter(ScreeningFilter):
    def __init__(self, min_return_6m: float = -0.10) -> None:
        self.min_return_6m = min_return_6m

    @property
    def name(self) -> str:
        return "MomentumFilter"

    async def apply(self, stocks: list[UniverseStock], data_service: DataPlatformService) -> list[UniverseStock]:
        result = []
        for stock in stocks:
            ret = await _get_metric(data_service, stock.symbol, "return6m", 0.0)
            if ret >= self.min_return_6m:
                result.append(stock)
        return result


class PEGFilter(ScreeningFilter):
    def __init__(self, max_peg_ratio: float = 3.0) -> None:
        self.max_peg_ratio = max_peg_ratio

    @property
    def name(self) -> str:
        return "PEGFilter"

    async def apply(self, stocks: list[UniverseStock], data_service: DataPlatformService) -> list[UniverseStock]:
        result = []
        for stock in stocks:
            peg = await _get_metric(data_service, stock.symbol, "pegRatio", None)
            if peg is not None and peg >= 0 and peg <= self.max_peg_ratio:
                result.append(stock)
        return result


# --- Value-Specific Filters ---


class PEFilter(ScreeningFilter):
    def __init__(self, max_pe_percentile: float = 60.0) -> None:
        self.max_pe_percentile = max_pe_percentile

    @property
    def name(self) -> str:
        return "PEFilter"

    async def apply(self, stocks: list[UniverseStock], data_service: DataPlatformService) -> list[UniverseStock]:
        if not stocks:
            return []
        pe_values = []
        for stock in stocks:
            pe = await _get_metric(data_service, stock.symbol, "peRatio", None)
            pe_values.append((stock, pe))
        valid = [(s, pe) for s, pe in pe_values if pe is not None]
        if not valid:
            return []
        sorted_pe = sorted([pe for _, pe in valid])
        cutoff_idx = int(len(sorted_pe) * self.max_pe_percentile / 100.0)
        cutoff_val = sorted_pe[min(cutoff_idx, len(sorted_pe) - 1)]
        return [s for s, pe in valid if pe <= cutoff_val]


class PBFilter(ScreeningFilter):
    def __init__(self, max_pb_percentile: float = 60.0) -> None:
        self.max_pb_percentile = max_pb_percentile

    @property
    def name(self) -> str:
        return "PBFilter"

    async def apply(self, stocks: list[UniverseStock], data_service: DataPlatformService) -> list[UniverseStock]:
        if not stocks:
            return []
        pb_values = []
        for stock in stocks:
            pb = await _get_metric(data_service, stock.symbol, "pbRatio", None)
            pb_values.append((stock, pb))
        valid = [(s, pb) for s, pb in pb_values if pb is not None]
        if not valid:
            return []
        sorted_pb = sorted([pb for _, pb in valid])
        cutoff_idx = int(len(sorted_pb) * self.max_pb_percentile / 100.0)
        cutoff_val = sorted_pb[min(cutoff_idx, len(sorted_pb) - 1)]
        return [s for s, pb in valid if pb <= cutoff_val]


class FCFYieldFilter(ScreeningFilter):
    def __init__(self, min_fcf_yield: float = 0.02) -> None:
        self.min_fcf_yield = min_fcf_yield

    @property
    def name(self) -> str:
        return "FCFYieldFilter"

    async def apply(self, stocks: list[UniverseStock], data_service: DataPlatformService) -> list[UniverseStock]:
        result = []
        for stock in stocks:
            fcf = await _get_metric(data_service, stock.symbol, "fcfYield", 0.0)
            if fcf >= self.min_fcf_yield:
                result.append(stock)
        return result


class DividendYieldFilter(ScreeningFilter):
    def __init__(self, min_dividend_yield: float = 0.005) -> None:
        self.min_dividend_yield = min_dividend_yield

    @property
    def name(self) -> str:
        return "DividendYieldFilter"

    async def apply(self, stocks: list[UniverseStock], data_service: DataPlatformService) -> list[UniverseStock]:
        result = []
        for stock in stocks:
            dy = await _get_metric(data_service, stock.symbol, "dividendYield", 0.0)
            if dy >= self.min_dividend_yield:
                result.append(stock)
        return result


class ROEFilter(ScreeningFilter):
    def __init__(self, min_roe: float = 0.08) -> None:
        self.min_roe = min_roe

    @property
    def name(self) -> str:
        return "ROEFilter"

    async def apply(self, stocks: list[UniverseStock], data_service: DataPlatformService) -> list[UniverseStock]:
        result = []
        for stock in stocks:
            roe = await _get_metric(data_service, stock.symbol, "returnOnEquity", 0.0)
            if roe >= self.min_roe:
                result.append(stock)
        return result


class PriceRangeFilter(ScreeningFilter):
    def __init__(self, max_52w_range_percentile: float = 70.0) -> None:
        self.max_52w_range_percentile = max_52w_range_percentile

    @property
    def name(self) -> str:
        return "PriceRangeFilter"

    async def apply(self, stocks: list[UniverseStock], data_service: DataPlatformService) -> list[UniverseStock]:
        result = []
        for stock in stocks:
            try:
                data = await data_service.get_metrics(stock.symbol)
                metrics = data.get("metrics", [{}])
                m = metrics[0] if metrics else {}
                high = m.get("fiftyTwoWeekHigh")
                low = m.get("fiftyTwoWeekLow")
                current = m.get("currentPrice")
                if high is None or low is None or current is None or high == low:
                    continue
                pct = (current - low) / (high - low) * 100.0
                if pct <= self.max_52w_range_percentile:
                    result.append(stock)
            except Exception:
                continue
        return result

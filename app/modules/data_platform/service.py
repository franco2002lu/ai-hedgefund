"""Data Platform service — routes requests to adapters with caching and rate limiting."""

from datetime import date

from app.modules.data_platform.cache import DataCache
from app.modules.data_platform.rate_limiter import RateLimiter


class DataUnavailableError(Exception):
    """Raised when no adapter can serve the requested data."""

    pass


class DataPlatformService:
    """
    Unified data access layer. Routes requests to the appropriate adapter,
    with in-memory caching and rate limiting.
    """

    def __init__(self, adapter_registry: dict, cache: DataCache | None = None, rate_limiter: RateLimiter | None = None):
        self.registry = adapter_registry
        self.cache = cache or DataCache()
        self.rate_limiter = rate_limiter or RateLimiter()

    async def get_prices(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        interval: str = "1d",
        asset_class: str = "equity",
    ) -> dict:
        cache_key = f"{symbol}:{start_date}:{end_date}:{interval}"
        cached = self.cache.get("prices", cache_key)
        if cached is not None:
            return cached

        adapters = self.registry.get("prices", {}).get(asset_class, [])
        if not adapters:
            adapters = self.registry.get("prices", {}).get("all", [])

        for adapter in adapters:
            try:
                await self.rate_limiter.acquire(adapter.name)
                bars = await adapter.get_prices(symbol, start_date, end_date, interval)
                result = {
                    "symbol": symbol,
                    "interval": interval,
                    "bars": [b.model_dump() for b in bars],
                    "source": adapter.name,
                }
                self.cache.set("prices", cache_key, result)
                return result
            except Exception:
                continue

        raise DataUnavailableError(f"No adapter could serve prices for {symbol}")

    async def get_current_price(self, symbol: str) -> float | None:
        """Get latest price — used by paper trading adapter."""
        cache_key = f"current:{symbol}"
        cached = self.cache.get("prices", cache_key)
        if cached is not None:
            return cached

        # Try all price adapters
        for asset_class_adapters in self.registry.get("prices", {}).values():
            for adapter in asset_class_adapters:
                try:
                    if hasattr(adapter, "get_current_price"):
                        await self.rate_limiter.acquire(adapter.name)
                        price = await adapter.get_current_price(symbol)
                        if price is not None:
                            self.cache.set("prices", cache_key, price)
                            return price
                except Exception:
                    continue
        return None

    async def get_metrics(
        self,
        symbol: str,
        end_date: date | None = None,
        period: str = "ttm",
        limit: int = 10,
    ) -> dict:
        end = end_date or date.today()
        cache_key = f"{symbol}:{end}:{period}:{limit}"
        cached = self.cache.get("fundamentals", cache_key)
        if cached is not None:
            return cached

        adapters = self.registry.get("fundamentals", {}).get("equity", [])
        for adapter in adapters:
            try:
                await self.rate_limiter.acquire(adapter.name)
                metrics = await adapter.get_metrics(symbol, end, period, limit)
                result = {"symbol": symbol, "metrics": metrics, "source": adapter.name}
                self.cache.set("fundamentals", cache_key, result)
                return result
            except Exception:
                continue

        raise DataUnavailableError(f"No adapter could serve metrics for {symbol}")

    async def get_line_items(
        self,
        symbol: str,
        items: list[str],
        end_date: date | None = None,
        period: str = "ttm",
        limit: int = 10,
    ) -> dict:
        end = end_date or date.today()
        cache_key = f"{symbol}:{','.join(items)}:{end}:{period}"
        cached = self.cache.get("fundamentals", cache_key)
        if cached is not None:
            return cached

        adapters = self.registry.get("fundamentals", {}).get("equity", [])
        for adapter in adapters:
            try:
                await self.rate_limiter.acquire(adapter.name)
                line_items = await adapter.search_line_items(symbol, items, end, period, limit)
                result = {"symbol": symbol, "items": line_items, "source": adapter.name}
                self.cache.set("fundamentals", cache_key, result)
                return result
            except Exception:
                continue

        raise DataUnavailableError(f"No adapter could serve line items for {symbol}")

    async def get_company_facts(self, symbol: str) -> dict:
        cache_key = f"facts:{symbol}"
        cached = self.cache.get("fundamentals", cache_key)
        if cached is not None:
            return cached

        adapters = self.registry.get("fundamentals", {}).get("equity", [])
        for adapter in adapters:
            try:
                await self.rate_limiter.acquire(adapter.name)
                facts = await adapter.get_company_facts(symbol)
                self.cache.set("fundamentals", cache_key, facts)
                return facts
            except Exception:
                continue

        raise DataUnavailableError(f"No adapter could serve company facts for {symbol}")

    async def get_news(
        self,
        symbols: list[str] | None = None,
        since: date | None = None,
        limit: int = 100,
    ) -> dict:
        syms_key = ",".join(symbols) if symbols else "all"
        cache_key = f"news:{syms_key}:{since}:{limit}"
        cached = self.cache.get("news", cache_key)
        if cached is not None:
            return cached

        adapters = self.registry.get("news", {}).get("all", [])
        for adapter in adapters:
            try:
                await self.rate_limiter.acquire(adapter.name)
                articles = await adapter.get_news(symbols=symbols, since=since, limit=limit)
                result = {
                    "articles": [a.model_dump(mode="json") for a in articles],
                    "source": adapter.name,
                }
                self.cache.set("news", cache_key, result)
                return result
            except Exception:
                continue

        raise DataUnavailableError("No adapter could serve news")

    @staticmethod
    def _instrument_to_dict(instrument) -> dict:
        return {
            "instrument_id": str(instrument.id),
            "symbol": instrument.symbol,
            "name": instrument.name,
            "asset_class": instrument.asset_class,
            "exchange": instrument.exchange,
            "currency": instrument.currency,
            "sector": instrument.sector,
            "industry": instrument.industry,
            "country": instrument.country,
            "is_active": instrument.is_active,
            "metadata": instrument.metadata_,
            "created_at": instrument.created_at.isoformat(),
        }

    async def upsert_instrument(self, session, instrument_data: dict) -> dict:
        """Create or update an instrument in the database."""
        from sqlalchemy import select

        from app.db.models import InstrumentModel

        stmt = select(InstrumentModel).where(
            InstrumentModel.symbol == instrument_data["symbol"],
            InstrumentModel.asset_class == instrument_data["asset_class"],
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            for key, value in instrument_data.items():
                if key == "metadata":
                    existing.metadata_ = value
                elif hasattr(existing, key):
                    setattr(existing, key, value)
            await session.flush()
            return self._instrument_to_dict(existing)
        else:
            instrument = InstrumentModel(
                symbol=instrument_data["symbol"],
                name=instrument_data["name"],
                asset_class=instrument_data["asset_class"],
                exchange=instrument_data.get("exchange"),
                currency=instrument_data.get("currency", "USD"),
                sector=instrument_data.get("sector"),
                industry=instrument_data.get("industry"),
                country=instrument_data.get("country"),
                is_active=instrument_data.get("is_active", True),
                metadata_=instrument_data.get("metadata"),
            )
            session.add(instrument)
            await session.flush()
            return self._instrument_to_dict(instrument)

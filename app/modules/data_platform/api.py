"""Data Platform API routes."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import get_session
from app.dependencies import get_data_platform_service
from app.modules.data_platform.service import DataPlatformService, DataUnavailableError

router = APIRouter(prefix="/api/v1", tags=["data-platform"])


# --- Request/Response schemas ---


class UpsertInstrumentRequest(BaseModel):
    symbol: str
    name: str
    asset_class: str
    exchange: str | None = None
    currency: str = "USD"
    is_active: bool = True
    metadata: dict | None = None


# --- Routes ---


@router.get("/prices/{symbol}")
async def get_prices(
    symbol: str,
    start_date: date | None = None,
    end_date: date | None = None,
    interval: str = "1d",
    asset_class: str = "equity",
    service: DataPlatformService = Depends(get_data_platform_service),
):
    start = start_date or date(2024, 1, 1)
    end = end_date or date.today()
    try:
        return await service.get_prices(symbol, start, end, interval, asset_class)
    except DataUnavailableError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/fundamentals/{symbol}/metrics")
async def get_metrics(
    symbol: str,
    end_date: date | None = None,
    period: str = "ttm",
    limit: int = 10,
    service: DataPlatformService = Depends(get_data_platform_service),
):
    try:
        return await service.get_metrics(symbol, end_date, period, limit)
    except DataUnavailableError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/fundamentals/{symbol}/line-items")
async def get_line_items(
    symbol: str,
    items: str = "net_income,revenue",
    end_date: date | None = None,
    period: str = "ttm",
    limit: int = 10,
    service: DataPlatformService = Depends(get_data_platform_service),
):
    item_list = [i.strip() for i in items.split(",")]
    try:
        return await service.get_line_items(symbol, item_list, end_date, period, limit)
    except DataUnavailableError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/fundamentals/{symbol}/facts")
async def get_company_facts(
    symbol: str,
    service: DataPlatformService = Depends(get_data_platform_service),
):
    try:
        return await service.get_company_facts(symbol)
    except DataUnavailableError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/news")
async def get_news(
    symbols: str | None = None,
    since: date | None = None,
    limit: int = 100,
    service: DataPlatformService = Depends(get_data_platform_service),
):
    sym_list = [s.strip() for s in symbols.split(",")] if symbols else None
    try:
        return await service.get_news(sym_list, since, limit)
    except DataUnavailableError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/instruments")
async def upsert_instrument(
    req: UpsertInstrumentRequest,
    session: AsyncSession = Depends(get_session),
    service: DataPlatformService = Depends(get_data_platform_service),
):
    return await service.upsert_instrument(session, req.model_dump())


@router.get("/instruments/{symbol}")
async def get_instrument(
    symbol: str,
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy import select
    from app.db.models import InstrumentModel

    stmt = select(InstrumentModel).where(InstrumentModel.symbol == symbol)
    result = await session.execute(stmt)
    instrument = result.scalar_one_or_none()
    if instrument is None:
        raise HTTPException(status_code=404, detail=f"Instrument {symbol} not found")
    return {
        "instrument_id": str(instrument.id),
        "symbol": instrument.symbol,
        "name": instrument.name,
        "asset_class": instrument.asset_class,
        "exchange": instrument.exchange,
        "currency": instrument.currency,
        "is_active": instrument.is_active,
        "metadata": instrument.metadata_,
        "created_at": instrument.created_at.isoformat(),
    }

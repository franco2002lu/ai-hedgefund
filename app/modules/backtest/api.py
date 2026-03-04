"""Backtest API endpoints."""

from __future__ import annotations

import logging
import time as _time
import uuid
from dataclasses import dataclass
from datetime import date

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.db.connection import async_session_factory
from app.modules.backtest.config import BacktestConfig
from app.modules.backtest.engine import BacktestEngine
from app.modules.backtest.models import BacktestResult
from app.modules.backtest.repository import PostgresBacktestRepository

router = APIRouter()
logger = logging.getLogger(__name__)


@dataclass
class BacktestRunRecord:
    """Unified record for a backtest run (pending → running → completed/failed/cancelled)."""

    backtest_id: str
    status: str
    config: BacktestConfig
    context: object | None = None
    progress: dict | None = None
    result: BacktestResult | None = None
    completed_at: float | None = None
    error_message: str | None = None


# Unified in-memory registry for all backtest runs.
_runs: dict[str, BacktestRunRecord] = {}


async def _persist_result(result: BacktestResult) -> None:
    """Persist a completed backtest result to the database (best-effort)."""
    async with async_session_factory() as session, session.begin():
        repo = PostgresBacktestRepository(session)
        await repo.save_result(result)


async def recover_stale_backtests() -> int:
    """Mark any DB rows stuck in 'running' as 'failed' (crash recovery).

    Called during app startup. Best-effort — returns 0 on any error.
    """
    try:
        async with async_session_factory() as session, session.begin():
            repo = PostgresBacktestRepository(session)
            return await repo.mark_stale_runs_failed()
    except Exception:
        logger.warning("Failed to recover stale backtests", exc_info=True)
        return 0


async def _run_backtest_background(backtest_id: str, config: BacktestConfig) -> None:
    """Background task that builds context, runs engine, and updates the record."""
    record = _runs.get(backtest_id)
    if record is None:
        return

    try:
        from app.modules.backtest.context import BacktestContext
        from app.modules.equities.config import EquitiesConfig

        ctx = await BacktestContext.build(config, EquitiesConfig())
        record.context = ctx  # Expose for cancellation

        # If cancelled during context build (race), propagate to the event
        if record.status == "cancelled":
            ctx.cancelled.set()

        engine = BacktestEngine()
        result = await engine._run_simulation(
            ctx,
            config,
            backtest_id=backtest_id,
            progress_callback=lambda p: setattr(record, "progress", p),
        )
        record.result = result
        record.status = result.status
        record.error_message = result.error_message

        # Persist to DB (best-effort — in-memory record is authoritative)
        try:
            await _persist_result(result)
        except Exception:
            logger.warning("Failed to persist backtest %s to DB", backtest_id, exc_info=True)
    except Exception as exc:
        logger.exception("Backtest %s failed", backtest_id)
        record.status = "failed"
        record.error_message = str(exc)
    finally:
        record.completed_at = _time.monotonic()


async def estimate_cost(config: dict) -> dict:
    """Estimate the cost of a backtest run."""
    start = date.fromisoformat(config.get("start_date", "2024-01-01"))
    end = date.fromisoformat(config.get("end_date", "2024-06-28"))
    frequency = config.get("rebalance_frequency", "weekly")
    use_llm = config.get("use_llm_agents", False)

    trading_days = max(0, (end - start).days * 5 // 7)

    step_map = {
        "daily": 1,
        "weekly": 5,
        "biweekly": 10,
        "monthly": 21,
    }
    step = step_map.get(frequency, 5)
    estimated_rebalances = max(1, trading_days // step) if trading_days > 0 else 0

    # LLM calls: 3 analysts per stock per rebalance (if using LLM agents)
    universe_size = 30  # approximate
    estimated_llm_calls = estimated_rebalances * universe_size * 3 if use_llm else 0
    estimated_cost_usd = estimated_llm_calls * 0.01  # ~$0.01 per LLM call

    # Duration estimate: quantitative ~5s/rebalance, LLM ~30s/rebalance
    per_rebalance = 30.0 if use_llm else 5.0
    estimated_duration_minutes = (estimated_rebalances * per_rebalance) / 60.0

    return {
        "estimated_rebalances": estimated_rebalances,
        "estimated_llm_calls": estimated_llm_calls,
        "estimated_cost_usd": round(estimated_cost_usd, 2),
        "estimated_duration_minutes": round(estimated_duration_minutes, 1),
    }


async def get_backtest_result(backtest_id: str) -> dict | None:
    """Retrieve a backtest run by ID from in-memory registry."""
    record = _runs.get(backtest_id)
    if record is None:
        return None

    response: dict = {
        "backtest_id": record.backtest_id,
        "status": record.status,
        "config": record.config.model_dump(mode="json"),
        "error_message": record.error_message,
    }

    # Include progress for running backtests
    if record.progress is not None:
        response["progress"] = record.progress

    # Include metrics/results for completed backtests
    if record.result is not None:
        response["rebalance_count"] = record.result.rebalance_count
        response["duration_seconds"] = record.result.duration_seconds
        if record.result.metrics:
            response["metrics"] = record.result.metrics.model_dump()
        if record.result.benchmark:
            response["benchmark"] = record.result.benchmark.model_dump()

    return response


async def list_backtest_runs(
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """List backtest runs with optional filtering."""
    records = list(_runs.values())
    if status is not None:
        records = [r for r in records if r.status == status]

    total = len(records)
    paginated = records[offset : offset + limit]

    return {
        "runs": [
            {
                "backtest_id": r.backtest_id,
                "status": r.status,
                "config": r.config.model_dump(mode="json"),
                "rebalance_count": r.result.rebalance_count if r.result else 0,
                "duration_seconds": r.result.duration_seconds if r.result else 0.0,
                "error_message": r.error_message,
            }
            for r in paginated
        ],
        "total": total,
    }


async def cancel_backtest(backtest_id: str) -> dict:
    """Cancel a running backtest."""
    record = _runs.get(backtest_id)
    if record is None:
        raise KeyError(f"Backtest {backtest_id} not found")
    if record.status in ("completed", "failed", "cancelled"):
        raise ValueError(f"Backtest {backtest_id} already {record.status}")
    if record.context is not None:
        record.context.cancelled.set()
    record.status = "cancelled"
    return {"status": "cancelled"}


async def get_backtest_trades(
    backtest_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Get trades for a backtest run."""
    record = _runs.get(backtest_id)
    if record is None or record.result is None:
        return {"trades": [], "total": 0}

    trades = record.result.trades[offset : offset + limit]
    return {
        "trades": [t.model_dump(mode="json") for t in trades],
        "total": len(record.result.trades),
    }


async def get_backtest_snapshots(
    backtest_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Get snapshots for a backtest run."""
    record = _runs.get(backtest_id)
    if record is None or record.result is None:
        return {"snapshots": [], "total": 0}

    snapshots = record.result.snapshots[offset : offset + limit]
    return {
        "snapshots": [s.model_dump(mode="json") for s in snapshots],
        "total": len(record.result.snapshots),
    }


class ScreeningRequest(BaseModel):
    """Request body for vectorized screening."""

    start_date: date
    end_date: date
    branch_name: str
    step_days: int = 5
    compute_forward_returns: bool = False


async def run_vectorized_screening(request: ScreeningRequest) -> dict:
    """Run vectorized screening engine and return snapshots.

    Sets up a lightweight data chain (store + adapter + data service) and
    a screener adapter that matches the VectorizedScreeningEngine interface.
    """
    from app.modules.backtest.adapters.historical_data import (
        HistoricalDataAdapter,
        HistoricalPriceStore,
    )
    from app.modules.backtest.time_provider import BacktestTimeProvider
    from app.modules.backtest.vectorized import VectorizedScreeningEngine
    from app.modules.data_platform.noop import NoOpCache, NoOpRateLimiter
    from app.modules.data_platform.service import DataPlatformService
    from app.modules.equities.config import EquitiesConfig
    from app.modules.equities.service import EquitiesBranchService
    from app.modules.equities.universe.provider import UniverseProvider

    tp = BacktestTimeProvider(request.start_date)
    equities_config = EquitiesConfig()

    # Load universe symbols
    universe_provider = UniverseProvider()
    branch_key = "growth" if "growth" in request.branch_name else "value"
    universe_stocks = await universe_provider.get_holdings(branch_key)
    symbols = [s.symbol for s in universe_stocks]

    # Preload OHLCV from yfinance into the store
    store = HistoricalPriceStore()
    await store.preload(
        symbols=symbols,
        start_date=request.start_date,
        end_date=request.end_date,
    )

    # Build data adapter chain (no fundamentals cache needed for screening)
    adapter = HistoricalDataAdapter(
        store=store,
        time_provider=tp,
        fundamentals_cache={},
        facts_cache={},
    )
    data_service = DataPlatformService(
        adapter_registry={
            "prices": {"equity": [adapter], "all": [adapter]},
            "fundamentals": {"equity": [adapter]},
        },
        cache=NoOpCache(),
        rate_limiter=NoOpRateLimiter(),
    )

    # Build screener with branch-specific filters
    svc = EquitiesBranchService(config=equities_config)
    screener = svc._build_screener(request.branch_name)

    # Adapter that wraps Screener for the VectorizedScreeningEngine interface:
    # engine calls screener.screen(branch_name) but Screener.screen(stocks, data_service)
    class _ScreenerAdapter:
        def __init__(self, inner, stocks, ds):
            self.filters = inner.filters
            self._inner = inner
            self._stocks = stocks
            self._ds = ds

        async def screen(self, _branch_name: str):
            return await self._inner.screen(self._stocks, self._ds)

    adapted = _ScreenerAdapter(screener, universe_stocks, data_service)

    engine = VectorizedScreeningEngine()
    snapshots = await engine.run(
        branch_name=request.branch_name,
        start_date=request.start_date,
        end_date=request.end_date,
        step_days=request.step_days,
        screener=adapted,
        time_provider=tp,
        store=store,
        compute_forward_returns=request.compute_forward_returns,
    )

    return {
        "snapshots": [s.model_dump(mode="json") for s in snapshots],
        "total": len(snapshots),
    }


# --- Static routes MUST come before dynamic /{backtest_id} routes ---


@router.post("/run", status_code=202)
async def run_backtest(config: BacktestConfig, background_tasks: BackgroundTasks):
    backtest_id = str(uuid.uuid4())
    _runs[backtest_id] = BacktestRunRecord(
        backtest_id=backtest_id,
        status="running",
        config=config,
    )
    background_tasks.add_task(_run_backtest_background, backtest_id, config)
    return {"backtest_id": backtest_id, "status": "running"}


@router.post("/estimate")
async def post_estimate(config: BacktestConfig):
    return await estimate_cost(config.model_dump(mode="json"))


@router.post("/screening")
async def post_screening(request: ScreeningRequest):
    return await run_vectorized_screening(request)


@router.get("/")
async def list_backtests(
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    return await list_backtest_runs(status=status, limit=limit, offset=offset)


# --- Dynamic routes ---


@router.get("/{backtest_id}")
async def get_backtest(backtest_id: str):
    result = await get_backtest_result(backtest_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return result


@router.post("/{backtest_id}/cancel")
async def cancel(backtest_id: str):
    try:
        return await cancel_backtest(backtest_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Backtest not found") from None
    except ValueError:
        raise HTTPException(status_code=409, detail="Backtest already completed") from None


@router.get("/{backtest_id}/trades")
async def get_trades(backtest_id: str, limit: int = 50, offset: int = 0):
    return await get_backtest_trades(backtest_id, limit=limit, offset=offset)


@router.get("/{backtest_id}/snapshots")
async def get_snapshots(backtest_id: str, limit: int = 50, offset: int = 0):
    return await get_backtest_snapshots(backtest_id, limit=limit, offset=offset)

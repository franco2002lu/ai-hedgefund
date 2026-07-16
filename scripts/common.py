"""Shared helpers for operational scripts (weekly pipeline, daily snapshot, backfill)."""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import BranchModel
from app.modules.data_platform.adapters.yahoo_finance import YahooFinanceAdapter
from app.modules.data_platform.cache import DataCache
from app.modules.data_platform.rate_limiter import RateLimiter
from app.modules.data_platform.service import DataPlatformService


def init_data_platform() -> DataPlatformService:
    """Reproduce what app/main.py::lifespan does for DataPlatformService."""
    yahoo = YahooFinanceAdapter()
    registry = {
        "prices": {"equity": [yahoo], "crypto": [yahoo], "all": [yahoo]},
        "fundamentals": {"equity": [yahoo]},
        "news": {"all": [yahoo]},
    }
    return DataPlatformService(
        adapter_registry=registry,
        cache=DataCache(),
        rate_limiter=RateLimiter(),
    )


async def resolve_branch_id(session, branch_name: str) -> str:
    """Resolve a short branch key (e.g. 'growth') to its branches.id UUID.

    Uses a case-insensitive substring match (branches are named 'Equities Growth'
    but the CLI accepts 'growth'). If multiple branches match, raises — the
    caller should pick a more specific name rather than letting us guess.
    """
    stmt = select(BranchModel).where(BranchModel.name.ilike(f"%{branch_name}%"))
    result = await session.execute(stmt)
    rows = result.scalars().all()
    if not rows:
        raise RuntimeError(f"No branch found matching '{branch_name}'")
    if len(rows) > 1:
        names = sorted(r.name for r in rows)
        raise RuntimeError(
            f"Branch name '{branch_name}' matched {len(rows)} branches: {names}. Use a more specific name."
        )
    return str(rows[0].id)

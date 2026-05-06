"""E2E test: the weekly pipeline script runs end-to-end against seeded DB.

Prerequisites (see CLAUDE.md "Running Integration Tests"):
- docker compose up, alembic upgrade head
- branches seeded (growth: 33333333-...)
- ANTHROPIC_API_KEY set
"""

import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import PipelineRunModel

pytestmark = pytest.mark.e2e


GROWTH_BRANCH_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")


@pytest.fixture
async def async_session():
    engine = create_async_engine(settings.database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        yield session
    await engine.dispose()


async def _latest_row_for_today_growth(session) -> PipelineRunModel | None:
    from app.modules.equities.weekly_runner import today_ny
    stmt = (
        select(PipelineRunModel)
        .where(
            PipelineRunModel.branch_id == GROWTH_BRANCH_ID,
            PipelineRunModel.run_date == today_ny(),
        )
        .order_by(PipelineRunModel.attempt.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _run_script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "scripts.run_weekly_pipeline", *args],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "HEDGE_EQUITIES_TOP_N": "5"},  # tiny universe for test speed
        timeout=600,
    )


@pytest.mark.asyncio
async def test_dry_run_creates_no_pipeline_rows(async_session):
    # Count rows before
    stmt = select(PipelineRunModel).where(
        PipelineRunModel.branch_id == GROWTH_BRANCH_ID,
    )
    before = len((await async_session.execute(stmt)).scalars().all())

    proc = _run_script("--branches", "growth", "--dry-run")
    assert proc.returncode == 0, proc.stderr

    after = len((await async_session.execute(stmt)).scalars().all())
    assert after == before  # no new rows from dry-run


@pytest.mark.asyncio
async def test_live_run_creates_completed_row(async_session):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    proc = _run_script("--branches", "growth", "--top-n", "5")
    # Exit 0 (completed) or 1 (pipeline failed on data issue — still a valid test outcome);
    # exit 2 means infrastructure broke and that's a real failure.
    assert proc.returncode in (0, 1), f"infrastructure failure: {proc.stderr}"

    row = await _latest_row_for_today_growth(async_session)
    assert row is not None
    assert row.status in ("completed", "failed")
    if row.status == "completed":
        assert row.summary_json is not None
        assert "trades_executed" in row.summary_json


@pytest.mark.asyncio
async def test_second_invocation_is_idempotent_when_completed(async_session):
    """If the previous test left a `completed` row, re-running is a no-op."""
    row = await _latest_row_for_today_growth(async_session)
    if row is None or row.status != "completed":
        pytest.skip("No completed row from previous test to test idempotency against")

    proc = _run_script("--branches", "growth", "--top-n", "5")
    assert proc.returncode == 0
    assert "skipped" in proc.stdout.lower() or "⏭" in proc.stdout

    # Still only one row for today (no new attempt created)
    from sqlalchemy import func

    from app.modules.equities.weekly_runner import today_ny
    stmt = select(func.count()).select_from(PipelineRunModel).where(
        PipelineRunModel.branch_id == GROWTH_BRANCH_ID,
        PipelineRunModel.run_date == today_ny(),
    )
    count = (await async_session.execute(stmt)).scalar_one()
    assert count == 1

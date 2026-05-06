# Weekly Autonomous Paper Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-04-23-weekly-autonomous-paper-trading-design.md`

**Goal:** Run the existing equities pipeline on a weekly schedule (Monday 8am ET) against a hosted Neon Postgres, with idempotency guards, loud-failure alerting, and a markdown digest per run.

**Architecture:** A GitHub Actions workflow fires a CLI script (`scripts/run_weekly_pipeline.py`) that imports `EquitiesBranchService` and runs the pipeline in-process against hosted Postgres. A new `pipeline_runs` table tracks attempts per branch per day; idempotency is enforced at the `weekly_runner` layer (not the DB), allowing retry rows. Failures re-raise so GH emails the repo owner; successes emit a markdown digest to `$GITHUB_STEP_SUMMARY`.

**Tech Stack:** Python 3.12, FastAPI (for inspection endpoint), SQLAlchemy 2.0 (async), asyncpg, Alembic, Pydantic, pytest, GitHub Actions, Neon Postgres.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `app/db/models.py` | Modify | Add `PipelineRunModel` (rows: run_id PK, branch_id FK, run_date, attempt, status, timings, summary_json, error_msg). |
| `app/db/migrations/versions/XXXXXXXXXXXX_add_pipeline_runs.py` | Create | Alembic migration, `down_revision = "818060af929b"`. |
| `app/config.py` | Modify | Add `equities_top_n` and `equities_enabled_branches` (comma-split list) to `Settings`, env keys `HEDGE_EQUITIES_TOP_N` and `HEDGE_EQUITIES_ENABLED_BRANCHES`. |
| `app/modules/equities/weekly_runner.py` | Create | `WeeklyRunner` class — idempotency lookup, pipeline invocation, status transitions, markdown digest builder. Custom exceptions: `RunInFlightError`, `ManualInterventionRequired`. |
| `app/modules/equities/service.py` | Modify | `run_pipeline()` accepts optional `run_id: str \| None = None` (no behavioral change when None; preserves current callers). |
| `app/modules/equities/api.py` | Modify | Add `GET /api/v1/equities/pipeline-runs` (branch + limit query params). |
| `scripts/run_weekly_pipeline.py` | Create | CLI entrypoint: constructs services manually, iterates enabled branches, calls `WeeklyRunner.execute()`, writes digest to `$GITHUB_STEP_SUMMARY` (or stdout if unset). |
| `.github/workflows/weekly-rebalance.yml` | Create | GH Actions cron + workflow_dispatch. Inputs: branches, top_n, force_retry, dry_run. Secrets: `HEDGE_DATABASE_URL`, `ANTHROPIC_API_KEY`. |
| `.env.example` | Modify | Document `HEDGE_EQUITIES_TOP_N`, `HEDGE_EQUITIES_ENABLED_BRANCHES`, Neon URL format. |
| `CLAUDE.md` | Modify | Add "Running the Weekly Pipeline" section. |
| `tests/unit/equities/test_weekly_runner.py` | Create | Unit tests: idempotency (new/completed/running/failed+retry), run_id format, summary markdown rendering. |
| `tests/unit/equities/test_weekly_config.py` | Create | Unit tests: env var parsing for `HEDGE_EQUITIES_TOP_N` and `HEDGE_EQUITIES_ENABLED_BRANCHES`. |
| `tests/integration/test_weekly_pipeline_e2e.py` | Create | E2E: run the script against seeded DB, assert `pipeline_runs` row + idempotency on re-invocation. |

---

## Tasks

### Task 1: Add `PipelineRunModel` ORM

**Files:**
- Modify: `app/db/models.py` (append new class after line 467)
- Test: `tests/unit/db/test_pipeline_run_model.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/db/test_pipeline_run_model.py`:

```python
"""Unit tests for the PipelineRunModel ORM class."""

from app.db.models import PipelineRunModel


def test_pipeline_run_model_has_expected_columns():
    cols = {c.name for c in PipelineRunModel.__table__.columns}
    expected = {
        "run_id",
        "branch_id",
        "run_date",
        "attempt",
        "status",
        "started_at",
        "completed_at",
        "summary_json",
        "error_msg",
    }
    assert expected.issubset(cols)


def test_pipeline_run_model_primary_key_is_run_id():
    pk_cols = [c.name for c in PipelineRunModel.__table__.primary_key.columns]
    assert pk_cols == ["run_id"]


def test_pipeline_run_model_has_branch_date_index():
    indexes = {ix.name for ix in PipelineRunModel.__table__.indexes}
    assert "idx_pipeline_runs_branch_date" in indexes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/db/test_pipeline_run_model.py -v`
Expected: ImportError or AttributeError — `PipelineRunModel` does not exist.

- [ ] **Step 3: Add the model**

Append to `app/db/models.py` (after the `BacktestRebalanceDetailModel` class at line 467):

```python
# ============================================================
# WEEKLY PIPELINE RUNS (scheduled autonomous execution tracking)
# ============================================================


class PipelineRunModel(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        Index("idx_pipeline_runs_branch_date", "branch_id", "run_date"),
        Index("idx_pipeline_runs_status", "status"),
    )

    run_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False
    )
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_json: Mapped[dict | None] = mapped_column(JSONB)
    error_msg: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/db/test_pipeline_run_model.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/db/models.py tests/unit/db/test_pipeline_run_model.py
git commit -m "feat(db): add PipelineRunModel for weekly pipeline tracking"
```

---

### Task 2: Create Alembic migration for `pipeline_runs`

**Files:**
- Create: `app/db/migrations/versions/<new_revision>_add_pipeline_runs.py`

- [ ] **Step 1: Generate the migration**

Run:

```bash
alembic revision --autogenerate -m "add pipeline runs"
```

Expected: a new file appears in `app/db/migrations/versions/` with a hash prefix like `<12-char-hex>_add_pipeline_runs.py`.

- [ ] **Step 2: Verify and edit the migration**

Open the generated file. It MUST contain:
- `down_revision: str | None = "818060af929b"` (previous head)
- `op.create_table("pipeline_runs", ...)` creating the 9 columns from Task 1
- Two `op.create_index` calls for `idx_pipeline_runs_branch_date` and `idx_pipeline_runs_status`
- Inverse operations in `downgrade()`

Ensure the table creation looks like this (fix if autogen missed anything):

```python
def upgrade() -> None:
    op.create_table(
        "pipeline_runs",
        sa.Column("run_id", sa.String(length=100), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "summary_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"]),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(
        "idx_pipeline_runs_branch_date",
        "pipeline_runs",
        ["branch_id", "run_date"],
        unique=False,
    )
    op.create_index(
        "idx_pipeline_runs_status",
        "pipeline_runs",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_pipeline_runs_status", table_name="pipeline_runs")
    op.drop_index("idx_pipeline_runs_branch_date", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
```

- [ ] **Step 3: Run migration against local Postgres**

Run:

```bash
docker compose -f infrastructure/docker-compose.yml up -d
alembic upgrade head
```

Expected: migration applies without errors. Verify with:

```bash
docker exec infrastructure-postgres-1 psql -U hedgefund -d hedgefund -c "\d pipeline_runs"
```

Expected: 9 columns listed, 2 indexes.

- [ ] **Step 4: Commit**

```bash
git add app/db/migrations/versions/
git commit -m "feat(db): migration for pipeline_runs table"
```

---

### Task 3: Add config knobs for `top_n` and `enabled_branches`

**Files:**
- Modify: `app/config.py`
- Test: `tests/unit/equities/test_weekly_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/equities/test_weekly_config.py`:

```python
"""Tests for weekly-pipeline env var parsing in app.config.Settings."""

import pytest

from app.config import Settings


def test_top_n_defaults_to_50(monkeypatch):
    monkeypatch.delenv("HEDGE_EQUITIES_TOP_N", raising=False)
    s = Settings()
    assert s.equities_top_n == 50


def test_top_n_parses_env(monkeypatch):
    monkeypatch.setenv("HEDGE_EQUITIES_TOP_N", "20")
    s = Settings()
    assert s.equities_top_n == 20


def test_enabled_branches_default(monkeypatch):
    monkeypatch.delenv("HEDGE_EQUITIES_ENABLED_BRANCHES", raising=False)
    s = Settings()
    assert s.equities_enabled_branches == ["growth", "value"]


def test_enabled_branches_parses_comma_split(monkeypatch):
    monkeypatch.setenv("HEDGE_EQUITIES_ENABLED_BRANCHES", "growth")
    s = Settings()
    assert s.equities_enabled_branches == ["growth"]


def test_enabled_branches_trims_whitespace(monkeypatch):
    monkeypatch.setenv("HEDGE_EQUITIES_ENABLED_BRANCHES", "growth, value")
    s = Settings()
    assert s.equities_enabled_branches == ["growth", "value"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/equities/test_weekly_config.py -v`
Expected: AttributeError — `Settings` has no `equities_top_n` attribute.

- [ ] **Step 3: Add the settings**

Edit `app/config.py`. Replace the file with:

```python
from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://hedgefund:localdev@localhost:5433/hedgefund"

    # Execution
    execution_mode: str = "paper"

    # Paper trading
    paper_slippage_bps: float = 5.0
    paper_commission_per_trade: float = 0.0

    # Cache TTLs (seconds)
    cache_prices_ttl: int = 60
    cache_fundamentals_ttl: int = 3600
    cache_news_ttl: int = 300

    # App
    app_name: str = "AI Hedge Fund"
    debug: bool = False

    # Equities weekly pipeline (overridable via HEDGE_EQUITIES_*)
    equities_top_n: int = 50
    equities_enabled_branches: list[str] = ["growth", "value"]

    @field_validator("equities_enabled_branches", mode="before")
    @classmethod
    def _split_branches(cls, v):
        if isinstance(v, str):
            return [b.strip() for b in v.split(",") if b.strip()]
        return v

    model_config = {"env_prefix": "HEDGE_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/equities/test_weekly_config.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/unit/equities/test_weekly_config.py
git commit -m "feat(config): add HEDGE_EQUITIES_TOP_N and HEDGE_EQUITIES_ENABLED_BRANCHES"
```

---

### Task 4: Define custom exceptions + `WeeklyRunSummary` dataclass

**Files:**
- Create: `app/modules/equities/weekly_runner.py`
- Test: `tests/unit/equities/test_weekly_runner.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/equities/test_weekly_runner.py`:

```python
"""Unit tests for WeeklyRunner idempotency and summary rendering."""

import pytest

from app.modules.equities.weekly_runner import (
    ManualInterventionRequired,
    RunInFlightError,
    WeeklyRunSummary,
)


def test_summary_dataclass_fields():
    s = WeeklyRunSummary(
        run_id="2026-04-27-growth",
        branch_name="growth",
        status="completed",
        universe_count=50,
        screened_count=32,
        orders_placed=15,
        trades_executed=12,
        duration_seconds=402.0,
        error=None,
    )
    assert s.run_id == "2026-04-27-growth"
    assert s.status == "completed"


def test_exceptions_are_exceptions():
    assert issubclass(RunInFlightError, Exception)
    assert issubclass(ManualInterventionRequired, Exception)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/equities/test_weekly_runner.py -v`
Expected: ImportError — the module does not exist.

- [ ] **Step 3: Create the weekly_runner module scaffolding**

Create `app/modules/equities/weekly_runner.py`:

```python
"""Orchestrates weekly autonomous pipeline runs with idempotency guards.

Lookup strategy: find the latest pipeline_runs row for (branch_id, run_date),
then decide whether to skip, abort, or proceed based on its status.

run_id format:
  - Attempt 1: "{run_date}-{branch_name}"                e.g., "2026-04-27-growth"
  - Attempt N: "{run_date}-{branch_name}-attempt{N}"     e.g., "2026-04-27-growth-attempt2"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo
from datetime import datetime

logger = logging.getLogger(__name__)


class RunInFlightError(Exception):
    """Raised when a prior run for the same (branch, date) is still running."""


class ManualInterventionRequired(Exception):
    """Raised when a prior run failed and force_retry was not set."""


@dataclass
class WeeklyRunSummary:
    run_id: str
    branch_name: str
    status: str  # "completed" | "failed" | "skipped"
    universe_count: int
    screened_count: int
    orders_placed: int
    trades_executed: int
    duration_seconds: float
    error: str | None = None


_NY_TZ = ZoneInfo("America/New_York")


def today_ny() -> date:
    """Return today's date in America/New_York (not UTC)."""
    return datetime.now(_NY_TZ).date()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/equities/test_weekly_runner.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/equities/weekly_runner.py tests/unit/equities/test_weekly_runner.py
git commit -m "feat(equities): scaffolding for WeeklyRunner (exceptions + summary)"
```

---

### Task 5: `WeeklyRunner.execute()` — happy path (no prior row)

**Files:**
- Modify: `app/modules/equities/weekly_runner.py`
- Test: `tests/unit/equities/test_weekly_runner.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/equities/test_weekly_runner.py`:

```python
from datetime import date
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.modules.equities.models import RunResult
from app.modules.equities.weekly_runner import WeeklyRunner


def _make_run_result(branch: str) -> RunResult:
    return RunResult(
        branch_name=branch,
        universe_count=50,
        screened_count=30,
        signals=[],
        composite_scores=[],
        orders=[],
        trades_executed=10,
    )


@pytest.mark.asyncio
async def test_execute_creates_row_when_no_prior_run():
    branch_id = uuid4()
    service = MagicMock()
    service.run_pipeline = AsyncMock(return_value=_make_run_result("growth"))

    repo = MagicMock()
    repo.find_latest = AsyncMock(return_value=None)  # no prior row
    repo.insert = AsyncMock()
    repo.mark_completed = AsyncMock()

    runner = WeeklyRunner(
        service=service,
        repo=repo,
        session=MagicMock(),
    )

    summary = await runner.execute(
        branch_name="growth",
        branch_id=str(branch_id),
        run_date=date(2026, 4, 27),
        force_retry=False,
    )

    repo.insert.assert_awaited_once()
    inserted = repo.insert.await_args.kwargs
    assert inserted["run_id"] == "2026-04-27-growth"
    assert inserted["attempt"] == 1
    assert inserted["status"] == "running"

    service.run_pipeline.assert_awaited_once()
    repo.mark_completed.assert_awaited_once()
    assert summary.status == "completed"
    assert summary.run_id == "2026-04-27-growth"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/equities/test_weekly_runner.py::test_execute_creates_row_when_no_prior_run -v`
Expected: ImportError / AttributeError — `WeeklyRunner` class not defined.

- [ ] **Step 3: Implement `WeeklyRunner` + `PipelineRunsRepository`**

Append to `app/modules/equities/weekly_runner.py`:

```python
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PipelineRunModel
from app.modules.equities.service import EquitiesBranchService


class PipelineRunsRepository:
    """Thin data-access layer for pipeline_runs rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_latest(
        self, branch_id: str, run_date: date
    ) -> PipelineRunModel | None:
        bid = uuid.UUID(branch_id) if isinstance(branch_id, str) else branch_id
        stmt = (
            select(PipelineRunModel)
            .where(
                PipelineRunModel.branch_id == bid,
                PipelineRunModel.run_date == run_date,
            )
            .order_by(PipelineRunModel.attempt.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def insert(
        self,
        *,
        run_id: str,
        branch_id: str,
        run_date: date,
        attempt: int,
        status: str,
    ) -> None:
        bid = uuid.UUID(branch_id) if isinstance(branch_id, str) else branch_id
        row = PipelineRunModel(
            run_id=run_id,
            branch_id=bid,
            run_date=run_date,
            attempt=attempt,
            status=status,
        )
        self.session.add(row)
        await self.session.flush()

    async def mark_completed(
        self, run_id: str, summary: dict[str, Any]
    ) -> None:
        from datetime import UTC, datetime as dt
        row = await self.session.get(PipelineRunModel, run_id)
        if row is None:
            raise RuntimeError(f"pipeline_runs row {run_id} not found")
        row.status = "completed"
        row.completed_at = dt.now(UTC)
        row.summary_json = summary
        await self.session.flush()

    async def mark_failed(self, run_id: str, error_msg: str) -> None:
        from datetime import UTC, datetime as dt
        row = await self.session.get(PipelineRunModel, run_id)
        if row is None:
            raise RuntimeError(f"pipeline_runs row {run_id} not found")
        row.status = "failed"
        row.completed_at = dt.now(UTC)
        row.error_msg = error_msg
        await self.session.flush()


class WeeklyRunner:
    """Executes a weekly pipeline run for a single branch with idempotency."""

    def __init__(
        self,
        *,
        service: EquitiesBranchService,
        repo: PipelineRunsRepository,
        session: AsyncSession,
    ) -> None:
        self.service = service
        self.repo = repo
        self.session = session

    async def execute(
        self,
        *,
        branch_name: str,
        branch_id: str,
        run_date: date,
        force_retry: bool = False,
    ) -> WeeklyRunSummary:
        latest = await self.repo.find_latest(branch_id, run_date)
        attempt, run_id = self._decide_attempt(
            latest=latest,
            branch_name=branch_name,
            run_date=run_date,
            force_retry=force_retry,
        )
        if attempt == 0:  # sentinel: skip
            assert latest is not None
            return WeeklyRunSummary(
                run_id=latest.run_id,
                branch_name=branch_name,
                status="skipped",
                universe_count=0,
                screened_count=0,
                orders_placed=0,
                trades_executed=0,
                duration_seconds=0.0,
            )

        await self.repo.insert(
            run_id=run_id,
            branch_id=branch_id,
            run_date=run_date,
            attempt=attempt,
            status="running",
        )

        t0 = time.monotonic()
        try:
            result = await self.service.run_pipeline(
                branch_name=branch_name,
                branch_id=branch_id,
                run_id=run_id,
                session=self.session,
            )
        except Exception as exc:
            await self.repo.mark_failed(run_id, repr(exc))
            raise

        duration = time.monotonic() - t0
        orders_placed = len(result.orders)
        summary_dict = {
            "universe_count": result.universe_count,
            "screened_count": result.screened_count,
            "orders_placed": orders_placed,
            "trades_executed": result.trades_executed,
            "duration_seconds": duration,
        }
        await self.repo.mark_completed(run_id, summary_dict)

        return WeeklyRunSummary(
            run_id=run_id,
            branch_name=branch_name,
            status="completed",
            universe_count=result.universe_count,
            screened_count=result.screened_count,
            orders_placed=orders_placed,
            trades_executed=result.trades_executed,
            duration_seconds=duration,
        )

    def _decide_attempt(
        self,
        *,
        latest: PipelineRunModel | None,
        branch_name: str,
        run_date: date,
        force_retry: bool,
    ) -> tuple[int, str]:
        """Return (attempt, run_id). attempt=0 is a sentinel meaning 'skip'."""
        if latest is None:
            return 1, f"{run_date.isoformat()}-{branch_name}"
        if latest.status == "completed":
            logger.info("Run %s already completed — skipping", latest.run_id)
            return 0, latest.run_id
        if latest.status == "running":
            raise RunInFlightError(
                f"Prior run {latest.run_id} is status=running. "
                "Inspect manually before re-running."
            )
        # latest.status == "failed"
        if not force_retry:
            raise ManualInterventionRequired(
                f"Prior run {latest.run_id} failed. "
                "Re-trigger with force_retry=true to create a new attempt."
            )
        next_attempt = latest.attempt + 1
        new_run_id = f"{run_date.isoformat()}-{branch_name}-attempt{next_attempt}"
        return next_attempt, new_run_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/equities/test_weekly_runner.py::test_execute_creates_row_when_no_prior_run -v`
Expected: 1 passed.

Also re-run the full file to confirm nothing else broke:

Run: `pytest tests/unit/equities/test_weekly_runner.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/equities/weekly_runner.py tests/unit/equities/test_weekly_runner.py
git commit -m "feat(equities): WeeklyRunner.execute() happy path + repository"
```

---

### Task 6: `WeeklyRunner.execute()` — completed/running/failed cases

**Files:**
- Modify: `tests/unit/equities/test_weekly_runner.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/equities/test_weekly_runner.py`:

```python
from datetime import UTC, datetime as dt


def _row(status: str, run_id: str, attempt: int = 1):
    from app.db.models import PipelineRunModel
    return PipelineRunModel(
        run_id=run_id,
        branch_id=uuid4(),
        run_date=date(2026, 4, 27),
        attempt=attempt,
        status=status,
        started_at=dt.now(UTC),
    )


@pytest.mark.asyncio
async def test_execute_skips_when_completed_row_exists():
    existing = _row("completed", "2026-04-27-growth")
    service = MagicMock()
    service.run_pipeline = AsyncMock()
    repo = MagicMock()
    repo.find_latest = AsyncMock(return_value=existing)
    repo.insert = AsyncMock()

    runner = WeeklyRunner(service=service, repo=repo, session=MagicMock())

    summary = await runner.execute(
        branch_name="growth",
        branch_id=str(existing.branch_id),
        run_date=date(2026, 4, 27),
        force_retry=False,
    )

    assert summary.status == "skipped"
    service.run_pipeline.assert_not_called()
    repo.insert.assert_not_called()


@pytest.mark.asyncio
async def test_execute_aborts_when_running_row_exists():
    existing = _row("running", "2026-04-27-growth")
    service = MagicMock()
    repo = MagicMock()
    repo.find_latest = AsyncMock(return_value=existing)

    runner = WeeklyRunner(service=service, repo=repo, session=MagicMock())

    with pytest.raises(RunInFlightError):
        await runner.execute(
            branch_name="growth",
            branch_id=str(existing.branch_id),
            run_date=date(2026, 4, 27),
            force_retry=False,
        )


@pytest.mark.asyncio
async def test_execute_aborts_on_failed_without_retry():
    existing = _row("failed", "2026-04-27-growth")
    service = MagicMock()
    repo = MagicMock()
    repo.find_latest = AsyncMock(return_value=existing)

    runner = WeeklyRunner(service=service, repo=repo, session=MagicMock())

    with pytest.raises(ManualInterventionRequired):
        await runner.execute(
            branch_name="growth",
            branch_id=str(existing.branch_id),
            run_date=date(2026, 4, 27),
            force_retry=False,
        )


@pytest.mark.asyncio
async def test_execute_creates_attempt2_row_on_force_retry():
    existing = _row("failed", "2026-04-27-growth", attempt=1)
    service = MagicMock()
    service.run_pipeline = AsyncMock(return_value=_make_run_result("growth"))
    repo = MagicMock()
    repo.find_latest = AsyncMock(return_value=existing)
    repo.insert = AsyncMock()
    repo.mark_completed = AsyncMock()

    runner = WeeklyRunner(service=service, repo=repo, session=MagicMock())

    summary = await runner.execute(
        branch_name="growth",
        branch_id=str(existing.branch_id),
        run_date=date(2026, 4, 27),
        force_retry=True,
    )

    inserted = repo.insert.await_args.kwargs
    assert inserted["run_id"] == "2026-04-27-growth-attempt2"
    assert inserted["attempt"] == 2
    assert summary.status == "completed"


@pytest.mark.asyncio
async def test_execute_marks_failed_on_pipeline_exception():
    service = MagicMock()
    service.run_pipeline = AsyncMock(side_effect=RuntimeError("boom"))
    repo = MagicMock()
    repo.find_latest = AsyncMock(return_value=None)
    repo.insert = AsyncMock()
    repo.mark_failed = AsyncMock()

    runner = WeeklyRunner(service=service, repo=repo, session=MagicMock())

    with pytest.raises(RuntimeError, match="boom"):
        await runner.execute(
            branch_name="growth",
            branch_id=str(uuid4()),
            run_date=date(2026, 4, 27),
            force_retry=False,
        )

    repo.mark_failed.assert_awaited_once()
    call_args = repo.mark_failed.await_args
    assert "boom" in call_args.args[1]
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/unit/equities/test_weekly_runner.py -v`
Expected: 8 passed. (The implementation from Task 5 already handles these cases — this task verifies the behavior.)

If any fail: debug the `_decide_attempt` branches in `weekly_runner.py`.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/equities/test_weekly_runner.py
git commit -m "test(equities): cover WeeklyRunner idempotency cases"
```

---

### Task 7: Thread `run_id` through `EquitiesBranchService.run_pipeline()`

**Files:**
- Modify: `app/modules/equities/service.py`
- Test: `tests/unit/equities/test_service_run_id.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/equities/test_service_run_id.py`:

```python
"""Regression: run_pipeline() accepts run_id without breaking existing behavior."""

import inspect

from app.modules.equities.service import EquitiesBranchService


def test_run_pipeline_accepts_run_id_kwarg():
    sig = inspect.signature(EquitiesBranchService.run_pipeline)
    assert "run_id" in sig.parameters
    assert sig.parameters["run_id"].default is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/equities/test_service_run_id.py -v`
Expected: AssertionError — `run_id` not in signature.

- [ ] **Step 3: Add `run_id` parameter**

Edit `app/modules/equities/service.py`. Change the `run_pipeline` signature around line 106 from:

```python
    async def run_pipeline(
        self,
        branch_name: str,
        branch_id: str,
        trade_execution_service: TradeExecutionService | None = None,
        portfolio_service: PortfolioService | None = None,
        event_log_repo: EventLogRepository | None = None,
        session: AsyncSession | None = None,
        instrument_ids: dict[str, str] | None = None,
        as_of_date=None,
        news_window_days: int = 7,
        manual_news_root: Path | None = Path("data/news/manual"),
    ) -> RunResult:
```

To:

```python
    async def run_pipeline(
        self,
        branch_name: str,
        branch_id: str,
        trade_execution_service: TradeExecutionService | None = None,
        portfolio_service: PortfolioService | None = None,
        event_log_repo: EventLogRepository | None = None,
        session: AsyncSession | None = None,
        instrument_ids: dict[str, str] | None = None,
        as_of_date=None,
        news_window_days: int = 7,
        manual_news_root: Path | None = Path("data/news/manual"),
        run_id: str | None = None,
    ) -> RunResult:
```

Then add one line immediately after the docstring (around line 119):

```python
        """Run the full pipeline: universe -> screen -> analyze -> rebalance -> execute."""
        if run_id:
            logger.info("Starting pipeline run_id=%s branch=%s", run_id, branch_name)
```

No other behavioral change — `run_id` is informational only, used by logs and captured by `WeeklyRunner` externally.

- [ ] **Step 4: Run tests**

Run the new test plus all existing equities unit tests to ensure no regressions:

```bash
pytest tests/unit/equities/test_service_run_id.py -v
pytest tests/unit/equities/ -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/modules/equities/service.py tests/unit/equities/test_service_run_id.py
git commit -m "feat(equities): accept optional run_id in run_pipeline for tracing"
```

---

### Task 8: Apply `top_n` from config to the `UniverseProvider` at run time

**Files:**
- Modify: `app/modules/equities/weekly_runner.py`
- Test: `tests/unit/equities/test_weekly_runner.py`

**Context:** The singleton `EquitiesBranchService` holds a `UniverseProvider` with `top_n=None` (full universe). The weekly job needs the configured `top_n`. Cleanest path: before `execute()`, the caller mutates `service.universe_provider.top_n`. We wrap this in a `configure_service()` helper on `WeeklyRunner` so it's testable and not scattered in the CLI.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/equities/test_weekly_runner.py`:

```python
def test_configure_service_sets_top_n_on_universe_provider():
    service = MagicMock()
    service.universe_provider = MagicMock()
    service.universe_provider.top_n = None

    WeeklyRunner.configure_service(service, top_n=50)

    assert service.universe_provider.top_n == 50


def test_configure_service_with_none_top_n_leaves_unset():
    service = MagicMock()
    service.universe_provider = MagicMock()
    service.universe_provider.top_n = None

    WeeklyRunner.configure_service(service, top_n=None)

    assert service.universe_provider.top_n is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/equities/test_weekly_runner.py::test_configure_service_sets_top_n_on_universe_provider -v`
Expected: AttributeError — `configure_service` does not exist.

- [ ] **Step 3: Add the classmethod**

Append to the `WeeklyRunner` class in `app/modules/equities/weekly_runner.py`:

```python
    @classmethod
    def configure_service(
        cls,
        service: EquitiesBranchService,
        *,
        top_n: int | None,
    ) -> None:
        """Mutate the service's universe provider for this weekly run.

        Called once before any execute() calls. Safe for sequential single-process
        use (the weekly CLI is single-threaded). Do not call from multi-worker
        contexts — the mutation is not thread-safe.
        """
        if top_n is not None:
            service.universe_provider.top_n = top_n
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/equities/test_weekly_runner.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/equities/weekly_runner.py tests/unit/equities/test_weekly_runner.py
git commit -m "feat(equities): WeeklyRunner.configure_service applies top_n to universe provider"
```

---

### Task 9: Markdown digest renderer

**Files:**
- Modify: `app/modules/equities/weekly_runner.py`
- Test: `tests/unit/equities/test_weekly_runner.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/equities/test_weekly_runner.py`:

```python
from app.modules.equities.weekly_runner import render_digest


def test_render_digest_single_success():
    s = WeeklyRunSummary(
        run_id="2026-04-27-growth",
        branch_name="growth",
        status="completed",
        universe_count=50,
        screened_count=32,
        orders_placed=15,
        trades_executed=12,
        duration_seconds=402.5,
        error=None,
    )
    out = render_digest([s], run_date=date(2026, 4, 27))

    assert "# Weekly Rebalance — 2026-04-27" in out
    assert "## growth" in out
    assert "completed" in out or "✅" in out
    assert "Universe: 50" in out
    assert "Screened: 32" in out
    assert "Orders placed: 15" in out
    assert "Trades executed: 12" in out
    assert "6m 42s" in out or "402.5s" in out or "402" in out


def test_render_digest_mixed_success_and_failure():
    s1 = WeeklyRunSummary(
        run_id="2026-04-27-growth",
        branch_name="growth",
        status="completed",
        universe_count=50, screened_count=30, orders_placed=10,
        trades_executed=8, duration_seconds=300.0, error=None,
    )
    s2 = WeeklyRunSummary(
        run_id="2026-04-27-value",
        branch_name="value",
        status="failed",
        universe_count=0, screened_count=0, orders_placed=0,
        trades_executed=0, duration_seconds=12.0,
        error="RuntimeError: yfinance rate limited",
    )
    out = render_digest([s1, s2], run_date=date(2026, 4, 27))

    assert "## growth" in out
    assert "## value" in out
    assert "❌" in out or "failed" in out
    assert "yfinance rate limited" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/equities/test_weekly_runner.py::test_render_digest_single_success -v`
Expected: ImportError — `render_digest` not defined.

- [ ] **Step 3: Implement `render_digest`**

Append to `app/modules/equities/weekly_runner.py`:

```python
def _format_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    if m == 0:
        return f"{s}s"
    return f"{m}m {s:02d}s"


def render_digest(summaries: list[WeeklyRunSummary], *, run_date: date) -> str:
    """Build the markdown digest written to $GITHUB_STEP_SUMMARY."""
    lines = [f"# Weekly Rebalance — {run_date.isoformat()}", ""]
    for s in summaries:
        icon = {"completed": "✅", "failed": "❌", "skipped": "⏭️"}.get(s.status, "❔")
        lines.append(f"## {s.branch_name} {icon}")
        lines.append(f"- Run ID: `{s.run_id}`")
        lines.append(f"- Status: {s.status}")
        if s.status == "completed":
            lines.append(f"- Universe: {s.universe_count}")
            lines.append(f"- Screened: {s.screened_count}")
            lines.append(f"- Orders placed: {s.orders_placed}")
            lines.append(f"- Trades executed: {s.trades_executed}")
            lines.append(f"- Duration: {_format_duration(s.duration_seconds)}")
            if s.orders_placed == 0:
                lines.append("- ⚠️ 0 orders — check data freshness")
        elif s.status == "failed":
            lines.append(f"- Duration before failure: {_format_duration(s.duration_seconds)}")
            lines.append(f"- Error: `{s.error or 'unknown'}`")
        lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/equities/test_weekly_runner.py -v`
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/equities/weekly_runner.py tests/unit/equities/test_weekly_runner.py
git commit -m "feat(equities): markdown digest renderer for weekly runs"
```

---

### Task 10: CLI script `scripts/run_weekly_pipeline.py`

**Files:**
- Create: `scripts/run_weekly_pipeline.py`

**Context:** This script manually wires up what `app/main.py::lifespan` does for FastAPI, so we can run the pipeline without a server. It also applies idempotency via `WeeklyRunner`, iterates enabled branches, and emits the digest.

- [ ] **Step 1: Create the script**

Create `scripts/run_weekly_pipeline.py`:

```python
"""CLI entrypoint for the weekly autonomous paper-trading pipeline.

Reads configuration from environment variables (HEDGE_EQUITIES_TOP_N,
HEDGE_EQUITIES_ENABLED_BRANCHES) and from workflow_dispatch inputs
exported into the env by the GH Actions workflow.

Exit codes:
  0 — all enabled branches ran (completed or skipped as idempotent)
  1 — at least one branch failed
  2 — infrastructure error (DB unreachable, missing secret, etc.)

Writes the markdown digest to the file named by $GITHUB_STEP_SUMMARY
(if set — it is set on GH Actions). Otherwise prints to stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # Make ANTHROPIC_API_KEY and HEDGE_* available

from app.config import settings  # noqa: E402
from app.db.connection import AsyncSessionLocal  # noqa: E402
from app.db.models import BranchModel  # noqa: E402
from app.dependencies import get_equities_service, init_services  # noqa: E402
from app.modules.data_platform.adapters.yahoo_finance import YahooFinanceAdapter  # noqa: E402
from app.modules.data_platform.cache import DataCache  # noqa: E402
from app.modules.data_platform.rate_limiter import RateLimiter  # noqa: E402
from app.modules.data_platform.service import DataPlatformService  # noqa: E402
from app.modules.equities.weekly_runner import (  # noqa: E402
    ManualInterventionRequired,
    PipelineRunsRepository,
    RunInFlightError,
    WeeklyRunSummary,
    WeeklyRunner,
    render_digest,
    today_ny,
)
from app.modules.event_log.repository import PostgresEventLogRepository  # noqa: E402
from app.modules.portfolio.repository import (  # noqa: E402
    PostgresPortfolioRepository,
    PostgresPositionRepository,
    PostgresSnapshotRepository,
)
from app.modules.portfolio.service import PortfolioService  # noqa: E402
from app.modules.trade_execution.repository import (  # noqa: E402
    PostgresOrderRepository,
    PostgresTradeRepository,
)
from app.modules.trade_execution.service import TradeExecutionService  # noqa: E402
from sqlalchemy import select  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("yfinance").setLevel(logging.WARNING)
logger = logging.getLogger("weekly_pipeline")


def _init_data_platform() -> DataPlatformService:
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


async def _resolve_branch_id(session, branch_name: str) -> str | None:
    stmt = select(BranchModel).where(BranchModel.name.ilike(f"%{branch_name}%"))
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return str(row.id) if row else None


async def _run_one_branch(
    *,
    branch_name: str,
    top_n: int | None,
    force_retry: bool,
    dry_run: bool,
) -> WeeklyRunSummary:
    equities_service = get_equities_service()
    WeeklyRunner.configure_service(equities_service, top_n=top_n)

    async with AsyncSessionLocal() as session:
        branch_id = await _resolve_branch_id(session, branch_name)
        if branch_id is None:
            raise RuntimeError(f"No branch found matching '{branch_name}'")

        if dry_run:
            logger.info("[dry_run] Would have executed branch=%s top_n=%s", branch_name, top_n)
            return WeeklyRunSummary(
                run_id=f"dry-run-{branch_name}",
                branch_name=branch_name,
                status="skipped",
                universe_count=0, screened_count=0, orders_placed=0,
                trades_executed=0, duration_seconds=0.0,
            )

        # Build per-request services (same as FastAPI Depends())
        event_log = PostgresEventLogRepository(session)
        portfolio_service = PortfolioService(
            portfolio_repo=PostgresPortfolioRepository(session),
            position_repo=PostgresPositionRepository(session),
            snapshot_repo=PostgresSnapshotRepository(session),
            event_log=event_log,
        )
        from app.dependencies import get_paper_broker
        trade_execution_service = TradeExecutionService(
            order_repo=PostgresOrderRepository(session),
            trade_repo=PostgresTradeRepository(session),
            broker=get_paper_broker(),
            event_log=event_log,
            portfolio_service=portfolio_service,
        )

        # Attach per-request services to the singleton for this run only
        equities_service.trade_execution_service = trade_execution_service
        equities_service.portfolio_service = portfolio_service
        equities_service.event_log = event_log

        repo = PipelineRunsRepository(session)
        runner = WeeklyRunner(
            service=equities_service, repo=repo, session=session
        )

        summary = await runner.execute(
            branch_name=branch_name,
            branch_id=branch_id,
            run_date=today_ny(),
            force_retry=force_retry,
        )
        await session.commit()
        return summary


async def _main_async(args: argparse.Namespace) -> int:
    data_service = _init_data_platform()
    init_services(data_service)

    # Resolve configuration
    top_n = args.top_n if args.top_n is not None else settings.equities_top_n
    branches = args.branches or settings.equities_enabled_branches

    logger.info(
        "Weekly run: branches=%s top_n=%s force_retry=%s dry_run=%s",
        branches, top_n, args.force_retry, args.dry_run,
    )

    summaries: list[WeeklyRunSummary] = []
    any_failed = False
    for branch in branches:
        try:
            s = await _run_one_branch(
                branch_name=branch,
                top_n=top_n,
                force_retry=args.force_retry,
                dry_run=args.dry_run,
            )
            summaries.append(s)
        except (RunInFlightError, ManualInterventionRequired) as exc:
            logger.error("Branch %s aborted: %s", branch, exc)
            summaries.append(WeeklyRunSummary(
                run_id=f"aborted-{branch}",
                branch_name=branch,
                status="failed",
                universe_count=0, screened_count=0, orders_placed=0,
                trades_executed=0, duration_seconds=0.0,
                error=repr(exc),
            ))
            any_failed = True
        except Exception as exc:
            logger.exception("Branch %s failed", branch)
            summaries.append(WeeklyRunSummary(
                run_id=f"failed-{branch}",
                branch_name=branch,
                status="failed",
                universe_count=0, screened_count=0, orders_placed=0,
                trades_executed=0, duration_seconds=0.0,
                error=repr(exc),
            ))
            any_failed = True

    digest = render_digest(summaries, run_date=today_ny())
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        Path(summary_path).write_text(digest, encoding="utf-8")
    else:
        print(digest)

    return 1 if any_failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the weekly equities paper-trading pipeline")
    parser.add_argument("--branches", nargs="*", default=None,
                        help="Branches to run (default: $HEDGE_EQUITIES_ENABLED_BRANCHES)")
    parser.add_argument("--top-n", type=int, default=None,
                        help="Top N holdings per branch (default: $HEDGE_EQUITIES_TOP_N)")
    parser.add_argument("--force-retry", action="store_true",
                        help="If prior run for today failed, create a new attempt row")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate plumbing without invoking the pipeline")
    args = parser.parse_args()

    try:
        exit_code = asyncio.run(_main_async(args))
    except Exception:
        logger.exception("Infrastructure error (DB unreachable / missing secret / etc.)")
        sys.exit(2)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test locally (dry_run)**

Ensure local Postgres is running and seeded. Then:

```bash
docker compose -f infrastructure/docker-compose.yml up -d
alembic upgrade head
# Seed branches if not already (see CLAUDE.md "DB Reset Procedure")
python -m scripts.run_weekly_pipeline --dry-run --branches growth
```

Expected:
- Script exits 0
- Digest printed to stdout with "growth ⏭️" (skipped, dry run)
- No rows inserted into `pipeline_runs`

- [ ] **Step 3: Commit**

```bash
git add scripts/run_weekly_pipeline.py
git commit -m "feat(scripts): CLI entrypoint for weekly autonomous pipeline"
```

---

### Task 11: `GET /api/v1/equities/pipeline-runs` endpoint

**Files:**
- Modify: `app/modules/equities/api.py`

- [ ] **Step 1: Add the endpoint**

Append to `app/modules/equities/api.py` (after the `get_config` endpoints, before the `/{branch_name}` dynamic routes — static routes must precede dynamic ones):

```python
from app.db.models import PipelineRunModel  # add to the existing import block at top


@router.get("/pipeline-runs")
async def get_pipeline_runs(
    branch: str | None = Query(default=None, description="Filter by branch name substring"),
    limit: int = Query(default=20, le=100),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(PipelineRunModel).order_by(
        PipelineRunModel.run_date.desc(),
        PipelineRunModel.attempt.desc(),
    )
    if branch:
        stmt = stmt.join(BranchModel).where(BranchModel.name.ilike(f"%{branch}%"))
    stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [
        {
            "run_id": r.run_id,
            "branch_id": str(r.branch_id),
            "run_date": r.run_date.isoformat(),
            "attempt": r.attempt,
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "summary_json": r.summary_json,
            "error_msg": r.error_msg,
        }
        for r in rows
    ]
```

- [ ] **Step 2: Smoke test**

Start the server:

```bash
uvicorn app.main:app --port 8000
```

In another shell:

```bash
curl -s http://localhost:8000/api/v1/equities/pipeline-runs | python -m json.tool
```

Expected: `[]` (empty list — no runs yet) or a JSON array if prior runs exist. Exit the server.

- [ ] **Step 3: Commit**

```bash
git add app/modules/equities/api.py
git commit -m "feat(api): expose GET /api/v1/equities/pipeline-runs for inspection"
```

---

### Task 12: GitHub Actions workflow

**Files:**
- Create: `.github/workflows/weekly-rebalance.yml`

- [ ] **Step 1: Create the workflow**

Create `.github/workflows/weekly-rebalance.yml`:

```yaml
name: Weekly Rebalance

on:
  schedule:
    # 13:00 UTC = 8:00am America/New_York during EST (winter)
    # = 9:00am EDT (summer). GH cron does not respect DST, so the run
    # shifts by 1h with seasons; acceptable for a weekly paper job.
    - cron: "0 13 * * 1"
  workflow_dispatch:
    inputs:
      branches:
        description: "Comma-separated branches (default: growth,value)"
        required: false
        default: ""
      top_n:
        description: "Top N holdings per branch (default: env HEDGE_EQUITIES_TOP_N or 50)"
        required: false
        default: ""
      force_retry:
        description: "Create a new attempt row if today already has a failed run"
        type: boolean
        required: false
        default: false
      dry_run:
        description: "Skip pipeline execution, validate plumbing only"
        type: boolean
        required: false
        default: false

concurrency:
  group: weekly-rebalance
  cancel-in-progress: false

jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 90
    env:
      HEDGE_DATABASE_URL: ${{ secrets.HEDGE_DATABASE_URL }}
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      HEDGE_EQUITIES_TOP_N: ${{ vars.HEDGE_EQUITIES_TOP_N || '50' }}
      HEDGE_EQUITIES_ENABLED_BRANCHES: ${{ vars.HEDGE_EQUITIES_ENABLED_BRANCHES || 'growth,value' }}
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"

      - name: Apply migrations
        run: alembic upgrade head

      - name: Run weekly pipeline
        run: |
          ARGS=""
          if [ -n "${{ github.event.inputs.branches }}" ]; then
            ARGS="$ARGS --branches $(echo '${{ github.event.inputs.branches }}' | tr ',' ' ')"
          fi
          if [ -n "${{ github.event.inputs.top_n }}" ]; then
            ARGS="$ARGS --top-n ${{ github.event.inputs.top_n }}"
          fi
          if [ "${{ github.event.inputs.force_retry }}" = "true" ]; then
            ARGS="$ARGS --force-retry"
          fi
          if [ "${{ github.event.inputs.dry_run }}" = "true" ]; then
            ARGS="$ARGS --dry-run"
          fi
          python -m scripts.run_weekly_pipeline $ARGS
```

- [ ] **Step 2: Validate YAML syntax locally**

Run:

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/weekly-rebalance.yml'))"
```

Expected: no output (valid YAML).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/weekly-rebalance.yml
git commit -m "feat(ci): GH Actions workflow for weekly rebalance"
```

---

### Task 13: Update `.env.example` and `CLAUDE.md`

**Files:**
- Modify: `.env.example`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update `.env.example`**

Append to `.env.example`:

```bash

# ----- Weekly pipeline -----
# For scheduled runs, point HEDGE_DATABASE_URL at a hosted Postgres (e.g., Neon):
#   HEDGE_DATABASE_URL=postgresql+asyncpg://user:pass@ep-xxxx.neon.tech/hedgefund?ssl=require
# For local dev, keep the Docker Compose default.

# Number of top-ranked holdings per branch to use in weekly runs (by ETF weight)
HEDGE_EQUITIES_TOP_N=50

# Comma-separated branches to run on the weekly schedule
HEDGE_EQUITIES_ENABLED_BRANCHES=growth,value
```

- [ ] **Step 2: Update `CLAUDE.md`**

Add a new section to `CLAUDE.md` immediately after "## Gotchas" (at the end of the file):

```markdown

## Running the Weekly Pipeline

The weekly paper-trading pipeline runs autonomously via GitHub Actions on Mondays at 13:00 UTC (~8am ET winter, ~9am EDT summer; no DST adjustment). Full design: `docs/superpowers/specs/2026-04-23-weekly-autonomous-paper-trading-design.md`.

### Manual dispatch

From the Actions tab on GitHub, run the "Weekly Rebalance" workflow with inputs:
- `branches` (default: `growth,value`) — comma-separated
- `top_n` (default: env `HEDGE_EQUITIES_TOP_N` = 50)
- `force_retry` (default: false) — retry a failed run for today
- `dry_run` (default: false) — skip pipeline execution, validate plumbing only

### Running locally against Neon

```bash
export HEDGE_DATABASE_URL="postgresql+asyncpg://user:pass@ep-xxxx.neon.tech/hedgefund?ssl=require"
alembic upgrade head
python -m scripts.run_weekly_pipeline --branches growth --top-n 20
```

### Inspecting results

With the local server pointed at Neon:

```bash
curl -s http://localhost:8000/api/v1/equities/pipeline-runs?limit=10 | python -m json.tool
```

### GH Actions secrets required

- `HEDGE_DATABASE_URL` — Neon connection string (repo secret)
- `ANTHROPIC_API_KEY` — for LLM analyst calls (repo secret)

### Recovery from a failed run

1. Check the `pipeline_runs` table: `SELECT run_id, status, error_msg FROM pipeline_runs ORDER BY started_at DESC LIMIT 5;`
2. If status=`running` (stuck), inspect event log to determine how far the run got, then `UPDATE pipeline_runs SET status='failed' WHERE run_id = ?;`
3. Re-dispatch the workflow with `force_retry=true` to create an `attempt2` row and resume.
4. There is no automatic crash recovery (intentional — see the design doc).
```

- [ ] **Step 3: Commit**

```bash
git add .env.example CLAUDE.md
git commit -m "docs: weekly pipeline env vars and operator runbook"
```

---

### Task 14: E2E integration test

**Files:**
- Create: `tests/integration/test_weekly_pipeline_e2e.py`

**Context:** This runs against a live seeded DB with `ANTHROPIC_API_KEY`, same model as the existing `tests/integration/test_e2e_equities.py`. Tolerant of 0-orders outcomes (Yahoo rate limits) — same pattern.

- [ ] **Step 1: Create the test**

Create `tests/integration/test_weekly_pipeline_e2e.py`:

```python
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
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

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
```

- [ ] **Step 2: Run the test**

```bash
# Assumes local Postgres running, branches seeded, ANTHROPIC_API_KEY in .env
pytest tests/integration/test_weekly_pipeline_e2e.py -m e2e -v
```

Expected:
- `test_dry_run_creates_no_pipeline_rows` passes
- `test_live_run_creates_completed_row` passes (may take 2-5 min with top_n=5)
- `test_second_invocation_is_idempotent_when_completed` passes if the live run completed

If `ANTHROPIC_API_KEY` is unset, the live test is skipped.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_weekly_pipeline_e2e.py
git commit -m "test(integration): e2e test for weekly pipeline idempotency"
```

---

### Task 15: Pre-deploy validation checklist (manual)

**Files:** none (operational steps)

**Context:** Before the first scheduled Monday run, verify the workflow works end-to-end against production Neon.

- [ ] **Step 1: Provision Neon + GH secrets**

1. Create a Neon project (`hedgefund-prod`), copy the `postgresql://` connection string.
2. Convert to asyncpg form: replace `postgresql://` with `postgresql+asyncpg://` and append `?ssl=require`.
3. In GitHub repo → Settings → Secrets and variables → Actions:
   - Add repo secret `HEDGE_DATABASE_URL` with the Neon URL
   - Add repo secret `ANTHROPIC_API_KEY`
   - (Optional) Add repo variable `HEDGE_EQUITIES_TOP_N=50` and `HEDGE_EQUITIES_ENABLED_BRANCHES=growth,value`

- [ ] **Step 2: Seed Neon DB**

Run migrations and seed branches against Neon:

```bash
export HEDGE_DATABASE_URL="postgresql+asyncpg://...?ssl=require"
alembic upgrade head
# Then seed fund + branches — see CLAUDE.md "DB Reset Procedure" for the INSERT statements
```

- [ ] **Step 3: Dispatch dry-run**

In the GH Actions tab, manually run "Weekly Rebalance" with `dry_run=true`.
Expected: green job, summary shows "growth ⏭️" and "value ⏭️", no rows inserted into `pipeline_runs`.

- [ ] **Step 4: Dispatch live run with top_n=5**

Dispatch again with `top_n=5`, `dry_run=false`.
Expected: green job, summary shows `completed` for each branch, rows in `pipeline_runs`. Check NAV in the portfolio table — should reflect trades.

- [ ] **Step 5: Force a failure to test email**

Temporarily unset `ANTHROPIC_API_KEY` in GH secrets (or set it to an invalid value). Dispatch again.
Expected: job fails, email delivered by GH to repo owner, `pipeline_runs` row has `status=failed` with `error_msg`.

Restore the secret afterwards.

- [ ] **Step 6: Verify idempotency via re-dispatch**

With `ANTHROPIC_API_KEY` restored, dispatch once more with no overrides.
Expected: if the prior run was `completed`, the new run skips (summary shows `skipped`); if it was `failed`, the run aborts with `ManualInterventionRequired` — then re-dispatch with `force_retry=true` and verify an `attempt2` row is created.

- [ ] **Step 7: Commit any config tweaks discovered during validation**

If you found missing env vars or timing issues, commit fixes:

```bash
git add -A
git commit -m "fix(ci): tweaks discovered during pre-deploy validation"
```

---

## Self-review

After writing all tasks, verified:

**Spec coverage:**
- ✅ Architecture/topology → Tasks 10, 12
- ✅ `pipeline_runs` table schema → Tasks 1, 2
- ✅ Config knobs (`top_n`, `enabled_branches`) → Task 3
- ✅ `weekly_runner` idempotency model → Tasks 4, 5, 6
- ✅ `run_id` threading → Task 7
- ✅ `top_n` applied to universe provider → Task 8
- ✅ Markdown digest → Task 9
- ✅ CLI entrypoint → Task 10
- ✅ `GET /api/v1/equities/pipeline-runs` → Task 11
- ✅ GH Actions workflow (cron + dispatch) → Task 12
- ✅ `.env.example`, CLAUDE.md → Task 13
- ✅ Unit tests + E2E integration test → Tasks 3-9, 14
- ✅ Pre-deploy validation → Task 15
- ✅ Manual recovery (NOT auto-recovery) → documented in Task 13 runbook + enforced by the `ManualInterventionRequired` exception in Task 5

**Placeholder scan:** no TBDs, no "add error handling", every code block is complete.

**Type consistency:** `PipelineRunModel`, `PipelineRunsRepository`, `WeeklyRunner`, `WeeklyRunSummary`, `render_digest`, `today_ny` — names match across all tasks.

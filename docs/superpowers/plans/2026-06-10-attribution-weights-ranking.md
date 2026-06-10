# Attribution + Weights + Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the weekly attribution engine (Phase D), ship IC-justified composite weights + holding hysteresis (Phase A), and add cross-sectional ranking to the analysts (Phase B), per `docs/superpowers/specs/2026-06-10-attribution-weights-ranking-design.md`.

**Architecture:** Pure-computation attribution core + thin DB/price orchestrator, persisted to a new `attribution_reports` table and surfaced in the weekly digest. Config-level weight change and a selection-rule change in `PortfolioManager`. A two-stage ranking layer (`agents/ranker.py`) applied after each analyst's batch in the LangGraph workflow, with an LLM ranker for production and a deterministic ranker for backtests.

**Tech Stack:** Python 3.12, SQLAlchemy 2 async + alembic, pydantic, LangGraph, Anthropic SDK, pytest (asyncio_mode=auto). Run all tests with `.venv/bin/python -m pytest`; lint with `ruff check` / `ruff format`.

**Conventions:** TDD every task (test first, watch it fail, minimal code, watch it pass). Commit after each task with the message given. Tests for equities live in `tests/unit/equities/`. The project venv is `.venv/` — the bare `python` on PATH is miniconda and lacks deps.

---

## Phase 1 — D: Attribution engine

### Task 1: `AttributionReportModel` + migration

**Files:**
- Modify: `app/db/models.py` (append after `PipelineRunModel`)
- Create: `app/db/migrations/versions/b91f2a6c3d44_add_attribution_reports.py`
- Test: `tests/unit/db/test_attribution_model.py`

- [ ] **Step 1: Write the failing test**

```python
"""tests/unit/db/test_attribution_model.py"""

from app.db.models import AttributionReportModel


def test_attribution_report_model_columns():
    cols = {c.name for c in AttributionReportModel.__table__.columns}
    assert cols == {
        "id", "branch_id", "branch_name", "decision_date", "as_of_date",
        "basket_return_conviction", "basket_return_equal",
        "benchmark_return", "benchmark_symbol", "spy_return",
        "analyst_ics", "n_holdings", "n_holdings_priced", "created_at",
    }
    assert AttributionReportModel.__tablename__ == "attribution_reports"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/db/test_attribution_model.py -q`
Expected: FAIL with `ImportError: cannot import name 'AttributionReportModel'`

- [ ] **Step 3: Add the model**

Append to `app/db/models.py` (after `PipelineRunModel`, reusing the module's existing imports — `Numeric`, `Date`, `JSONB`, `UUID`, `new_uuid`, `utcnow` are already imported there):

```python
class AttributionReportModel(Base):
    """Weekly post-hoc scoring of a portfolio decision (Phase D attribution)."""

    __tablename__ = "attribution_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False
    )
    branch_name: Mapped[str] = mapped_column(String(50), nullable=False)
    decision_date: Mapped[date] = mapped_column(Date(), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date(), nullable=False)
    basket_return_conviction: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    basket_return_equal: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    benchmark_return: Mapped[float | None] = mapped_column(Numeric(10, 6))
    benchmark_symbol: Mapped[str] = mapped_column(String(10), nullable=False)
    spy_return: Mapped[float | None] = mapped_column(Numeric(10, 6))
    analyst_ics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    n_holdings: Mapped[int] = mapped_column(Integer, nullable=False)
    n_holdings_priced: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("branch_id", "decision_date", name="uq_attribution_branch_decision"),
    )
```

If `UniqueConstraint`, `Date`, or `Integer` are not yet imported at the top of `models.py`, add them to the existing `from sqlalchemy import ...` line. `date` comes from the existing `from datetime import date, datetime` import (add `date` if missing).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/db/test_attribution_model.py -q`
Expected: PASS

- [ ] **Step 5: Write the migration**

Create `app/db/migrations/versions/b91f2a6c3d44_add_attribution_reports.py`:

```python
"""add attribution reports

Revision ID: b91f2a6c3d44
Revises: 5c8e7c02dde9
Create Date: 2026-06-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b91f2a6c3d44"
down_revision: Union[str, None] = "5c8e7c02dde9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "attribution_reports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column("branch_name", sa.String(length=50), nullable=False),
        sa.Column("decision_date", sa.Date(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("basket_return_conviction", sa.Numeric(10, 6), nullable=False),
        sa.Column("basket_return_equal", sa.Numeric(10, 6), nullable=False),
        sa.Column("benchmark_return", sa.Numeric(10, 6), nullable=True),
        sa.Column("benchmark_symbol", sa.String(length=10), nullable=False),
        sa.Column("spy_return", sa.Numeric(10, 6), nullable=True),
        sa.Column("analyst_ics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("n_holdings", sa.Integer(), nullable=False),
        sa.Column("n_holdings_priced", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("branch_id", "decision_date", name="uq_attribution_branch_decision"),
    )


def downgrade() -> None:
    op.drop_table("attribution_reports")
```

- [ ] **Step 6: Verify migration applies against local Docker DB**

Run: `docker compose -f infrastructure/docker-compose.yml up -d && .venv/bin/alembic upgrade head`
Expected: `Running upgrade 5c8e7c02dde9 -> b91f2a6c3d44, add attribution reports`

- [ ] **Step 7: Commit**

```bash
git add app/db/models.py app/db/migrations/versions/b91f2a6c3d44_add_attribution_reports.py tests/unit/db/test_attribution_model.py
git commit -m "feat(attribution): add attribution_reports table and model"
```

### Task 2: Pure attribution math

**Files:**
- Create: `app/modules/equities/attribution.py`
- Test: `tests/unit/equities/test_attribution.py`

The pure core takes plain data (no DB, no network): decision holdings + scores, signals, and a `prices` map of `symbol -> list[(date, close)]` sorted ascending.

- [ ] **Step 1: Write the failing tests**

```python
"""tests/unit/equities/test_attribution.py"""

from datetime import date

import pytest

from app.modules.equities.attribution import (
    AttributionReport,
    compute_report,
    resolve_weights,
    spearman,
)

D0 = date(2026, 6, 1)
D1 = date(2026, 6, 8)


def _series(*closes, start=D0):
    """Build [(date, close)] with consecutive days starting at `start`."""
    from datetime import timedelta
    return [(start + timedelta(days=i), c) for i, c in enumerate(closes)]


class TestSpearman:
    def test_perfect_positive(self):
        assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)

    def test_perfect_negative(self):
        assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)

    def test_ties_average_ranks(self):
        # xs has ties; known spearman for ([1,2,2,4],[1,2,3,4]) = 0.9486832...
        assert spearman([1, 2, 2, 4], [1, 2, 3, 4]) == pytest.approx(0.9487, abs=1e-4)

    def test_constant_input_returns_none(self):
        assert spearman([5, 5, 5], [1, 2, 3]) is None


class TestResolveWeights:
    def test_uses_target_holdings_when_nonzero(self):
        w = resolve_weights(
            target_holdings={"A": 0.6, "B": 0.4},
            composite_scores={"A": {"score": 9, "confidence": 9}},
            buy_symbols=["A", "B"],
        )
        assert w == {"A": 0.6, "B": 0.4}

    def test_falls_back_to_conviction_when_targets_zero(self):
        w = resolve_weights(
            target_holdings={"A": 0, "B": 0},
            composite_scores={
                "A": {"score": 8.0, "confidence": 5.0},   # conviction 40
                "B": {"score": 6.0, "confidence": 10.0},  # conviction 60
            },
            buy_symbols=["A", "B"],
        )
        assert w["A"] == pytest.approx(0.4)
        assert w["B"] == pytest.approx(0.6)

    def test_empty_when_no_data(self):
        assert resolve_weights(target_holdings={}, composite_scores={}, buy_symbols=[]) == {}


class TestComputeReport:
    def test_basket_and_benchmark_returns(self):
        prices = {
            "A": _series(100, 101, 102, 103, 104, 105, 106, 110),   # +10%
            "B": _series(50, 50, 50, 50, 50, 50, 50, 45),           # -10%
            "VOOG": _series(200, 200, 200, 200, 200, 200, 200, 204),  # +2%
            "SPY": _series(400, 400, 400, 400, 400, 400, 400, 396),   # -1%
        }
        report = compute_report(
            branch_name="growth",
            decision_date=D0,
            as_of=D1,
            weights={"A": 0.75, "B": 0.25},
            signals=[],
            prices=prices,
            benchmark_symbol="VOOG",
        )
        assert isinstance(report, AttributionReport)
        assert report.basket_return_conviction == pytest.approx(0.75 * 0.10 + 0.25 * -0.10)
        assert report.basket_return_equal == pytest.approx((0.10 - 0.10) / 2)
        assert report.benchmark_return == pytest.approx(0.02)
        assert report.spy_return == pytest.approx(-0.01)
        assert report.n_holdings == 2
        assert report.n_holdings_priced == 2

    def test_missing_price_symbol_dropped_and_renormalized(self):
        prices = {
            "A": _series(100, 110),  # +10%; B has no prices at all
            "VOOG": _series(200, 202),
            "SPY": _series(400, 404),
        }
        report = compute_report(
            branch_name="growth", decision_date=D0, as_of=D1,
            weights={"A": 0.5, "B": 0.5}, signals=[], prices=prices,
            benchmark_symbol="VOOG",
        )
        assert report.basket_return_conviction == pytest.approx(0.10)
        assert report.n_holdings == 2
        assert report.n_holdings_priced == 1

    def test_analyst_ics_computed_per_type_with_min_n(self):
        # 5 symbols, fundamentals scores perfectly rank-aligned with returns
        prices = {f"S{i}": _series(100, 100 + i) for i in range(5)}  # returns 0..4%
        prices["VOOG"] = _series(200, 200)
        prices["SPY"] = _series(400, 400)
        signals = [
            {"symbol": f"S{i}", "analyst_type": "fundamentals", "bullish_score": i + 1}
            for i in range(5)
        ] + [
            # only 2 news signals -> below min_n of 5 -> None
            {"symbol": "S0", "analyst_type": "news", "bullish_score": 9},
            {"symbol": "S1", "analyst_type": "news", "bullish_score": 1},
        ]
        report = compute_report(
            branch_name="growth", decision_date=D0, as_of=D1,
            weights={f"S{i}": 0.2 for i in range(5)}, signals=signals,
            prices=prices, benchmark_symbol="VOOG",
        )
        assert report.analyst_ics["fundamentals"] == pytest.approx(1.0)
        assert report.analyst_ics["news"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/equities/test_attribution.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.modules.equities.attribution'`

- [ ] **Step 3: Implement the pure core**

Create `app/modules/equities/attribution.py`:

```python
"""Post-hoc attribution of weekly portfolio decisions (Phase D).

Pure computation lives in compute_report(); DB/price orchestration is in
AttributionEngine (added in a later task). Spearman is implemented locally to
avoid a scipy dependency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

logger = logging.getLogger(__name__)

MIN_IC_SAMPLES = 5
BENCHMARK_MAP = {"growth": "VOOG", "value": "VOOV"}


@dataclass(frozen=True)
class AttributionReport:
    branch_name: str
    decision_date: date
    as_of_date: date
    basket_return_conviction: float
    basket_return_equal: float
    benchmark_return: float | None
    benchmark_symbol: str
    spy_return: float | None
    analyst_ics: dict[str, float | None] = field(default_factory=dict)
    n_holdings: int = 0
    n_holdings_priced: int = 0


def _ranks(xs: list[float]) -> list[float]:
    """Average ranks (1-based) with ties sharing the mean rank."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation; None if either input is constant or n < 2."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    rx, ry = _ranks(list(xs)), _ranks(list(ys))
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return None
    return cov / (vx**0.5 * vy**0.5)


def resolve_weights(
    *,
    target_holdings: dict,
    composite_scores: dict,
    buy_symbols: list[str],
) -> dict[str, float]:
    """Decision weights: prefer stored target_holdings; reconstruct from
    conviction (score x confidence over buy symbols) when targets are all zero
    (historical rows predating commit d5123bb)."""
    nonzero = {s: float(w) for s, w in target_holdings.items() if float(w) > 0}
    if nonzero:
        return nonzero
    conv = {}
    for sym in buy_symbols:
        cs = composite_scores.get(sym)
        if cs:
            conv[sym] = float(cs.get("score", 0)) * float(cs.get("confidence", 0))
    total = sum(conv.values())
    if total <= 0:
        return {}
    return {s: c / total for s, c in conv.items()}


def _window_return(series: list[tuple[date, float]], d0: date, d1: date) -> float | None:
    """Return from first close on/after d0 to last close on/before d1."""
    on_or_after = [c for d, c in series if d >= d0]
    in_window = [c for d, c in series if d0 <= d <= d1]
    if not on_or_after or not in_window:
        return None
    first, last = on_or_after[0], in_window[-1]
    if first <= 0:
        return None
    return last / first - 1


def compute_report(
    *,
    branch_name: str,
    decision_date: date,
    as_of: date,
    weights: dict[str, float],
    signals: list[dict],
    prices: dict[str, list[tuple[date, float]]],
    benchmark_symbol: str,
) -> AttributionReport:
    returns: dict[str, float] = {}
    for sym in weights:
        r = _window_return(prices.get(sym, []), decision_date, as_of)
        if r is not None:
            returns[sym] = r

    priced_w = {s: weights[s] for s in returns}
    wsum = sum(priced_w.values())
    conviction = (
        sum(priced_w[s] * returns[s] for s in returns) / wsum if wsum > 0 else 0.0
    )
    equal = sum(returns.values()) / len(returns) if returns else 0.0

    analyst_ics: dict[str, float | None] = {}
    by_type: dict[str, list[tuple[float, float]]] = {}
    for sig in signals:
        r = _window_return(prices.get(sig["symbol"], []), decision_date, as_of)
        if r is not None:
            by_type.setdefault(sig["analyst_type"], []).append(
                (float(sig["bullish_score"]), r)
            )
    for a_type, pairs in by_type.items():
        if len(pairs) < MIN_IC_SAMPLES:
            analyst_ics[a_type] = None
        else:
            analyst_ics[a_type] = spearman([p[0] for p in pairs], [p[1] for p in pairs])

    return AttributionReport(
        branch_name=branch_name,
        decision_date=decision_date,
        as_of_date=as_of,
        basket_return_conviction=conviction,
        basket_return_equal=equal,
        benchmark_return=_window_return(prices.get(benchmark_symbol, []), decision_date, as_of),
        benchmark_symbol=benchmark_symbol,
        spy_return=_window_return(prices.get("SPY", []), decision_date, as_of),
        analyst_ics=analyst_ics,
        n_holdings=len(weights),
        n_holdings_priced=len(returns),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/equities/test_attribution.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add app/modules/equities/attribution.py tests/unit/equities/test_attribution.py
git commit -m "feat(attribution): pure attribution math (basket returns, spearman IC, weight fallback)"
```

### Task 3: `AttributionEngine` orchestrator + upsert persistence

**Files:**
- Modify: `app/modules/equities/attribution.py` (append)
- Test: `tests/unit/equities/test_attribution_engine.py`

The engine loads the latest prior decision + its signals from the DB, fetches prices via `DataPlatformService.get_prices` (which returns `{"bars": [{"timestamp": ..., "close": ...}, ...]}`), calls `compute_report`, and upserts an `AttributionReportModel` row. Tests use mocks for session and data service.

- [ ] **Step 1: Write the failing tests**

```python
"""tests/unit/equities/test_attribution_engine.py"""

import json
import uuid
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.equities.attribution import AttributionEngine

BRANCH_ID = str(uuid.uuid4())


def _decision_row(decided_at: date):
    row = MagicMock()
    row.decided_at = datetime(decided_at.year, decided_at.month, decided_at.day, 16, 0)
    row.branch_name = "growth"
    row.target_holdings = {"A": 0.6, "B": 0.4}
    row.composite_scores = {}
    row.orders_generated = [
        {"symbol": "A", "side": "buy", "quantity": 1, "reason": "new_position"},
        {"symbol": "B", "side": "buy", "quantity": 1, "reason": "new_position"},
    ]
    row.screening_run_id = uuid.uuid4()
    return row


def _signal_row(symbol, a_type, score):
    row = MagicMock()
    row.symbol, row.analyst_type, row.bullish_score = symbol, a_type, score
    return row


def _data_service(price_map):
    ds = MagicMock()

    async def get_prices(symbol, start_date, end_date, **kw):
        closes = price_map.get(symbol)
        if closes is None:
            raise RuntimeError("no data")
        return {
            "bars": [
                {"timestamp": (start_date + timedelta(days=i)).isoformat(), "close": c}
                for i, c in enumerate(closes)
            ]
        }

    ds.get_prices = AsyncMock(side_effect=get_prices)
    return ds


def _session_returning(decision, signals, existing_report=None):
    """Mock AsyncSession whose execute() returns decision, then signals, then existing report."""
    session = MagicMock()
    results = []
    for payload in (decision, signals, existing_report):
        r = MagicMock()
        if isinstance(payload, list):
            r.scalars.return_value.all.return_value = payload
        else:
            r.scalar_one_or_none.return_value = payload
        results.append(r)
    session.execute = AsyncMock(side_effect=results)
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


async def test_compute_returns_report_and_persists_new_row():
    decision = _decision_row(date(2026, 6, 1))
    signals = [_signal_row(s, "fundamentals", i + 1) for i, s in enumerate("ABCDE")]
    prices = {
        "A": [100, 110], "B": [50, 55], "C": [10, 10], "D": [20, 20], "E": [30, 30],
        "VOOG": [200, 202], "SPY": [400, 400],
    }
    session = _session_returning(decision, signals)
    engine = AttributionEngine(data_service=_data_service(prices))

    report = await engine.compute_and_persist(
        session, branch_id=BRANCH_ID, branch_name="growth", as_of=date(2026, 6, 8)
    )

    assert report is not None
    assert report.basket_return_conviction == pytest.approx(0.6 * 0.10 + 0.4 * 0.10)
    session.add.assert_called_once()
    persisted = session.add.call_args.args[0]
    assert persisted.branch_name == "growth"
    assert json.dumps(persisted.analyst_ics)  # JSON-serializable


async def test_returns_none_when_no_prior_decision():
    session = _session_returning(None, [])
    engine = AttributionEngine(data_service=_data_service({}))
    report = await engine.compute_and_persist(
        session, branch_id=BRANCH_ID, branch_name="growth", as_of=date(2026, 6, 8)
    )
    assert report is None


async def test_returns_none_when_decision_stale():
    decision = _decision_row(date(2026, 5, 1))  # > 14 days before as_of
    session = _session_returning(decision, [])
    engine = AttributionEngine(data_service=_data_service({}))
    report = await engine.compute_and_persist(
        session, branch_id=BRANCH_ID, branch_name="growth", as_of=date(2026, 6, 8)
    )
    assert report is None


async def test_upsert_overwrites_existing_row():
    decision = _decision_row(date(2026, 6, 1))
    existing = MagicMock()
    prices = {"A": [100, 110], "B": [50, 55], "VOOG": [200, 202], "SPY": [400, 400]}
    session = _session_returning(decision, [], existing_report=existing)
    engine = AttributionEngine(data_service=_data_service(prices))

    report = await engine.compute_and_persist(
        session, branch_id=BRANCH_ID, branch_name="growth", as_of=date(2026, 6, 8)
    )

    assert report is not None
    session.add.assert_not_called()  # updated in place instead
    assert existing.basket_return_conviction == pytest.approx(report.basket_return_conviction)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/equities/test_attribution_engine.py -q`
Expected: FAIL with `ImportError: cannot import name 'AttributionEngine'`

- [ ] **Step 3: Implement the engine**

Append to `app/modules/equities/attribution.py`:

```python
import uuid as _uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db.models import AgentSignalModel, AttributionReportModel, PortfolioDecisionModel

MAX_DECISION_AGE_DAYS = 14
_PRICE_LOOKBACK_PAD_DAYS = 1


class AttributionEngine:
    """Loads the prior decision + signals, fetches prices, computes and persists."""

    def __init__(self, data_service) -> None:
        self.data_service = data_service

    async def compute_and_persist(
        self, session, *, branch_id: str, branch_name: str, as_of: date
    ) -> AttributionReport | None:
        bid = _uuid.UUID(branch_id) if isinstance(branch_id, str) else branch_id

        stmt = (
            select(PortfolioDecisionModel)
            .where(
                PortfolioDecisionModel.branch_id == bid,
                PortfolioDecisionModel.decided_at < datetime(as_of.year, as_of.month, as_of.day),
            )
            .order_by(PortfolioDecisionModel.decided_at.desc())
            .limit(1)
        )
        decision = (await session.execute(stmt)).scalar_one_or_none()
        if decision is None:
            logger.info("Attribution: no prior decision for %s", branch_name)
            return None
        decision_date = decision.decided_at.date()
        if (as_of - decision_date).days > MAX_DECISION_AGE_DAYS:
            logger.info(
                "Attribution: latest decision %s is stale (> %d days) — skipping",
                decision_date, MAX_DECISION_AGE_DAYS,
            )
            return None

        sig_stmt = select(AgentSignalModel).where(
            AgentSignalModel.screening_run_id == decision.screening_run_id
        )
        signal_rows = (await session.execute(sig_stmt)).scalars().all()
        signals = [
            {"symbol": r.symbol, "analyst_type": r.analyst_type, "bullish_score": r.bullish_score}
            for r in signal_rows
        ]

        buy_symbols = [
            o["symbol"] for o in (decision.orders_generated or []) if o.get("side") == "buy"
        ]
        weights = resolve_weights(
            target_holdings=decision.target_holdings or {},
            composite_scores=decision.composite_scores or {},
            buy_symbols=buy_symbols,
        )

        benchmark = BENCHMARK_MAP.get(
            "growth" if "growth" in branch_name.lower() else "value", "SPY"
        )
        symbols = set(weights) | {s["symbol"] for s in signals} | {benchmark, "SPY"}
        prices = await self._fetch_prices(sorted(symbols), decision_date, as_of)

        report = compute_report(
            branch_name=branch_name,
            decision_date=decision_date,
            as_of=as_of,
            weights=weights,
            signals=signals,
            prices=prices,
            benchmark_symbol=benchmark,
        )
        await self._upsert(session, bid, report)
        return report

    async def _fetch_prices(
        self, symbols: list[str], d0: date, d1: date
    ) -> dict[str, list[tuple[date, float]]]:
        out: dict[str, list[tuple[date, float]]] = {}
        start = d0 - timedelta(days=_PRICE_LOOKBACK_PAD_DAYS)
        for sym in symbols:
            try:
                result = await self.data_service.get_prices(sym, start, d1)
                series = []
                for bar in result.get("bars", []):
                    ts = bar["timestamp"]
                    d = date.fromisoformat(ts[:10]) if isinstance(ts, str) else ts.date()
                    series.append((d, float(bar["close"])))
                out[sym] = sorted(series)
            except Exception:
                logger.warning("Attribution: no prices for %s", sym)
        return out

    async def _upsert(self, session, branch_id, report: AttributionReport) -> None:
        stmt = select(AttributionReportModel).where(
            AttributionReportModel.branch_id == branch_id,
            AttributionReportModel.decision_date == report.decision_date,
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        values = {
            "branch_name": report.branch_name,
            "as_of_date": report.as_of_date,
            "basket_return_conviction": report.basket_return_conviction,
            "basket_return_equal": report.basket_return_equal,
            "benchmark_return": report.benchmark_return,
            "benchmark_symbol": report.benchmark_symbol,
            "spy_return": report.spy_return,
            "analyst_ics": report.analyst_ics,
            "n_holdings": report.n_holdings,
            "n_holdings_priced": report.n_holdings_priced,
        }
        if existing is not None:
            for k, v in values.items():
                setattr(existing, k, v)
        else:
            session.add(
                AttributionReportModel(
                    branch_id=branch_id, decision_date=report.decision_date, **values
                )
            )
        await session.flush()
```

Note: the `import` lines go at the top of the file with the existing imports, not mid-file (ruff enforces import ordering).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/equities/test_attribution_engine.py tests/unit/equities/test_attribution.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add app/modules/equities/attribution.py tests/unit/equities/test_attribution_engine.py
git commit -m "feat(attribution): engine orchestrator with DB loads, price fetch, upsert"
```

### Task 4: Digest + summary integration

**Files:**
- Modify: `app/modules/equities/weekly_runner.py` (`WeeklyRunSummary` at :42-52, `render_digest` at :264)
- Test: `tests/unit/equities/test_weekly_runner.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/unit/equities/test_weekly_runner.py`)

```python
from datetime import date as _date

from app.modules.equities.attribution import AttributionReport
from app.modules.equities.weekly_runner import WeeklyRunSummary, render_digest


class TestDigestAttribution:
    def test_digest_includes_attribution_section(self):
        report = AttributionReport(
            branch_name="growth",
            decision_date=_date(2026, 6, 1),
            as_of_date=_date(2026, 6, 8),
            basket_return_conviction=0.012,
            basket_return_equal=0.009,
            benchmark_return=0.004,
            benchmark_symbol="VOOG",
            spy_return=-0.003,
            analyst_ics={"fundamentals": 0.11, "news": -0.18, "technical": None},
            n_holdings=20,
            n_holdings_priced=20,
        )
        summary = WeeklyRunSummary(
            run_id="2026-06-08-growth", branch_name="growth", status="completed",
            universe_count=50, screened_count=24, orders_placed=20,
            trades_executed=20, duration_seconds=75.0, attribution=report,
        )
        digest = render_digest([summary], run_date=_date(2026, 6, 8))
        assert "Last week (2026-06-01)" in digest
        assert "+1.20%" in digest          # conviction basket
        assert "eq-wt +0.90%" in digest
        assert "VOOG +0.40%" in digest
        assert "fund +0.11" in digest
        assert "tech n/a" in digest

    def test_digest_without_attribution_unchanged(self):
        summary = WeeklyRunSummary(
            run_id="x", branch_name="growth", status="completed",
            universe_count=1, screened_count=1, orders_placed=1,
            trades_executed=1, duration_seconds=1.0,
        )
        digest = render_digest([summary], run_date=_date(2026, 6, 8))
        assert "Last week" not in digest
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/equities/test_weekly_runner.py -q -k Digest`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'attribution'`

- [ ] **Step 3: Implement**

In `weekly_runner.py`, add to `WeeklyRunSummary` (after `error`):

```python
    attribution: "AttributionReport | None" = None
```

with a `TYPE_CHECKING` import `from app.modules.equities.attribution import AttributionReport` (the dataclass field uses a string annotation, so no runtime import cycle). Add helper + digest lines in `render_digest`, inside the `if s.status == "completed":` block after the duration line:

```python
def _fmt_pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.2%}"


def _fmt_ic(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.2f}"
```

```python
            if s.attribution is not None:
                a = s.attribution
                ics = a.analyst_ics
                lines.append(
                    f"- Last week ({a.decision_date.isoformat()}): "
                    f"basket {_fmt_pct(a.basket_return_conviction)} "
                    f"(eq-wt {_fmt_pct(a.basket_return_equal)}) vs "
                    f"{a.benchmark_symbol} {_fmt_pct(a.benchmark_return)}, "
                    f"SPY {_fmt_pct(a.spy_return)} · "
                    f"IC fund {_fmt_ic(ics.get('fundamentals'))} / "
                    f"news {_fmt_ic(ics.get('news'))} / "
                    f"tech {_fmt_ic(ics.get('technical'))}"
                )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/equities/test_weekly_runner.py -q`
Expected: PASS (all, including pre-existing)

- [ ] **Step 5: Commit**

```bash
git add app/modules/equities/weekly_runner.py tests/unit/equities/test_weekly_runner.py
git commit -m "feat(attribution): attribution section in weekly digest"
```

### Task 5: CLI wiring in `run_weekly_pipeline`

**Files:**
- Modify: `scripts/run_weekly_pipeline.py:105-164` (`_run_one_branch`)

No new unit test (thin glue, covered by the e2e weekly pipeline test); behavior requirement: attribution failure must not fail the run.

- [ ] **Step 1: Implement**

In `_run_one_branch`, after `summary = await runner.execute(...)` (change the bare `return await runner.execute(...)` to assign first), add before returning:

```python
        summary = await runner.execute(
            branch_name=branch_name,
            branch_id=branch_id,
            run_date=today_ny(),
            force_retry=force_retry,
        )

        # Phase D: score last week's decision now that a week of prices exists.
        # Never allowed to fail the trading run. begin_nested() (SAVEPOINT)
        # ensures a failed attribution flush can't poison the outer
        # transaction's COMMIT with PendingRollbackError.
        try:
            engine = AttributionEngine(data_service=equities_service.data_service)
            async with session.begin_nested():
                summary.attribution = await engine.compute_and_persist(
                    session,
                    branch_id=branch_id,
                    branch_name=branch_name,
                    as_of=run_date,
                )
        except Exception:
            logger.warning("Attribution failed for %s — continuing", branch_name, exc_info=True)

        return summary
```

Add the import with the other equities imports:

```python
from app.modules.equities.attribution import AttributionEngine  # noqa: E402
```

- [ ] **Step 2: Verify nothing broke**

Run: `.venv/bin/python -m pytest tests/unit/ -q` and `ruff check scripts/run_weekly_pipeline.py`
Expected: all PASS / no lint errors

- [ ] **Step 3: Commit**

```bash
git add scripts/run_weekly_pipeline.py
git commit -m "feat(attribution): compute and persist attribution in weekly CLI"
```

### Task 6: Backfill script + production backfill

**Files:**
- Create: `scripts/backfill_attribution.py`

- [ ] **Step 1: Implement the script**

```python
"""Backfill attribution_reports for all historical portfolio decisions.

Usage:
    HEDGE_DATABASE_URL=postgresql+asyncpg://... python -m scripts.backfill_attribution
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

from app.db.connection import async_session_factory  # noqa: E402
from app.db.models import BranchModel, PortfolioDecisionModel  # noqa: E402
from app.modules.equities.attribution import AttributionEngine  # noqa: E402
from app.modules.equities.weekly_runner import today_ny  # noqa: E402
from scripts.run_weekly_pipeline import _init_data_platform  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("backfill_attribution")


async def main() -> None:
    engine = AttributionEngine(data_service=_init_data_platform())
    async with async_session_factory() as session, session.begin():
        decisions = (
            (await session.execute(
                select(PortfolioDecisionModel).order_by(PortfolioDecisionModel.decided_at)
            )).scalars().all()
        )
        branches = {
            b.id: b.name
            for b in (await session.execute(select(BranchModel))).scalars().all()
        }
        for d in decisions:
            # Score each decision one week after it was made (or today if sooner)
            as_of = min(d.decided_at.date() + timedelta(days=7), today_ny())
            report = await engine.compute_and_persist(
                session,
                branch_id=str(d.branch_id),
                branch_name=d.branch_name or branches.get(d.branch_id, "unknown"),
                as_of=as_of,
            )
            if report:
                logger.info(
                    "%s %s: basket %+.2%% (eq %+.2%%) vs %s %+.2%%",
                    report.branch_name, report.decision_date,
                    report.basket_return_conviction * 100,
                    report.basket_return_equal * 100,
                    report.benchmark_symbol,
                    (report.benchmark_return or 0) * 100,
                )


if __name__ == "__main__":
    asyncio.run(main())
```

Caveat: `compute_and_persist` looks up "the latest decision strictly before as_of" — for backfill this resolves to each decision in turn as long as `as_of` for decision N is at most decision N+1's date. Weekly cadence guarantees that (as_of = decision + 7 days = next decision's date, and the lookup uses `<` on the as_of midnight timestamp while decisions are stored with intraday times — so decision N+1 made at 15:53 on `as_of` day is NOT selected). Verify row counts after running.

- [ ] **Step 2: Run unit suite + lint**

Run: `.venv/bin/python -m pytest tests/unit/ -q && ruff check scripts/ app/`
Expected: PASS / clean

- [ ] **Step 3: Apply migration + backfill against Neon**

```bash
export HEDGE_DATABASE_URL="<neon connection string, asyncpg form>"
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.backfill_attribution
```

Expected: one log line per decision from 2026-05-11 onward (10 reports: 5 weeks × 2 branches; the 2026-05-06 smoke decisions also get rows). Verify: `SELECT branch_name, decision_date, basket_return_conviction, benchmark_return FROM attribution_reports ORDER BY decision_date;`

- [ ] **Step 4: Commit**

```bash
git add scripts/backfill_attribution.py
git commit -m "feat(attribution): historical backfill script"
```

---

## Phase 2 — A: Composite weights + hysteresis

### Task 7: Reweight composite defaults

**Files:**
- Modify: `app/modules/equities/config.py:61-63`
- Test: `tests/unit/equities/test_config.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/unit/equities/test_config.py`)

```python
from app.modules.equities.config import AgentsConfig


def test_composite_weights_favor_fundamentals():
    cfg = AgentsConfig()
    assert cfg.weight_fundamentals == 0.60
    assert cfg.weight_news == 0.20
    assert cfg.weight_technical == 0.20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/equities/test_config.py -q`
Expected: FAIL with `assert 0.4 == 0.6`

- [ ] **Step 3: Change the defaults** in `app/modules/equities/config.py`:

```python
    # Composite score weights (must sum to 1.0).
    # 2026-06-10: reweighted toward fundamentals based on live rank-IC
    # (fund +0.04, news -0.20, tech -0.19 over 5 production weeks) — see
    # docs/superpowers/specs/2026-06-10-attribution-weights-ranking-design.md
    weight_fundamentals: float = 0.60
    weight_news: float = 0.20
    weight_technical: float = 0.20
```

- [ ] **Step 4: Run the equities unit tests** (other tests may assert old weights — update any that do to construct `AgentsConfig(weight_fundamentals=0.4, weight_news=0.35, weight_technical=0.25)` explicitly rather than relying on defaults)

Run: `.venv/bin/python -m pytest tests/unit/ -q`
Expected: PASS after fixing any default-dependent tests

- [ ] **Step 5: Commit**

```bash
git add app/modules/equities/config.py tests/unit/
git commit -m "feat(weights): reweight composite toward fundamentals (0.60/0.20/0.20)"
```

### Task 8: Holding hysteresis in `select_stocks`

**Files:**
- Modify: `app/modules/equities/config.py` (`PortfolioConfig`), `app/modules/equities/agents/portfolio_manager.py:55-65`
- Test: `tests/unit/equities/test_portfolio_manager.py` (append)

- [ ] **Step 1: Write the failing tests** (append; reuse the file's existing helpers for building `CompositeScore`s if present, else construct directly)

```python
from app.modules.equities.config import AgentsConfig, PortfolioConfig
from app.modules.equities.models import CompositeScore


def _score(symbol, score, confidence=5.0):
    return CompositeScore(
        symbol=symbol, composite_score=score, composite_confidence=confidence,
        conviction=score * confidence,
    )


def _pm(**portfolio_kwargs):
    from app.modules.equities.agents.portfolio_manager import PortfolioManager
    return PortfolioManager(
        agents_config=AgentsConfig(),
        portfolio_config=PortfolioConfig(**portfolio_kwargs),
    )


class TestSelectStocksHysteresis:
    def test_held_stock_outside_top_n_but_above_exit_kept(self):
        pm = _pm(target_holdings=2, max_holdings=4)
        scores = [_score(f"S{i}", 9 - i) for i in range(4)]  # S0 best ... S3 worst
        # S3 (score 6, rank 4) is held -> kept despite being outside top 2
        result = pm.select_stocks(scores, current_holdings={"S3"})
        assert {s.symbol for s in result} == {"S0", "S1", "S3"}

    def test_held_stock_below_exit_threshold_dropped(self):
        pm = _pm(target_holdings=2, max_holdings=4, exit_score_threshold=4.0)
        scores = [_score("S0", 9), _score("S1", 8), _score("S2", 3.5)]
        result = pm.select_stocks(scores, current_holdings={"S2"})
        assert {s.symbol for s in result} == {"S0", "S1"}

    def test_held_stock_outside_max_holdings_rank_dropped(self):
        pm = _pm(target_holdings=1, max_holdings=2)
        scores = [_score("S0", 9), _score("S1", 8), _score("S2", 7)]
        # S2 held but ranks 3rd > max_holdings=2 -> dropped
        result = pm.select_stocks(scores, current_holdings={"S2"})
        assert {s.symbol for s in result} == {"S0"}

    def test_cap_drops_lowest_conviction_keeps_first(self):
        pm = _pm(target_holdings=2, max_holdings=3)
        scores = [_score(f"S{i}", 9 - i) for i in range(5)]  # S0..S4
        # S2, S3, S4 all held and above exit; only one keep slot available
        result = pm.select_stocks(scores, current_holdings={"S2", "S3", "S4"})
        assert {s.symbol for s in result} == {"S0", "S1", "S2"}

    def test_no_current_holdings_matches_old_behavior(self):
        pm = _pm(target_holdings=2, max_holdings=4)
        scores = [_score(f"S{i}", 9 - i) for i in range(4)]
        assert [s.symbol for s in pm.select_stocks(scores)] == ["S0", "S1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/equities/test_portfolio_manager.py -q -k Hysteresis`
Expected: FAIL with `TypeError: select_stocks() got an unexpected keyword argument 'current_holdings'`

- [ ] **Step 3: Implement**

In `config.py`, add to `PortfolioConfig`:

```python
    # A held stock exits only when its composite score drops below this
    # threshold or it falls outside max_holdings by conviction rank.
    exit_score_threshold: float = 4.0
```

Replace `select_stocks` in `portfolio_manager.py`:

```python
    def select_stocks(
        self,
        scores: list[CompositeScore],
        current_holdings: set[str] | None = None,
    ) -> list[CompositeScore]:
        """Top-N by conviction, with hysteresis for currently held names.

        New entries must rank in the top target_holdings. A held name is kept
        while its score >= exit_score_threshold and it ranks within
        max_holdings — held names near the rank boundary don't churn weekly.
        """
        if not scores:
            return []
        cfg = self.portfolio_config
        held = current_holdings or set()
        eligible = [s for s in scores if s.composite_score >= cfg.min_composite_score]
        eligible.sort(key=lambda s: s.conviction, reverse=True)
        top_n = min(cfg.target_holdings, cfg.max_holdings)
        selected = eligible[:top_n]
        keeps = [
            s for i, s in enumerate(eligible)
            if i >= top_n
            and s.symbol in held
            and s.composite_score >= cfg.exit_score_threshold
            and i < cfg.max_holdings
        ]
        # keeps are already conviction-sorted; cap drops the lowest first
        return (selected + keeps)[: cfg.max_holdings]
```

Note: `exit_score_threshold` only matters when it exceeds `min_composite_score` filtering; both default to 4.0 today, so the rank-window (top 20 → top 30) is the active hysteresis. The threshold exists as an independent dial.

- [ ] **Step 4: Wire current holdings through the graph**

In `app/modules/equities/agents/graph.py`, in the `portfolio_decision` node, change:

```python
        selected = pm.select_stocks(scores)
```

to:

```python
        selected = pm.select_stocks(scores, current_holdings=set(current_positions))
```

(`current_positions` is already in scope at graph.py:172.)

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/equities/ -q`
Expected: PASS (all — `TestGraphEndToEnd` mocks `pm.select_stocks` so the wiring change is transparent there)

- [ ] **Step 6: Commit**

```bash
git add app/modules/equities/config.py app/modules/equities/agents/portfolio_manager.py app/modules/equities/agents/graph.py tests/unit/equities/test_portfolio_manager.py
git commit -m "feat(portfolio): holding hysteresis in stock selection"
```

---

## Phase 3 — B: Cross-sectional ranking

### Task 9: `invoke_raw` on the LLM client

**Files:**
- Modify: `app/modules/equities/agents/llm_client.py`
- Test: `tests/unit/equities/test_llm_client.py` (append)

- [ ] **Step 1: Write the failing test** (append; follow the file's existing mock pattern for the anthropic client — check how existing tests stub `_client.messages.create`)

```python
from unittest.mock import AsyncMock, MagicMock

from app.modules.equities.agents.llm_client import AnthropicAnalystClient


async def test_invoke_raw_returns_text_without_parsing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = AnthropicAnalystClient()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='{"ranking": ["A", "B"]}')]
    client._client = MagicMock()
    client._client.messages.create = AsyncMock(return_value=fake_response)

    text = await client.invoke_raw("rank these", system_prompt="you are a ranker")

    assert text == '{"ranking": ["A", "B"]}'
    kwargs = client._client.messages.create.call_args.kwargs
    assert kwargs["max_tokens"] == 2048  # rankings of ~30 symbols need more room


async def test_invoke_raw_uses_response_cache(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cache = MagicMock()
    cache.get.return_value = {"raw_text": "cached!"}
    client = AnthropicAnalystClient(response_cache=cache)
    client._client = MagicMock()

    text = await client.invoke_raw("p", system_prompt="s")

    assert text == "cached!"
    client._client.messages.create.assert_not_called() if hasattr(
        client._client.messages, "create"
    ) else None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/equities/test_llm_client.py -q -k invoke_raw`
Expected: FAIL with `AttributeError: ... has no attribute 'invoke_raw'`

- [ ] **Step 3: Implement** (append method to `AnthropicAnalystClient`)

```python
    async def invoke_raw(
        self, prompt: str, *, system_prompt: str, max_tokens: int = 2048
    ) -> str:
        """Send prompt and return raw text (no analyst-JSON parsing/clamping).

        Used by the cross-sectional ranker, whose response is a ranking JSON,
        not a bullish_score object. Flows through the same response cache as
        invoke() so LLM-mode backtests stay reproducible.
        """
        if self._client is None:
            raise RuntimeError("ANTHROPIC_API_KEY not set — cannot invoke LLM analyst")

        if self._response_cache is not None:
            cached = self._response_cache.get(system_prompt, prompt, self.model, self.temperature)
            if cached is not None and "raw_text" in cached:
                return cached["raw_text"]

        system = [
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
        ]
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=self.temperature,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if self._response_cache is not None:
            self._response_cache.put(
                system_prompt, prompt, self.model, self.temperature, {"raw_text": text}
            )
        return text
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/equities/test_llm_client.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/equities/agents/llm_client.py tests/unit/equities/test_llm_client.py
git commit -m "feat(ranking): invoke_raw LLM client method for non-analyst responses"
```

### Task 10: Decile math + `DeterministicRanker`

**Files:**
- Create: `app/modules/equities/agents/ranker.py`
- Test: `tests/unit/equities/test_ranker.py`

- [ ] **Step 1: Write the failing tests**

```python
"""tests/unit/equities/test_ranker.py"""

from app.modules.equities.agents.ranker import DeterministicRanker, decile_score
from app.modules.equities.models import StockSignal


def _sig(symbol, score, conf=5):
    return StockSignal(
        symbol=symbol, analyst_type="fundamentals",
        bullish_score=score, confidence=conf, summary="t",
    )


class TestDecileScore:
    def test_spread_n20(self):
        assert decile_score(0, 20) == 10
        assert decile_score(19, 20) == 1
        assert decile_score(10, 20) == 5

    def test_spread_n7(self):
        assert decile_score(0, 7) == 10
        assert decile_score(6, 7) == 1
        assert decile_score(3, 7) == 5

    def test_n1_is_10(self):
        assert decile_score(0, 1) == 10


class TestDeterministicRanker:
    async def test_rank_normalizes_scores_preserving_order(self):
        ranker = DeterministicRanker(min_rank_universe=2)
        signals = [_sig("A", 7), _sig("B", 5), _sig("C", 6)]
        ranked = await ranker.rank(signals)
        by_symbol = {s.symbol: s.bullish_score for s in ranked}
        assert by_symbol["A"] == 10 and by_symbol["C"] == 5 and by_symbol["B"] == 1
        # confidence and summary preserved
        assert all(s.confidence == 5 and s.summary == "t" for s in ranked)

    async def test_ties_broken_by_symbol_for_determinism(self):
        ranker = DeterministicRanker(min_rank_universe=2)
        ranked = await ranker.rank([_sig("B", 5), _sig("A", 5)])
        assert [s.symbol for s in ranked] == ["A", "B"]
        assert ranked[0].bullish_score == 10 and ranked[1].bullish_score == 1

    async def test_below_min_universe_passthrough(self):
        ranker = DeterministicRanker(min_rank_universe=5)
        signals = [_sig("A", 7), _sig("B", 3)]
        ranked = await ranker.rank(signals)
        assert {s.bullish_score for s in ranked} == {7, 3}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/equities/test_ranker.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

Create `app/modules/equities/agents/ranker.py`:

```python
"""Cross-sectional ranking — stage 2 of analyst scoring (Phase B).

Stage 1 produces per-stock signals; rankers re-map bullish_score to a forced
decile based on rank across the screened set. DeterministicRanker (sort by
stage-1 score) serves backtests/quant mode; CrossSectionalRanker (LLM call)
serves production.
"""

from __future__ import annotations

import logging

from app.modules.equities.models import StockSignal

logger = logging.getLogger(__name__)

DEFAULT_MIN_RANK_UNIVERSE = 5


def decile_score(i: int, n: int) -> int:
    """Map 0-indexed rank position to a 1-10 score, best=10, worst=1."""
    if n <= 1:
        return 10
    return 1 + ((n - 1 - i) * 9) // (n - 1)


def apply_ranking(signals: list[StockSignal], ordered_symbols: list[str]) -> list[StockSignal]:
    """Return new signals with bullish_score = decile of position in ordered_symbols.

    Symbols absent from ordered_symbols keep their stage-1 score.
    """
    position = {sym: i for i, sym in enumerate(ordered_symbols)}
    n = len(ordered_symbols)
    out = []
    for sig in signals:
        i = position.get(sig.symbol)
        if i is None:
            out.append(sig)
        else:
            out.append(sig.model_copy(update={"bullish_score": decile_score(i, n)}))
    return out


class DeterministicRanker:
    """Rank-normalize by stage-1 score (ties broken alphabetically).

    Gives quant-mode backtests the same decile score semantics as the LLM
    ranker without any LLM call.
    """

    def __init__(self, min_rank_universe: int = DEFAULT_MIN_RANK_UNIVERSE) -> None:
        self.min_rank_universe = min_rank_universe

    async def rank(self, signals: list[StockSignal]) -> list[StockSignal]:
        if len(signals) < self.min_rank_universe:
            return signals
        ordered = sorted(signals, key=lambda s: (-s.bullish_score, s.symbol))
        return apply_ranking(signals, [s.symbol for s in ordered])
```

Note: `DeterministicRanker.rank` returns signals re-scored but in the input list's original order only for unranked symbols; `apply_ranking` preserves the input order of `signals`. The test asserts membership/scores, not order — but `test_ties_broken_by_symbol_for_determinism` asserts output order `["A", "B"]` because the input order is `[B, A]` and `apply_ranking` preserves input order. Fix the test OR the code so they agree: keep `apply_ranking` order-preserving and change that assertion to check scores by symbol:

```python
        by = {s.symbol: s.bullish_score for s in ranked}
        assert by == {"A": 10, "B": 1}
```

(Use this corrected assertion when writing the test in Step 1.)

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/equities/test_ranker.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/equities/agents/ranker.py tests/unit/equities/test_ranker.py
git commit -m "feat(ranking): decile mapping and deterministic ranker"
```

### Task 11: Ranking prompt + loader support + confidence redefinition

**Files:**
- Create: `app/modules/equities/agents/skills/base/ranking.md`
- Modify: `app/modules/equities/agents/skills/loader.py`, `app/modules/equities/agents/skills/output_format.md`
- Test: `tests/unit/equities/test_skill_loader.py` (append)

- [ ] **Step 1: Write the failing test** (append to `test_skill_loader.py`; mirror the file's existing fixture style for temp skills dirs if present)

```python
from app.modules.equities.agents.skills.loader import compose_ranking_prompt


def test_compose_ranking_prompt_loads_base_ranking_skill():
    prompt = compose_ranking_prompt("news", "growth")
    assert "rank" in prompt.lower()
    # must NOT append the per-stock output format (different response schema)
    assert "bullish_score" not in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/equities/test_skill_loader.py -q -k ranking`
Expected: FAIL with `ImportError: cannot import name 'compose_ranking_prompt'`

- [ ] **Step 3: Create the ranking skill** at `skills/base/ranking.md`:

```markdown
# Cross-Sectional Ranking

You are the final ranking stage for a team of {analyst_type} analysts at a
systematic hedge fund. You receive one thesis per stock, produced moments ago
by per-stock analysis. Your job is to force a strict ordering: which of these
stocks has the MOST attractive {analyst_type} picture right now, which the
least, and everything in between.

## Rules

1. Rank ALL symbols you are given. Every symbol appears exactly once.
2. No clustering escape hatch: this is a forced ranking. Two stocks may feel
   similar — rank them anyway using any defensible distinction (magnitude of
   catalyst, durability, risk).
3. Judge only the {analyst_type} dimension described in the theses. Do not
   import outside knowledge of price targets or other analysts' views.
4. The provisional scores you see came from analysts working one stock at a
   time without seeing the others — treat them as a hint, not an anchor.
   Re-order freely when theses warrant it.

## Output

Respond ONLY with a JSON object:
{"ranking": ["BEST_SYMBOL", "NEXT", ..., "WORST_SYMBOL"]}

No text outside the JSON object.
```

- [ ] **Step 4: Add `compose_ranking_prompt` to `loader.py`** (alongside `compose_system_prompt`, using the same `_read_skill`, `_normalize_branch`, `_SKILLS_DIR`, `_SEPARATOR` helpers):

```python
@lru_cache(maxsize=32)
def compose_ranking_prompt(
    analyst_type: str,
    branch_name: str = "",
    skills_dir: Path | None = None,
) -> str:
    """System prompt for the cross-sectional ranking stage.

    Layers base/ranking.md + branches/<branch>/ranking.md (optional), with
    {analyst_type} placeholders substituted. Deliberately does NOT append
    output_format.md — the ranking response schema is defined in ranking.md.
    """
    root = skills_dir if skills_dir is not None else _SKILLS_DIR
    layers: list[str] = []
    base = _read_skill(root / "base" / "ranking.md")
    if base:
        layers.append(base)
    else:
        logger.warning("Missing base ranking skill")
    if branch_name:
        overlay = _read_skill(root / "branches" / _normalize_branch(branch_name) / "ranking.md")
        if overlay:
            layers.append(overlay)
    return _SEPARATOR.join(layers).replace("{analyst_type}", analyst_type)
```

- [ ] **Step 5: Redefine confidence in `output_format.md`** — replace the line describing `confidence` with:

```markdown
- "confidence": integer 1-10 — the likelihood your directional call resolves
  correctly within ~1 month. 1 = coin flip, 5 = modest edge, 10 = near
  certainty backed by concrete, dated evidence. Score your evidence, not your
  conviction: vague positives are low confidence.
```

(Adjust to match the file's existing format — read it first; only the confidence definition changes.)

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/equities/test_skill_loader.py -q`
Expected: PASS (all)

- [ ] **Step 7: Commit**

```bash
git add app/modules/equities/agents/skills/ tests/unit/equities/test_skill_loader.py
git commit -m "feat(ranking): ranking skill prompt, loader support, confidence redefinition"
```

### Task 12: `CrossSectionalRanker` (LLM)

**Files:**
- Modify: `app/modules/equities/agents/ranker.py` (append)
- Test: `tests/unit/equities/test_ranker.py` (append)

- [ ] **Step 1: Write the failing tests** (append)

```python
import json
from unittest.mock import AsyncMock, MagicMock

from app.modules.equities.agents.ranker import CrossSectionalRanker


def _llm(returning: str):
    client = MagicMock()
    client.invoke_raw = AsyncMock(return_value=returning)
    return client


class TestCrossSectionalRanker:
    async def test_reorders_scores_by_llm_ranking(self):
        signals = [_sig("A", 5), _sig("B", 5), _sig("C", 5), _sig("D", 5), _sig("E", 5)]
        llm = _llm(json.dumps({"ranking": ["C", "A", "E", "B", "D"]}))
        ranker = CrossSectionalRanker(llm, analyst_type="news", branch_name="growth")
        ranked = await ranker.rank(signals)
        by = {s.symbol: s.bullish_score for s in ranked}
        assert by["C"] == 10 and by["D"] == 1
        assert by["A"] > by["E"] > by["B"]

    async def test_symbols_missing_from_ranking_keep_stage1_score(self):
        signals = [_sig("A", 7), _sig("B", 6), _sig("C", 5), _sig("D", 4), _sig("E", 3)]
        llm = _llm(json.dumps({"ranking": ["B", "A", "D", "C"]}))  # E omitted
        ranker = CrossSectionalRanker(llm, analyst_type="news", branch_name="growth")
        ranked = await ranker.rank(signals)
        by = {s.symbol: s.bullish_score for s in ranked}
        assert by["E"] == 3  # untouched
        assert by["B"] == 10

    async def test_hallucinated_symbols_ignored(self):
        signals = [_sig(s, 5) for s in "ABCDE"]
        llm = _llm(json.dumps({"ranking": ["A", "ZZZ", "B", "C", "D", "E"]}))
        ranker = CrossSectionalRanker(llm, analyst_type="news", branch_name="growth")
        ranked = await ranker.rank(signals)
        by = {s.symbol: s.bullish_score for s in ranked}
        assert by["A"] == 10 and by["E"] == 1 and "ZZZ" not in by

    async def test_llm_error_falls_back_to_stage1(self):
        signals = [_sig(s, 6) for s in "ABCDE"]
        llm = MagicMock()
        llm.invoke_raw = AsyncMock(side_effect=RuntimeError("api down"))
        ranker = CrossSectionalRanker(llm, analyst_type="news", branch_name="growth")
        ranked = await ranker.rank(signals)
        assert all(s.bullish_score == 6 for s in ranked)

    async def test_unparseable_response_falls_back(self):
        signals = [_sig(s, 6) for s in "ABCDE"]
        ranker = CrossSectionalRanker(_llm("sorry, I cannot"), analyst_type="news", branch_name="growth")
        ranked = await ranker.rank(signals)
        assert all(s.bullish_score == 6 for s in ranked)

    async def test_below_min_universe_skips_llm(self):
        signals = [_sig("A", 7), _sig("B", 3)]
        llm = _llm("{}")
        ranker = CrossSectionalRanker(llm, analyst_type="news", branch_name="growth")
        ranked = await ranker.rank(signals)
        llm.invoke_raw.assert_not_called()
        assert {s.bullish_score for s in ranked} == {7, 3}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/equities/test_ranker.py -q -k CrossSectional`
Expected: FAIL with `ImportError: cannot import name 'CrossSectionalRanker'`

- [ ] **Step 3: Implement** (append to `ranker.py`)

```python
import json
from pathlib import Path

from app.modules.equities.agents.skills.loader import compose_ranking_prompt


class CrossSectionalRanker:
    """One LLM call that force-ranks all stage-1 theses best-to-worst."""

    def __init__(
        self,
        llm_client,
        *,
        analyst_type: str,
        branch_name: str,
        skills_dir: Path | None = None,
        min_rank_universe: int = DEFAULT_MIN_RANK_UNIVERSE,
    ) -> None:
        self.llm_client = llm_client
        self.analyst_type = analyst_type
        self.branch_name = branch_name
        self.skills_dir = skills_dir
        self.min_rank_universe = min_rank_universe

    async def rank(self, signals: list[StockSignal]) -> list[StockSignal]:
        if len(signals) < self.min_rank_universe:
            return signals
        try:
            system_prompt = compose_ranking_prompt(
                self.analyst_type, self.branch_name, self.skills_dir
            )
            lines = [
                f"- {s.symbol} (provisional score {s.bullish_score}): {s.summary}"
                for s in sorted(signals, key=lambda s: s.symbol)
            ]
            prompt = (
                f"Theses for {len(signals)} stocks:\n\n"
                + "\n".join(lines)
                + "\n\nRank ALL of these symbols best to worst."
            )
            text = await self.llm_client.invoke_raw(prompt, system_prompt=system_prompt)
            ordered = self._parse_ranking(text, {s.symbol for s in signals})
            if not ordered:
                logger.warning("%s ranker: unusable ranking response — stage-1 scores kept",
                               self.analyst_type)
                return signals
            return apply_ranking(signals, ordered)
        except Exception:
            logger.warning("%s ranker failed — stage-1 scores kept",
                           self.analyst_type, exc_info=True)
            return signals

    @staticmethod
    def _parse_ranking(text: str, valid_symbols: set[str]) -> list[str]:
        cleaned = text.strip()
        fence = chr(96) * 3
        if cleaned.startswith(fence):
            cleaned = "\n".join(
                ln for ln in cleaned.split("\n") if not ln.strip().startswith(fence)
            ).strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            return []
        ranking = parsed.get("ranking")
        if not isinstance(ranking, list):
            return []
        seen: set[str] = set()
        ordered = []
        for sym in ranking:
            if isinstance(sym, str) and sym in valid_symbols and sym not in seen:
                ordered.append(sym)
                seen.add(sym)
        return ordered
```

(Imports go to the top of the file per ruff.)

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/equities/test_ranker.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add app/modules/equities/agents/ranker.py tests/unit/equities/test_ranker.py
git commit -m "feat(ranking): LLM cross-sectional ranker with strict parsing and fallbacks"
```

### Task 13: Graph + service wiring

**Files:**
- Modify: `app/modules/equities/agents/graph.py` (three analyst nodes), `app/modules/equities/service.py` (deps construction)
- Test: `tests/unit/equities/test_graph.py` (append to `TestGraphEndToEnd`)

- [ ] **Step 1: Write the failing test** (append to `TestGraphEndToEnd` in `test_graph.py`)

```python
    async def test_rankers_applied_after_each_analyst(self):
        state, _ = self._initial_state([], n_orders=0)
        ranked_marker = [
            StockSignal(symbol="AAPL", analyst_type="news", bullish_score=10,
                        confidence=5, summary="ranked")
        ]
        ranker = MagicMock()
        ranker.rank = AsyncMock(return_value=ranked_marker)
        state["deps"]["rankers"] = {"news": ranker, "fundamentals": ranker, "technical": ranker}

        graph = build_equities_graph("growth")
        result = await graph.ainvoke(state)

        assert ranker.rank.await_count == 3
        assert result["signals"] == ranked_marker * 3
```

Add `StockSignal` to the test file's imports from `app.modules.equities.models`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/equities/test_graph.py -q -k rankers`
Expected: FAIL — `ranker.rank.await_count == 0`

- [ ] **Step 3: Wire rankers in the graph**

In `graph.py`, each of the three analyst paths gains the same two lines after `analyze_batch`. For `_run_news_analysis`:

```python
    signals = await analyst.analyze_batch(
        screened,
        articles_by_symbol=articles_by_symbol,
        max_concurrent=max_concurrent,
    )
    ranker = deps.get("rankers", {}).get("news")
    if ranker is not None:
        signals = await ranker.rank(list(signals))
    logger.info("News analyst produced %d signals", len(signals))
    return {"signals": list(signals)}
```

For `fundamentals_analysis` and `technical_analysis` nodes, identically with keys `"fundamentals"` / `"technical"`:

```python
        signals = await analyst.analyze_batch(state["screened"], max_concurrent=max_concurrent)
        ranker = deps.get("rankers", {}).get("fundamentals")
        if ranker is not None:
            signals = await ranker.rank(list(signals))
        logger.info("Fundamentals analyst produced %d signals", len(signals))
        return {"signals": list(signals)}
```

- [ ] **Step 4: Build rankers in the service**

In `service.py` `run_pipeline`, before `initial_state` is built, add:

```python
        # --- Phase B: cross-sectional ranking stage ---
        # LLM ranker when the analyst exposes an LLM client (production and
        # LLM-mode backtests, including CachedAnalystWrapper-wrapped analysts);
        # deterministic rank-normalization otherwise (quant backtests).
        def _build_ranker(analyst, a_type: str):
            if analyst is None:
                return None
            llm = getattr(analyst, "llm_client", None) or getattr(
                getattr(analyst, "_analyst", None), "llm_client", None
            )
            if llm is not None and hasattr(llm, "invoke_raw"):
                skills_dir = getattr(analyst, "skills_dir", None) or getattr(
                    getattr(analyst, "_analyst", None), "skills_dir", None
                )
                return CrossSectionalRanker(
                    llm, analyst_type=a_type, branch_name=branch_name, skills_dir=skills_dir
                )
            return DeterministicRanker()

        rankers = {
            "news": _build_ranker(self.news_analyst, "news"),
            "fundamentals": _build_ranker(self.fundamentals_analyst, "fundamentals"),
            "technical": _build_ranker(self.technical_analyst, "technical"),
        }
        rankers = {k: v for k, v in rankers.items() if v is not None}
```

and add `"rankers": rankers,` to the `deps` dict in `initial_state`. Import at top:

```python
from app.modules.equities.agents.ranker import CrossSectionalRanker, DeterministicRanker
```

- [ ] **Step 5: Run the full equities suite**

Run: `.venv/bin/python -m pytest tests/unit/equities/ -q`
Expected: PASS. Note: `TestGraphEndToEnd`'s other tests build deps without `"rankers"`, so they are unaffected. Backtest unit tests must also pass: `.venv/bin/python -m pytest tests/unit/backtest/ -q` — quant analysts now flow through `DeterministicRanker`, which may change expected scores in any backtest test that asserts specific signal values; update those assertions to the decile-normalized values (the change is intentional, per spec).

- [ ] **Step 6: Commit**

```bash
git add app/modules/equities/agents/graph.py app/modules/equities/service.py tests/unit/equities/test_graph.py tests/unit/backtest/
git commit -m "feat(ranking): wire cross-sectional rankers into pipeline graph"
```

### Task 14: Full verification + deploy

- [ ] **Step 1: Full unit suite**

Run: `.venv/bin/python -m pytest tests/unit/ -q`
Expected: ALL PASS

- [ ] **Step 2: Lint + format**

Run: `ruff check app/ tests/ scripts/ && ruff format --check app/modules/equities/ tests/unit/equities/ scripts/`
Expected: clean (format only the files this plan touched)

- [ ] **Step 3: Smoke the pipeline against local DB with tiny universe** (optional but recommended; needs ANTHROPIC_API_KEY and local Docker DB seeded per CLAUDE.md)

Run: `.venv/bin/python -m scripts.run_weekly_pipeline --branches growth --top-n 5 --dry-run`
Expected: exit 0, dry-run digest printed

- [ ] **Step 4: Ask the user before pushing to main** (per project convention). After approval:

```bash
git push origin main
```

Expected: next Monday's scheduled run uses new weights, hysteresis, ranking, and produces the first in-pipeline attribution report.

---

## Self-review notes

- Spec coverage: Phase D tasks 1–6 (model/migration, pure math, engine, digest, CLI, backfill); Phase A tasks 7–8 (weights, hysteresis incl. graph wiring); Phase B tasks 9–13 (invoke_raw, deterministic ranker + decile math, prompts/loader, LLM ranker, wiring incl. backtest parity via DeterministicRanker). Verification task 14.
- The spec's "CachedAnalystWrapper must delegate through analyze_batch" check resolves cleanly: ranking lives in the graph nodes downstream of any wrapper, so both wrapped and unwrapped analysts get ranked; the wrapper's per-(date,symbol) cache stores stage-1 signals only, which is correct (ranking is re-derived, and reproducible via the LLM response cache).
- Type consistency: `AttributionReport` fields match the model columns and digest accessors; `rank(signals) -> list[StockSignal]` is the shared ranker interface; `select_stocks(scores, current_holdings=None)` matches graph call.

# Investor Reporting Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the fund a true market-valued NAV series, an inception baseline for "+/− since $1M", an investor-grade weekly report, and cash-correct execution.

**Architecture:** Mark-to-market lives in `PortfolioService` (mirrors backtest `_mark_to_market`, but unpriced positions keep cost basis). Snapshots gain `positions_detail` (existing unused JSONB column). The weekly CLI wires MTM + snapshot + report after trading in a dedicated session (attribution pattern — never fails the run). A daily GH cron snapshots EOD. A backfill script reconstructs history from `trades` + Yahoo closes with a cash-match validation gate. Cash fix: sells-first ordering, 1% sizing buffer, fill-time overdraft rejection in `TradeExecutionService`.

**Tech Stack:** Python 3.12, SQLAlchemy 2 async, Pydantic, pytest (asyncio_mode=auto), GitHub Actions, yfinance via existing `DataPlatformService`.

**Spec:** `docs/superpowers/specs/2026-07-15-investor-reporting-foundation-design.md`

**⚠️ Commit policy override (user instruction):** Do NOT run `git commit`. Every "Commit" step below is replaced by `git add <files>` only — the user reviews and commits. This overrides the default TDD commit cadence.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `app/common/models/portfolio.py` | Modify | `MarkToMarketResult`; `PortfolioSnapshot.positions_detail` |
| `app/common/interfaces/repositories.py` | Modify | `SnapshotRepository.create(..., positions_detail)`, `latest_by_branch` |
| `app/modules/portfolio/service.py` | Modify | `mark_to_market`, `take_snapshot(positions_detail)`, fund-summary derived metrics |
| `app/modules/portfolio/repository.py` | Modify | Persist/map positions_detail; `latest_by_branch`; inception_date in fund summary |
| `app/modules/backtest/state.py` | Modify | InMemory repo: new kwarg + `latest_by_branch` |
| `app/modules/equities/config.py` | Modify | `PortfolioConfig.cash_buffer_pct` |
| `app/modules/equities/agents/portfolio_manager.py` | Modify | Buffer in `size_positions`; sells-first in `generate_orders` |
| `app/modules/trade_execution/service.py` | Modify | Fill-time insufficient-cash rejection |
| `app/modules/equities/weekly_runner.py` | Modify | `PortfolioReport`, `build_portfolio_report`, `ny_date`, digest sections |
| `scripts/common.py` | Create | Shared `init_data_platform`, `resolve_branch_id` |
| `scripts/run_weekly_pipeline.py` | Modify | Post-run MTM/snapshot/report wiring; `--report-dir` |
| `scripts/take_daily_snapshot.py` | Create | Daily EOD MTM + snapshot (idempotent) |
| `scripts/backfill_snapshots.py` | Create | Historical reconstruction + validation gate |
| `scripts/build_report_json.py` | Create | Regenerate `scheduled_run_results/report.json` from DB |
| `.github/workflows/daily-snapshot.yml` | Create | Weekday EOD snapshot cron |
| `.github/workflows/weekly-rebalance.yml` | Modify | permissions + report + auto-commit step |
| `CLAUDE.md` | Modify | Seed SQL, new commands, gotchas |

Tests (new files, self-contained fakes): `tests/unit/test_mark_to_market.py`, `tests/unit/test_snapshot_detail.py`, `tests/unit/test_fund_summary_returns.py`, `tests/unit/equities/test_order_generation_cash.py`, `tests/unit/test_trade_execution_cash_check.py`, `tests/unit/equities/test_digest_portfolio_report.py`, `tests/unit/test_backfill_reconstruction.py`, `tests/unit/test_report_json_helpers.py`.

---

### Task 1: `MarkToMarketResult` + `PortfolioService.mark_to_market`

**Files:**
- Modify: `app/common/models/portfolio.py`
- Modify: `app/modules/portfolio/service.py`
- Test: `tests/unit/test_mark_to_market.py`

- [ ] **Step 1.1: Write the failing tests**

```python
"""Tests for PortfolioService.mark_to_market."""

from datetime import UTC, datetime

import pytest

from app.common.models.portfolio import PortfolioSummary
from app.common.models.position import Position
from app.modules.portfolio.service import PortfolioService


def _summary(**over):
    base = dict(
        id="pf-1", branch_id="b-1", branch_type="equities",
        cash=10_000.0, allocated_capital=1_000_000.0,
        margin_requirement=0.0, margin_used=0.0,
        nav=110_000.0, total_long_exposure=100_000.0, total_short_exposure=0.0,
        gross_exposure=100_000.0, net_exposure=100_000.0,
        unrealized_pnl=0.0, realized_pnl=500.0,
        updated_at=datetime.now(UTC),
    )
    base.update(over)
    return PortfolioSummary(**base)


def _position(symbol, qty, cost):
    return Position(
        id=f"pos-{symbol}", portfolio_id="pf-1", instrument_id=f"in-{symbol}",
        symbol=symbol, long_quantity=qty, long_cost_basis=cost,
        short_quantity=0.0, short_cost_basis=0.0, short_margin_used=0.0,
        realized_pnl_long=0.0, realized_pnl_short=0.0,
        updated_at=datetime.now(UTC),
    )


class FakePortfolioRepo:
    def __init__(self, summary):
        self.summary = summary
        self.updated_fields = None

    async def get_by_branch(self, branch_id):
        return self.summary

    async def update_portfolio_fields(self, branch_id, **fields):
        self.updated_fields = fields


class FakePositionRepo:
    def __init__(self, positions):
        self.positions = positions

    async def get_by_portfolio(self, portfolio_id):
        return self.positions


class FakeEventLog:
    def __init__(self):
        self.events = []

    async def append(self, event):
        self.events.append(event)


def _service(summary, positions):
    return PortfolioService(
        portfolio_repo=FakePortfolioRepo(summary),
        position_repo=FakePositionRepo(positions),
        snapshot_repo=None,
        event_log=FakeEventLog(),
    )


async def test_mark_to_market_values_positions_at_price():
    svc = _service(_summary(), [_position("AAA", 100, 10_000.0), _position("BBB", 50, 5_000.0)])
    result = await svc.mark_to_market("b-1", {"AAA": 120.0, "BBB": 90.0})
    # AAA mv=12000, BBB mv=4500 → total 16500; nav = 10000 + 16500
    assert result.nav == pytest.approx(26_500.0)
    assert result.unrealized_pnl == pytest.approx(16_500.0 - 15_000.0)
    assert result.priced == 2 and result.unpriced == 0
    fields = svc.portfolio_repo.updated_fields
    assert fields["nav"] == pytest.approx(26_500.0)
    assert fields["total_long_exposure"] == pytest.approx(16_500.0)
    assert fields["unrealized_pnl"] == pytest.approx(1_500.0)


async def test_mark_to_market_unpriced_position_keeps_cost_basis():
    svc = _service(_summary(), [_position("AAA", 100, 10_000.0), _position("BAD", 10, 2_000.0)])
    result = await svc.mark_to_market("b-1", {"AAA": 120.0, "BAD": None})
    # BAD carried at cost 2000 → mv total 14000
    assert result.nav == pytest.approx(10_000.0 + 14_000.0)
    assert result.unpriced == 1
    bad = next(d for d in result.positions_detail if d["symbol"] == "BAD")
    assert bad["market_value"] == pytest.approx(2_000.0)
    assert bad["price"] is None


async def test_mark_to_market_detail_sorted_and_weighted():
    svc = _service(_summary(), [_position("AAA", 100, 10_000.0), _position("BBB", 50, 5_000.0)])
    result = await svc.mark_to_market("b-1", {"AAA": 120.0, "BBB": 400.0})
    # BBB mv=20000 > AAA mv=12000; nav = 10000+32000 = 42000
    assert [d["symbol"] for d in result.positions_detail] == ["BBB", "AAA"]
    assert result.positions_detail[0]["weight"] == pytest.approx(20_000.0 / 42_000.0)


async def test_mark_to_market_logs_event_with_trigger():
    svc = _service(_summary(), [_position("AAA", 100, 10_000.0)])
    await svc.mark_to_market("b-1", {"AAA": 100.0})
    events = svc.event_log.events
    assert len(events) == 1
    assert events[0].trigger == "mark_to_market"


async def test_mark_to_market_no_portfolio_raises():
    svc = PortfolioService(
        portfolio_repo=FakePortfolioRepo(None),
        position_repo=FakePositionRepo([]),
        snapshot_repo=None,
        event_log=FakeEventLog(),
    )
    with pytest.raises(ValueError):
        await svc.mark_to_market("b-x", {})
```

- [ ] **Step 1.2: Run to verify failure**

Run: `pytest tests/unit/test_mark_to_market.py -q`
Expected: FAIL — `AttributeError: 'PortfolioService' object has no attribute 'mark_to_market'`

- [ ] **Step 1.3: Implement**

In `app/common/models/portfolio.py`, append:

```python
class MarkToMarketResult(BaseModel):
    """Result of repricing a portfolio's open positions at market."""

    nav: float
    cash: float
    unrealized_pnl: float
    realized_pnl: float
    priced: int
    unpriced: int
    # Per position, sorted by market_value desc:
    # {symbol, quantity, price (None if unpriced), market_value, cost_basis,
    #  unrealized_pnl, weight}
    positions_detail: list[dict] = []
```

In `app/modules/portfolio/service.py`: add `import logging`, `logger = logging.getLogger(__name__)` at module top; extend the models import to include `MarkToMarketResult`; add method to `PortfolioService`:

```python
    async def mark_to_market(self, branch_id: str, prices: dict[str, float | None]) -> MarkToMarketResult:
        """Reprice open long positions and persist marked NAV/exposure/unrealized P&L.

        A position with no usable price keeps its cost basis (a live data outage
        must not crater reported NAV — unlike the backtest's zero-out rule).
        """
        summary = await self.portfolio_repo.get_by_branch(branch_id)
        if summary is None:
            raise ValueError(f"No portfolio for branch {branch_id}")

        positions = await self.position_repo.get_by_portfolio(summary.id)
        open_positions = [p for p in positions if p.long_quantity > 0]

        total_mv = 0.0
        total_cost = 0.0
        unpriced = 0
        detail: list[dict] = []
        for p in open_positions:
            price = prices.get(p.symbol)
            if price is None or price <= 0:
                unpriced += 1
                logger.warning("No price for %s — carrying at cost basis", p.symbol)
                price = None
                mv = p.long_cost_basis
            else:
                mv = price * p.long_quantity
            total_mv += mv
            total_cost += p.long_cost_basis
            detail.append(
                {
                    "symbol": p.symbol,
                    "quantity": p.long_quantity,
                    "price": price,
                    "market_value": mv,
                    "cost_basis": p.long_cost_basis,
                    "unrealized_pnl": mv - p.long_cost_basis,
                }
            )

        nav = float(summary.cash) + total_mv
        unrealized = total_mv - total_cost
        for d in detail:
            d["weight"] = d["market_value"] / nav if nav > 0 else 0.0
        detail.sort(key=lambda d: d["market_value"], reverse=True)

        await self.portfolio_repo.update_portfolio_fields(
            branch_id,
            nav=nav,
            total_long_exposure=total_mv,
            unrealized_pnl=unrealized,
        )
        await self.event_log.append(
            PortfolioUpdatedEvent(
                source="portfolio_service",
                portfolio_id=summary.id,
                branch_id=branch_id,
                trigger="mark_to_market",
                cash=float(summary.cash),
                nav=nav,
                margin_used=float(summary.margin_used),
                total_long_exposure=total_mv,
                total_short_exposure=float(summary.total_short_exposure),
                unrealized_pnl=unrealized,
                realized_pnl=float(summary.realized_pnl),
            )
        )
        return MarkToMarketResult(
            nav=nav,
            cash=float(summary.cash),
            unrealized_pnl=unrealized,
            realized_pnl=float(summary.realized_pnl),
            priced=len(open_positions) - unpriced,
            unpriced=unpriced,
            positions_detail=detail,
        )
```

- [ ] **Step 1.4: Run to verify pass**

Run: `pytest tests/unit/test_mark_to_market.py -q` → all PASS.
Also: `pytest tests/unit/test_portfolio_service.py -q` (no regressions).

- [ ] **Step 1.5: Stage**

`git add app/common/models/portfolio.py app/modules/portfolio/service.py tests/unit/test_mark_to_market.py`

- [ ] **Step 1.6: Writer reconciliation + edge tests (added by Task-1 quality review)**

`app/modules/portfolio/repository.py` `update_cash`: NAV recomputation becomes
`new_cash + total_long_exposure − total_short_exposure` (drop `+ unrealized_pnl` —
exposure is market-valued after marks, so the old formula double-counts).
`app/modules/portfolio/service.py` `handle_trade_executed`: pass `unrealized_pnl=0.0`
in its `update_portfolio_fields` call (it reverts exposure to cost basis, so a stale
marked unrealized value must not linger). `mark_to_market` detail sort key becomes
`(-market_value, symbol)` for deterministic ties. Add tests: empty-portfolio no-op
(nav == cash, no positions detail), zero/negative price treated as unpriced,
nav ≤ 0 → weights 0.0, event payload nav/unrealized match result. Stage the same
files plus `app/modules/portfolio/repository.py`.

---

### Task 2: Snapshot `positions_detail` + `latest_by_branch`

**Files:**
- Modify: `app/common/interfaces/repositories.py` (SnapshotRepository)
- Modify: `app/common/models/portfolio.py` (PortfolioSnapshot)
- Modify: `app/modules/portfolio/repository.py` (PostgresSnapshotRepository)
- Modify: `app/modules/portfolio/service.py` (take_snapshot passthrough)
- Modify: `app/modules/backtest/state.py` (InMemorySnapshotRepository)
- Test: `tests/unit/test_snapshot_detail.py`

- [ ] **Step 2.1: Write the failing tests**

```python
"""Snapshot positions_detail passthrough + InMemory latest_by_branch."""

from datetime import UTC, datetime, timedelta

from app.modules.backtest.state import InMemorySnapshotRepository


async def test_inmemory_create_stores_positions_detail():
    repo = InMemorySnapshotRepository()
    detail = [{"symbol": "AAA", "weight": 0.5}]
    snap = await repo.create("pf-1", "b-1", positions_detail=detail)
    assert snap.positions_detail == detail


async def test_inmemory_create_without_detail_defaults_none():
    repo = InMemorySnapshotRepository()
    snap = await repo.create("pf-1", "b-1")
    assert snap.positions_detail is None


async def test_latest_by_branch_returns_most_recent():
    repo = InMemorySnapshotRepository()
    s1 = await repo.create("pf-1", "b-1")
    s2 = await repo.create("pf-1", "b-1")
    s1_at = datetime.now(UTC) - timedelta(days=7)
    repo._store[0].snapshot_at = s1_at  # backdate first snapshot
    latest = await repo.latest_by_branch("b-1")
    assert latest.id == s2.id


async def test_latest_by_branch_before_excludes_recent():
    repo = InMemorySnapshotRepository()
    s1 = await repo.create("pf-1", "b-1")
    await repo.create("pf-1", "b-1")
    cutoff = datetime.now(UTC) - timedelta(days=1)
    repo._store[0].snapshot_at = cutoff - timedelta(days=6)
    latest = await repo.latest_by_branch("b-1", before=cutoff)
    assert latest is not None and latest.id == s1.id


async def test_latest_by_branch_none_when_empty():
    repo = InMemorySnapshotRepository()
    assert await repo.latest_by_branch("b-1") is None
```

- [ ] **Step 2.2: Run to verify failure**

Run: `pytest tests/unit/test_snapshot_detail.py -q`
Expected: FAIL — `TypeError: create() got an unexpected keyword argument 'positions_detail'`

- [ ] **Step 2.3: Implement**

`app/common/models/portfolio.py` — add to `PortfolioSnapshot` (after `top_holdings`):

```python
    # Per-position detail captured at snapshot time (symbol, quantity, price,
    # market_value, cost_basis, unrealized_pnl, weight). None for legacy rows.
    positions_detail: list[dict] | None = None
```

`app/common/interfaces/repositories.py` — replace `SnapshotRepository` (add `datetime` to the existing imports at the top of the file if absent):

```python
class SnapshotRepository(ABC):
    @abstractmethod
    async def create(
        self, portfolio_id: str, branch_id: str, positions_detail: list[dict] | None = None
    ) -> PortfolioSnapshot: ...

    @abstractmethod
    async def list_by_branch(
        self, branch_id: str, limit: int = 30, offset: int = 0
    ) -> tuple[list[PortfolioSnapshot], int]: ...

    @abstractmethod
    async def latest_by_branch(
        self, branch_id: str, before: datetime | None = None
    ) -> PortfolioSnapshot | None: ...
```

`app/modules/portfolio/repository.py` — `PostgresSnapshotRepository.create` gains the kwarg; pass through to the model and domain mapping:

```python
    async def create(
        self, portfolio_id: str, branch_id: str, positions_detail: list[dict] | None = None
    ) -> PortfolioSnapshot:
        ...  # unchanged body until PortfolioSnapshotModel(...)
        snapshot = PortfolioSnapshotModel(
            ...,  # existing fields unchanged
            position_count=pos_count,
            positions_detail=positions_detail,
        )
```

`_to_domain` adds `positions_detail=model.positions_detail,`. New method:

```python
    async def latest_by_branch(
        self, branch_id: str, before: datetime | None = None
    ) -> PortfolioSnapshot | None:
        stmt = (
            select(PortfolioSnapshotModel)
            .where(PortfolioSnapshotModel.branch_id == branch_id)
            .order_by(PortfolioSnapshotModel.snapshot_at.desc())
            .limit(1)
        )
        if before is not None:
            stmt = stmt.where(PortfolioSnapshotModel.snapshot_at < before)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        return self._to_domain(row) if row else None
```

`app/modules/portfolio/service.py` — `take_snapshot` signature becomes
`async def take_snapshot(self, branch_id: str, positions_detail: list[dict] | None = None)`,
and the repo call becomes `await self.snapshot_repo.create(summary.id, branch_id, positions_detail=positions_detail)`.

`app/modules/backtest/state.py` — `InMemorySnapshotRepository.create` gains the same kwarg, sets `positions_detail=positions_detail` on the constructed `PortfolioSnapshot`; add:

```python
    async def latest_by_branch(self, branch_id: str, before=None):
        rows = [s for s in self._store if s.branch_id == branch_id]
        if before is not None:
            rows = [s for s in rows if s.snapshot_at < before]
        return max(rows, key=lambda s: s.snapshot_at, default=None)
```

- [ ] **Step 2.4: Run to verify pass**

Run: `pytest tests/unit/test_snapshot_detail.py tests/unit/test_portfolio_service.py tests/unit/backtest/ -q` → PASS.

- [ ] **Step 2.5: Stage**

`git add app/common/interfaces/repositories.py app/common/models/portfolio.py app/modules/portfolio/repository.py app/modules/portfolio/service.py app/modules/backtest/state.py tests/unit/test_snapshot_detail.py`

---

### Task 3: Fund summary inception metrics

**Files:**
- Modify: `app/modules/portfolio/repository.py` (add `inception_date` + `created_at` data)
- Modify: `app/modules/portfolio/service.py` (derived metrics)
- Test: `tests/unit/test_fund_summary_returns.py`

- [ ] **Step 3.1: Write the failing tests**

```python
"""Fund summary derived inception metrics (service-level, fake repo)."""

import pytest

from app.modules.portfolio.service import PortfolioService


class FakeRepo:
    def __init__(self, summary):
        self._summary = summary

    async def get_fund_summary(self, fund_id):
        return self._summary


def _svc(summary):
    return PortfolioService(
        portfolio_repo=FakeRepo(summary), position_repo=None,
        snapshot_repo=None, event_log=None,
    )


async def test_fund_summary_adds_branch_and_fund_returns():
    raw = {
        "fund_id": "f-1", "total_aum": 2_000_000.0, "total_nav": 2_030_000.0,
        "total_cash": 30_000.0, "total_long_exposure": 2_000_000.0,
        "total_short_exposure": 0.0, "execution_mode": "paper",
        "branches": [
            {"branch_id": "b-1", "allocated_capital": 1_000_000.0, "nav": 1_010_000.0,
             "inception_date": "2026-06-10"},
            {"branch_id": "b-2", "allocated_capital": 1_000_000.0, "nav": 1_020_000.0,
             "inception_date": "2026-06-10"},
        ],
    }
    out = await _svc(raw).get_fund_summary("f-1")
    b1 = out["branches"][0]
    assert b1["initial_capital"] == 1_000_000.0
    assert b1["total_pnl"] == pytest.approx(10_000.0)
    assert b1["total_return_pct"] == pytest.approx(0.01)
    assert out["total_initial_capital"] == 2_000_000.0
    assert out["total_pnl"] == pytest.approx(30_000.0)
    assert out["total_return_pct"] == pytest.approx(0.015)


async def test_fund_summary_zero_initial_capital_yields_none_pct():
    raw = {
        "fund_id": "f-1", "total_aum": 0.0, "total_nav": 500.0, "total_cash": 500.0,
        "total_long_exposure": 0.0, "total_short_exposure": 0.0,
        "execution_mode": "paper",
        "branches": [{"branch_id": "b-1", "allocated_capital": 0.0, "nav": 500.0,
                      "inception_date": None}],
    }
    out = await _svc(raw).get_fund_summary("f-1")
    assert out["branches"][0]["total_return_pct"] is None
    assert out["total_return_pct"] is None
```

- [ ] **Step 3.2: Run to verify failure**

Run: `pytest tests/unit/test_fund_summary_returns.py -q`
Expected: FAIL — `KeyError: 'initial_capital'`

- [ ] **Step 3.3: Implement**

`app/modules/portfolio/repository.py` — in `get_fund_summary`, add to each branch dict:
`"inception_date": portfolio.created_at.date().isoformat() if portfolio.created_at else None,`

`app/modules/portfolio/service.py` — replace `get_fund_summary`:

```python
    async def get_fund_summary(self, fund_id: str) -> dict:
        summary = await self.portfolio_repo.get_fund_summary(fund_id)
        total_initial = 0.0
        for b in summary.get("branches", []):
            initial = float(b.get("allocated_capital") or 0.0)
            nav = float(b.get("nav") or 0.0)
            b["initial_capital"] = initial
            b["total_pnl"] = nav - initial
            b["total_return_pct"] = (nav - initial) / initial if initial > 0 else None
            total_initial += initial
        total_nav = float(summary.get("total_nav") or 0.0)
        summary["total_initial_capital"] = total_initial
        summary["total_pnl"] = total_nav - total_initial
        summary["total_return_pct"] = (
            (total_nav - total_initial) / total_initial if total_initial > 0 else None
        )
        return summary
```

- [ ] **Step 3.4: Run to verify pass**

Run: `pytest tests/unit/test_fund_summary_returns.py tests/unit/test_portfolio_service.py -q` → PASS.

- [ ] **Step 3.5: Carry-over minors from Task-2 review**

Fix `app/db/models.py` `PortfolioSnapshotModel.positions_detail` annotation to
`Mapped[list[dict] | None]` (JSONB stores a list now). Add a unit test for
`PostgresSnapshotRepository.latest_by_branch` using a mocked `AsyncSession`
(style: the `update_cash` NAV tests in tests/unit/test_portfolio_service.py) that
asserts the executed statement filters by branch AND `snapshot_at < before`,
orders desc, limits 1 — lock the composition in before the daily job relies on it.

- [ ] **Step 3.6: Stage**

`git add app/modules/portfolio/repository.py app/modules/portfolio/service.py app/db/models.py tests/unit/test_fund_summary_returns.py tests/unit/test_portfolio_service.py`

---

### Task 4: Sells-first order sequencing + sizing cash buffer

**Files:**
- Modify: `app/modules/equities/config.py` (`cash_buffer_pct`)
- Modify: `app/modules/equities/agents/portfolio_manager.py`
- Test: `tests/unit/equities/test_order_generation_cash.py`

- [ ] **Step 4.1: Write the failing tests**

```python
"""Sells-first ordering and cash-buffer sizing."""

import pytest

from app.common.enums import OrderSide
from app.modules.equities.agents.portfolio_manager import PortfolioManager
from app.modules.equities.config import AgentsConfig, PortfolioConfig
from app.modules.equities.models import CompositeScore


def _pm(**cfg):
    return PortfolioManager(
        agents_config=AgentsConfig(),
        portfolio_config=PortfolioConfig(**cfg),
    )


def _score(symbol, conviction=50.0, target_weight=0.0):
    return CompositeScore(
        symbol=symbol, composite_score=7.0, composite_confidence=7.0,
        conviction=conviction, target_weight=target_weight,
    )


def test_generate_orders_sells_before_buys():
    pm = _pm()
    target = [_score("AAA", target_weight=0.5), _score("ZZZ", target_weight=0.4)]
    current = {"MMM": 0.5, "BBB": 0.4}  # both fully exited
    orders = pm.generate_orders(target, current, nav=1_000_000.0,
                                prices={"AAA": 10.0, "ZZZ": 10.0, "MMM": 10.0, "BBB": 10.0})
    sides = [o.side for o in orders]
    first_buy = sides.index(OrderSide.BUY)
    assert all(s == OrderSide.SELL for s in sides[:first_buy])
    assert all(s == OrderSide.BUY for s in sides[first_buy:])
    sells = [o.symbol for o in orders if o.side == OrderSide.SELL]
    assert sells == sorted(sells)


def test_size_positions_targets_sum_to_one_minus_buffer():
    pm = _pm(cash_buffer_pct=0.01)
    sized = pm.size_positions([_score(f"S{i}", conviction=10.0 + i) for i in range(10)])
    assert sum(s.target_weight for s in sized) == pytest.approx(0.99, abs=1e-9)


def test_size_positions_buffer_respects_cap():
    pm = _pm(cash_buffer_pct=0.01, max_position_weight=0.50)
    sized = pm.size_positions([_score("ONLY", conviction=42.0)])
    assert sized[0].target_weight == pytest.approx(0.50 * 0.99)


def test_size_positions_zero_conviction_equal_weight_buffered():
    pm = _pm(cash_buffer_pct=0.01)
    sized = pm.size_positions([_score("A", conviction=0.0), _score("B", conviction=0.0)])
    assert sum(s.target_weight for s in sized) == pytest.approx(0.99, abs=1e-9)
```

- [ ] **Step 4.2: Run to verify failure**

Run: `pytest tests/unit/equities/test_order_generation_cash.py -q`
Expected: FAIL — `TypeError` (`cash_buffer_pct` unknown) and/or ordering assertion.

- [ ] **Step 4.3: Implement**

`app/modules/equities/config.py` — add to `PortfolioConfig`:

```python
    # Fraction of NAV deliberately left uninvested so buy fills (slippage,
    # decision-to-fill drift) cannot overdraw cash. Target weights sum to
    # 1 - cash_buffer_pct.
    cash_buffer_pct: float = 0.01
```

`portfolio_manager.py` `size_positions` — apply the buffer in both branches:

```python
        buffer_scale = 1.0 - self.portfolio_config.cash_buffer_pct
```

In the `total_conviction == 0` branch: `target_weight=min(equal, cap) * buffer_scale`.
After the cap-redistribution loop, before building the result list:

```python
        weights = {sym: w * buffer_scale for sym, w in weights.items()}
```

`generate_orders` — before `return orders`:

```python
        # Sells first so proceeds fund the buys; alphabetical within side keeps
        # the deterministic ordering downstream execution depends on.
        orders.sort(key=lambda o: (0 if o.side == OrderSide.SELL else 1, o.symbol))
```

(`OrderSide` is already importable: add `from app.common.enums import OrderSide` if not present.)

- [ ] **Step 4.4: Run to verify pass**

Run: `pytest tests/unit/equities/test_order_generation_cash.py tests/unit/equities/test_portfolio_manager.py -q` → PASS. If existing portfolio-manager tests assert exact weights that now differ by the 0.99 factor, update those assertions deliberately (spec §7) — do not weaken the new tests.

- [ ] **Step 4.5: Stage**

`git add app/modules/equities/config.py app/modules/equities/agents/portfolio_manager.py tests/unit/equities/test_order_generation_cash.py tests/unit/equities/test_portfolio_manager.py`

---

### Task 5: Fill-time insufficient-cash rejection

**Files:**
- Modify: `app/modules/trade_execution/service.py`
- Test: `tests/unit/test_trade_execution_cash_check.py`

- [ ] **Step 5.1: Write the failing tests**

```python
"""BUY orders that would overdraw cash are rejected at fill time."""

from datetime import UTC, datetime

import pytest

from app.common.enums import OrderSide, OrderType
from app.common.interfaces.broker import OrderResult
from app.common.models.order import OrderRequest
from app.common.models.trade import Trade
from app.modules.trade_execution.service import TradeExecutionService


class FakeOrderRepo:
    def __init__(self):
        self.status_updates = []

    async def create(self, order):
        return order

    async def update_status(self, order_id, status, **kw):
        self.status_updates.append((order_id, status, kw))


class FakeTradeRepo:
    def __init__(self):
        self.created = []

    async def create(self, trade):
        self.created.append(trade)
        return trade


class FakeBroker:
    def __init__(self, fill_price):
        self.fill_price = fill_price

    async def submit_order(self, req):
        return OrderResult(
            success=True,
            trade=Trade(
                id="t-1", order_id="", branch_id=req.branch_id,
                instrument_id=req.instrument_id, symbol=req.symbol, side=req.side,
                quantity=req.quantity, price=self.fill_price, commission=0.0,
                slippage=0.0, execution_mode="paper", executed_at=datetime.now(UTC),
            ),
        )


class FakePortfolioService:
    def __init__(self, cash):
        self._cash = cash
        self.handled = []

    async def get_portfolio(self, branch_id):
        class S:  # minimal duck-typed summary
            pass
        s = S()
        s.cash = self._cash
        return s

    async def get_position_by_symbol(self, branch_id, symbol):
        return None

    async def handle_trade_executed(self, trade):
        self.handled.append(trade)


class FakeEventLog:
    def __init__(self):
        self.events = []

    async def append(self, event):
        self.events.append(event)


def _svc(cash, fill_price):
    return TradeExecutionService(
        order_repo=FakeOrderRepo(), trade_repo=FakeTradeRepo(),
        broker=FakeBroker(fill_price), event_log=FakeEventLog(),
        portfolio_service=FakePortfolioService(cash),
    )


def _buy(qty):
    return OrderRequest(
        branch_id="b-1", instrument_id="in-1", symbol="AAA",
        side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=qty,
    )


async def test_buy_exceeding_cash_is_rejected():
    svc = _svc(cash=1_000.0, fill_price=100.0)
    result = await svc.submit_order(_buy(qty=20))  # cost 2000 > 1000
    assert result["success"] is False
    assert "Insufficient cash" in result["message"]
    assert svc.trade_repo.created == []
    assert svc.portfolio_service.handled == []
    assert any("rejected" in str(u[1]).lower() for u in svc.order_repo.status_updates)


async def test_buy_exactly_at_cash_fills():
    svc = _svc(cash=2_000.0, fill_price=100.0)
    result = await svc.submit_order(_buy(qty=20))  # cost 2000 == 2000
    assert result["success"] is True
    assert len(svc.trade_repo.created) == 1


async def test_sell_ignores_cash_check():
    svc = _svc(cash=-500.0, fill_price=100.0)

    async def _pos(branch_id, symbol):
        class P:
            long_quantity = 50.0
        return P()

    svc.portfolio_service.get_position_by_symbol = _pos
    req = _buy(qty=10)
    req = req.model_copy(update={"side": OrderSide.SELL})
    result = await svc.submit_order(req)
    assert result["success"] is True
```

- [ ] **Step 5.2: Run to verify failure**

Run: `pytest tests/unit/test_trade_execution_cash_check.py -q`
Expected: `test_buy_exceeding_cash_is_rejected` FAILS (fill goes through today).

- [ ] **Step 5.3: Implement**

In `submit_order`, inside the `if result.success and result.trade:` branch, **before** any persistence:

```python
        if result.success and result.trade:
            # Cash gate at the exact fill price: a BUY that would overdraw the
            # portfolio is rejected, not filled — the paper broker itself has no
            # account state to enforce this.
            if req.side == OrderSide.BUY:
                portfolio = await self.portfolio_service.get_portfolio(req.branch_id)
                cost = result.trade.price * req.quantity + result.trade.commission
                available = float(portfolio.cash) if portfolio else 0.0
                if cost > available + 1e-6:
                    reason = (
                        f"Insufficient cash: cost {cost:.2f} > available {available:.2f}"
                    )
                    await self.order_repo.update_status(
                        order.id, OrderStatus.REJECTED, rejection_reason=reason
                    )
                    await self.event_log.append(
                        TradeRejectedEvent(
                            source="trade_execution_service",
                            order_id=order.id,
                            branch_id=req.branch_id,
                            instrument_id=req.instrument_id,
                            symbol=req.symbol,
                            side=req.side,
                            quantity=req.quantity,
                            rejection_reason=reason,
                        )
                    )
                    return {
                        "success": False,
                        "order_id": order.id,
                        "status": "rejected",
                        "message": reason,
                    }
            # ... existing fill-persistence body unchanged below
```

- [ ] **Step 5.4: Run to verify pass**

Run: `pytest tests/unit/test_trade_execution_cash_check.py tests/unit/test_trade_execution_service.py -q` → PASS.

- [ ] **Step 5.5: Stage**

`git add app/modules/trade_execution/service.py tests/unit/test_trade_execution_cash_check.py`

---

### Task 6: `PortfolioReport` + digest sections + `ny_date` helper

**Files:**
- Modify: `app/modules/equities/weekly_runner.py`
- Test: `tests/unit/equities/test_digest_portfolio_report.py`

- [ ] **Step 6.1: Write the failing tests**

```python
"""Digest rendering with portfolio report; ny_date helper."""

from datetime import UTC, date, datetime

from app.modules.equities.weekly_runner import (
    PortfolioReport,
    WeeklyRunSummary,
    ny_date,
    render_digest,
)


def _summary(**over):
    base = dict(
        run_id="2026-07-20-growth", branch_name="growth", status="completed",
        universe_count=50, screened_count=25, orders_placed=8,
        trades_executed=6, duration_seconds=95.0,
    )
    base.update(over)
    return WeeklyRunSummary(**base)


def _report(**over):
    base = dict(
        nav=1_020_000.0, cash=15_000.0, cash_pct=0.0147,
        unrealized_pnl=12_000.0, realized_pnl=8_000.0,
        initial_capital=1_000_000.0, inception_return_pct=0.02,
        wow_return_pct=0.005,
        top_holdings=[{"symbol": "AAA", "weight": 0.09}, {"symbol": "BBB", "weight": 0.07}],
        trades=[{"symbol": "CCC", "side": "buy", "quantity": 10.0, "price": 50.0,
                 "notional": 500.0}],
        unpriced=0,
    )
    base.update(over)
    return PortfolioReport(**base)


def test_digest_includes_nav_returns_holdings_trades():
    text = render_digest([_summary(portfolio_report=_report())], run_date=date(2026, 7, 20))
    assert "$1,020,000" in text
    assert "+2.00%" in text          # inception return
    assert "+0.50%" in text          # WoW
    assert "AAA 9.0%" in text
    assert "CCC" in text and "buy" in text


def test_digest_warns_on_negative_cash_and_unpriced():
    text = render_digest(
        [_summary(portfolio_report=_report(cash=-5_000.0, cash_pct=-0.005, unpriced=2))],
        run_date=date(2026, 7, 20),
    )
    assert "⚠️" in text
    assert "negative cash" in text.lower()
    assert "2 position(s) unpriced" in text


def test_digest_without_report_unchanged_shape():
    text = render_digest([_summary()], run_date=date(2026, 7, 20))
    assert "NAV" not in text


def test_ny_date_converts_utc_evening_to_same_ny_day():
    # 2026-07-20 21:30 UTC == 17:30 ET → NY date 2026-07-20
    assert ny_date(datetime(2026, 7, 20, 21, 30, tzinfo=UTC)) == date(2026, 7, 20)
    # 2026-07-21 01:00 UTC == 2026-07-20 21:00 ET
    assert ny_date(datetime(2026, 7, 21, 1, 0, tzinfo=UTC)) == date(2026, 7, 20)
```

- [ ] **Step 6.2: Run to verify failure**

Run: `pytest tests/unit/equities/test_digest_portfolio_report.py -q`
Expected: FAIL — `ImportError: cannot import name 'PortfolioReport'`

- [ ] **Step 6.3: Implement**

In `weekly_runner.py`:

```python
def ny_date(ts: datetime) -> date:
    """The America/New_York calendar date of an aware timestamp."""
    return ts.astimezone(_NY_TZ).date()


@dataclass
class PortfolioReport:
    """Marked portfolio state attached to a WeeklyRunSummary for the digest."""

    nav: float
    cash: float
    cash_pct: float
    unrealized_pnl: float
    realized_pnl: float
    initial_capital: float
    inception_return_pct: float | None
    wow_return_pct: float | None
    top_holdings: list[dict]   # [{symbol, weight}, ...] best-first
    trades: list[dict]         # [{symbol, side, quantity, price, notional}, ...]
    unpriced: int = 0
```

`WeeklyRunSummary` gains `portfolio_report: PortfolioReport | None = None`.

Add a money formatter and the digest section (inside the `status == "completed"` block of `render_digest`, after the attribution lines):

```python
def _fmt_money(x: float) -> str:
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"
```

```python
            if s.portfolio_report is not None:
                r = s.portfolio_report
                lines.append(
                    f"- NAV: {_fmt_money(r.nav)} "
                    f"(WoW {_fmt_pct(r.wow_return_pct)}, "
                    f"since inception {_fmt_pct(r.inception_return_pct)} / "
                    f"{_fmt_money(r.nav - r.initial_capital)})"
                )
                lines.append(
                    f"- Cash: {_fmt_money(r.cash)} ({r.cash_pct:.1%}) · "
                    f"Unrealized P&L: {_fmt_money(r.unrealized_pnl)} · "
                    f"Realized: {_fmt_money(r.realized_pnl)}"
                )
                if r.top_holdings:
                    tops = ", ".join(
                        f"{h['symbol']} {h['weight']:.1%}" for h in r.top_holdings[:5]
                    )
                    lines.append(f"- Top holdings: {tops}")
                if r.trades:
                    lines.append("- Trades this run:")
                    lines.append("")
                    lines.append("  | Symbol | Side | Qty | Fill | Notional |")
                    lines.append("  |---|---|---|---|---|")
                    for t in r.trades:
                        lines.append(
                            f"  | {t['symbol']} | {t['side']} | {t['quantity']:g} "
                            f"| ${t['price']:,.2f} | {_fmt_money(t['notional'])} |"
                        )
                    lines.append("")
                if r.unpriced > 0:
                    lines.append(
                        f"- ⚠️ {r.unpriced} position(s) unpriced — carried at cost basis"
                    )
                if r.cash < 0:
                    lines.append("- ⚠️ Negative cash balance — check order sizing")
```

Note: `_fmt_pct` renders `+2.00%` for `0.02` — the tests assert that format.

- [ ] **Step 6.4: Run to verify pass**

Run: `pytest tests/unit/equities/test_digest_portfolio_report.py tests/unit/equities/test_weekly_runner.py -q` → PASS.

- [ ] **Step 6.5: Stage**

`git add app/modules/equities/weekly_runner.py tests/unit/equities/test_digest_portfolio_report.py`

---

### Task 7: `scripts/common.py` + weekly CLI wiring (MTM → snapshot → report)

**Files:**
- Create: `scripts/common.py`
- Modify: `scripts/run_weekly_pipeline.py`
- Test: existing suites only (this is assembly wiring; the pieces are unit-tested above)

- [ ] **Step 7.1: Create `scripts/common.py`**

Move `_init_data_platform` and `_resolve_branch_id` from `run_weekly_pipeline.py` verbatim, renamed public:

```python
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
    """Resolve a short branch key (e.g. 'growth') to its branches.id UUID."""
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


```

`run_weekly_pipeline.py`: delete the two private helpers; `from scripts.common import init_data_platform, resolve_branch_id` and update call sites (`_init_data_platform()` → `init_data_platform()`, `_resolve_branch_id` → `resolve_branch_id`).

- [ ] **Step 7.2: Add the post-run MTM/snapshot/report step**

In `run_weekly_pipeline.py`, add a helper (after `_run_pipeline_in` definition area, module level) and call it from `_run_one_branch` right after the attribution block, for `completed` and `skipped` summaries — same isolation pattern:

```python
async def _mark_snapshot_and_report(
    *,
    branch_id: str,
    branch_name: str,
    data_service,
    summary: WeeklyRunSummary,
    run_started_at,
) -> None:
    """Mark to market, snapshot (once per NY day), and attach a PortfolioReport.

    Runs in its own session after trading data is durable. Never raises.
    """
    from datetime import UTC, datetime, time as dtime
    from zoneinfo import ZoneInfo

    from app.modules.equities.weekly_runner import PortfolioReport, ny_date

    try:
        async with async_session_factory() as session, session.begin():
            event_log = PostgresEventLogRepository(session)
            snapshot_repo = PostgresSnapshotRepository(session)
            portfolio_service = PortfolioService(
                portfolio_repo=PostgresPortfolioRepository(session),
                position_repo=PostgresPositionRepository(session),
                snapshot_repo=snapshot_repo,
                event_log=event_log,
            )
            portfolio = await portfolio_service.get_portfolio(branch_id)
            if portfolio is None:
                logger.warning("No portfolio for %s — skipping mark/snapshot", branch_name)
                return

            prices: dict[str, float | None] = {}
            for pos in portfolio.positions:
                if pos.long_quantity > 0:
                    prices[pos.symbol] = await data_service.get_current_price(pos.symbol)

            mtm = await portfolio_service.mark_to_market(branch_id, prices)

            today = today_ny()
            latest = await snapshot_repo.latest_by_branch(branch_id)
            if latest is None or ny_date(latest.snapshot_at) != today:
                await portfolio_service.take_snapshot(
                    branch_id, positions_detail=mtm.positions_detail
                )

            # WoW vs the last snapshot strictly before today (NY midnight → UTC)
            ny_midnight = datetime.combine(
                today, dtime.min, tzinfo=ZoneInfo("America/New_York")
            ).astimezone(UTC)
            prev = await snapshot_repo.latest_by_branch(branch_id, before=ny_midnight)
            wow = (mtm.nav - prev.nav) / prev.nav if prev and prev.nav > 0 else None

            initial = float(portfolio.allocated_capital)
            trades, _total = await PostgresTradeRepository(session).list_trades(
                branch_id=branch_id, since=run_started_at, limit=200
            )
            summary.portfolio_report = PortfolioReport(
                nav=mtm.nav,
                cash=mtm.cash,
                cash_pct=mtm.cash / mtm.nav if mtm.nav > 0 else 0.0,
                unrealized_pnl=mtm.unrealized_pnl,
                realized_pnl=mtm.realized_pnl,
                initial_capital=initial,
                inception_return_pct=(mtm.nav - initial) / initial if initial > 0 else None,
                wow_return_pct=wow,
                top_holdings=[
                    {"symbol": d["symbol"], "weight": d["weight"]}
                    for d in mtm.positions_detail[:5]
                ],
                trades=[
                    {
                        "symbol": t.symbol,
                        "side": str(t.side),
                        "quantity": float(t.quantity),
                        "price": float(t.price),
                        "notional": float(t.quantity) * float(t.price),
                    }
                    for t in trades
                ],
                unpriced=mtm.unpriced,
            )
    except Exception:
        logger.warning("Mark/snapshot/report failed for %s — continuing", branch_name, exc_info=True)
```

Call site in `_run_one_branch` (capture `run_started_at = datetime.now(UTC)` just before `runner.execute`; import `datetime`/`UTC` at module top):

```python
    if summary.status in ("completed", "skipped"):
        await _mark_snapshot_and_report(
            branch_id=branch_id,
            branch_name=branch_name,
            data_service=equities_service.data_service,
            summary=summary,
            run_started_at=run_started_at,
        )
```

(Place this AFTER the attribution block so attribution and report both appear in the digest.)

- [ ] **Step 7.3: Add `--report-dir`**

In `_main_async` after the digest is built:

```python
    if args.report_dir:
        report_dir = Path(args.report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / f"{today_ny().isoformat()}.md").write_text(digest, encoding="utf-8")
```

And in `main()`:

```python
    parser.add_argument(
        "--report-dir",
        default=None,
        help="Also write the digest to <dir>/<run_date>.md (workflow passes scheduled_run_results)",
    )
```

- [ ] **Step 7.4: Verify**

Run: `pytest tests/unit/ -q` → PASS. `ruff check scripts/ app/` → clean.
Sanity: `python -m scripts.run_weekly_pipeline --dry-run` (against local/absent DB this exercises imports and arg parsing; exit code 0 or a clean infrastructure error if no DB — either proves the module loads).

- [ ] **Step 7.5: Stage**

`git add scripts/common.py scripts/run_weekly_pipeline.py`

---

### Task 8: Daily snapshot script + workflow

**Files:**
- Create: `scripts/take_daily_snapshot.py`
- Create: `.github/workflows/daily-snapshot.yml`

- [ ] **Step 8.1: Create the script**

```python
"""Daily EOD mark-to-market + snapshot for each enabled branch.

Run by .github/workflows/daily-snapshot.yml on weekday evenings (~after close).
Idempotent: a branch already snapshotted today (NY) is skipped, so re-runs and
Monday overlap with the weekly pipeline are harmless.

Exit codes: 0 = all branches snapshotted or skipped; 2 = infrastructure error.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from dotenv import load_dotenv

load_dotenv()

from app.config import settings  # noqa: E402
from app.db.connection import async_session_factory  # noqa: E402
from app.modules.equities.weekly_runner import ny_date, today_ny  # noqa: E402
from app.modules.event_log.repository import PostgresEventLogRepository  # noqa: E402
from app.modules.portfolio.repository import (  # noqa: E402
    PostgresPortfolioRepository,
    PostgresPositionRepository,
    PostgresSnapshotRepository,
)
from app.modules.portfolio.service import PortfolioService  # noqa: E402
from scripts.common import init_data_platform, resolve_branch_id  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("yfinance").setLevel(logging.WARNING)
logger = logging.getLogger("daily_snapshot")


async def snapshot_branch(branch_name: str, data_service) -> str:
    """Returns 'snapshotted' | 'skipped' | 'no-portfolio'."""
    async with async_session_factory() as session, session.begin():
        branch_id = await resolve_branch_id(session, branch_name)
        snapshot_repo = PostgresSnapshotRepository(session)
        portfolio_service = PortfolioService(
            portfolio_repo=PostgresPortfolioRepository(session),
            position_repo=PostgresPositionRepository(session),
            snapshot_repo=snapshot_repo,
            event_log=PostgresEventLogRepository(session),
        )
        portfolio = await portfolio_service.get_portfolio(branch_id)
        if portfolio is None:
            logger.warning("No portfolio for %s", branch_name)
            return "no-portfolio"

        latest = await snapshot_repo.latest_by_branch(branch_id)
        if latest is not None and ny_date(latest.snapshot_at) == today_ny():
            logger.info("%s already snapshotted today — skipping", branch_name)
            return "skipped"

        prices: dict[str, float | None] = {}
        for pos in portfolio.positions:
            if pos.long_quantity > 0:
                prices[pos.symbol] = await data_service.get_current_price(pos.symbol)

        mtm = await portfolio_service.mark_to_market(branch_id, prices)
        await portfolio_service.take_snapshot(branch_id, positions_detail=mtm.positions_detail)
        logger.info(
            "%s: NAV %.2f (unrealized %.2f, %d unpriced)",
            branch_name, mtm.nav, mtm.unrealized_pnl, mtm.unpriced,
        )
        return "snapshotted"


async def _main_async() -> int:
    data_service = init_data_platform()
    for branch in settings.equities_enabled_branches:
        await snapshot_branch(branch, data_service)
    return 0


def main() -> None:
    try:
        sys.exit(asyncio.run(_main_async()))
    except Exception:
        logger.exception("Infrastructure error")
        sys.exit(2)


if __name__ == "__main__":
    main()
```

- [ ] **Step 8.2: Create the workflow**

```yaml
name: Daily Snapshot

on:
  schedule:
    # 21:30 UTC ≈ 5:30pm ET (4:30pm EST winter) — after market close.
    - cron: "30 21 * * 1-5"
  workflow_dispatch: {}

concurrency:
  group: daily-snapshot
  cancel-in-progress: false

jobs:
  snapshot:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    env:
      HEDGE_DATABASE_URL: ${{ secrets.HEDGE_DATABASE_URL }}
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

      - name: Take daily snapshot
        run: python -m scripts.take_daily_snapshot
```

- [ ] **Step 8.2b: Hardening (added during implementation)**

Per-branch fault isolation in `_main_async` (mirror the weekly CLI): wrap each
`snapshot_branch` call in try/except, log the failure, continue to the next branch,
track `any_failed` and return 1 if any branch failed (0 otherwise); log a final
summary line of per-branch outcomes. Workflow gains an `alembic upgrade head` step
before the snapshot step (a mid-week migration must not break the evening job).

- [ ] **Step 8.3: Verify**

Run: `pytest tests/unit/ -q` and `ruff check scripts/` → clean. `python -c "import scripts.take_daily_snapshot"` imports cleanly.

- [ ] **Step 8.4: Stage**

`git add scripts/take_daily_snapshot.py .github/workflows/daily-snapshot.yml`

---

### Task 9: Backfill script with validation gate

**Files:**
- Create: `scripts/backfill_snapshots.py`
- Test: `tests/unit/test_backfill_reconstruction.py`

- [ ] **Step 9.1: Write the failing tests**

```python
"""Known-answer tests for the backfill reconstruction math."""

from datetime import UTC, date, datetime

import pytest

from app.common.enums import ExecutionMode, OrderSide
from app.common.models.trade import Trade
from scripts.backfill_snapshots import reconstruct_daily_states, validate_final_state


def _trade(symbol, side, qty, price, day, commission=0.0):
    return Trade(
        id=f"t-{symbol}-{day}", order_id="o", branch_id="b-1", instrument_id="i",
        symbol=symbol, side=side, quantity=qty, price=price, commission=commission,
        execution_mode=ExecutionMode.PAPER,
        executed_at=datetime(day.year, day.month, day.day, 15, 40, tzinfo=UTC),
    )


def test_reconstruction_known_answer():
    d1, d2, d3 = date(2026, 6, 15), date(2026, 6, 16), date(2026, 6, 17)
    trades = [
        _trade("AAA", OrderSide.BUY, 100, 10.0, d1),          # cash 100000-1000=99000
        _trade("AAA", OrderSide.SELL, 50, 12.0, d3),          # proceeds 600
    ]
    closes = {"AAA": {d1: 11.0, d2: 12.0, d3: 12.5}}
    states = reconstruct_daily_states(
        trades, initial_cash=100_000.0, closes=closes, trading_days=[d1, d2, d3]
    )
    assert [s.day for s in states] == [d1, d2, d3]
    # Day1: cash 99000, AAA 100 @ close 11 → nav 100100, unrealized +100
    assert states[0].cash == pytest.approx(99_000.0)
    assert states[0].nav == pytest.approx(100_100.0)
    assert states[0].unrealized_pnl == pytest.approx(100.0)
    # Day2: no trades; close 12 → nav 99000 + 1200
    assert states[1].nav == pytest.approx(100_200.0)
    # Day3: sell 50 @ 12 → cash 99600; realized (12-10)*50 = 100
    assert states[2].cash == pytest.approx(99_600.0)
    assert states[2].realized_pnl == pytest.approx(100.0)
    # remaining 50 @ close 12.5 → nav 99600 + 625
    assert states[2].nav == pytest.approx(100_225.0)
    assert states[2].positions["AAA"]["quantity"] == pytest.approx(50.0)


def test_reconstruction_carries_forward_missing_close():
    d1, d2 = date(2026, 6, 15), date(2026, 6, 16)
    trades = [_trade("AAA", OrderSide.BUY, 10, 100.0, d1)]
    closes = {"AAA": {d1: 100.0}}  # no close on d2
    states = reconstruct_daily_states(
        trades, initial_cash=10_000.0, closes=closes, trading_days=[d1, d2]
    )
    assert states[1].nav == pytest.approx(states[0].nav)


def test_reconstruction_commission_reduces_cash_and_realized():
    d1 = date(2026, 6, 15)
    trades = [
        _trade("AAA", OrderSide.BUY, 10, 100.0, d1, commission=1.0),
        _trade("AAA", OrderSide.SELL, 10, 110.0, d1, commission=1.0),
    ]
    closes = {"AAA": {d1: 110.0}}
    states = reconstruct_daily_states(
        trades, initial_cash=10_000.0, closes=closes, trading_days=[d1]
    )
    # buy cost 1001, sell proceeds 1099; realized = (110 - 100.1)*10 - 1 = 98
    assert states[0].cash == pytest.approx(10_000.0 - 1001.0 + 1099.0)
    assert states[0].realized_pnl == pytest.approx(98.0)


def test_reconstruction_rejects_non_long_sides():
    d1 = date(2026, 6, 15)
    with pytest.raises(ValueError):
        reconstruct_daily_states(
            [_trade("AAA", OrderSide.SHORT, 10, 100.0, d1)],
            initial_cash=1_000.0, closes={"AAA": {d1: 100.0}}, trading_days=[d1],
        )


def test_validate_final_state_reports_mismatches():
    d1 = date(2026, 6, 15)
    states = reconstruct_daily_states(
        [_trade("AAA", OrderSide.BUY, 10, 100.0, d1)],
        initial_cash=10_000.0, closes={"AAA": {d1: 100.0}}, trading_days=[d1],
    )
    ok = validate_final_state(states[-1], live_cash=9_000.0, live_positions={"AAA": 10.0})
    assert ok == []
    bad = validate_final_state(states[-1], live_cash=1_234.0, live_positions={"AAA": 99.0})
    assert len(bad) == 2
```

- [ ] **Step 9.2: Run to verify failure**

Run: `pytest tests/unit/test_backfill_reconstruction.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.backfill_snapshots'`

- [ ] **Step 9.3: Implement**

`scripts/backfill_snapshots.py`:

```python
"""Backfill daily portfolio_snapshots from the trades ledger + Yahoo closes.

Reconstructs each branch's cash/positions day by day using the same average-cost
math as PortfolioService.handle_trade_executed, values positions at daily closes
(carrying the last known close forward across gaps), and inserts one snapshot per
NY trading day at 16:00 ET. Days that already have a snapshot are skipped.

Safety: --dry-run (default) writes nothing. --apply refuses to write unless the
reconstructed final cash and per-symbol quantities match the live portfolios/
positions rows (to the cent / 1e-6 shares).

Usage:
    python -m scripts.backfill_snapshots --branches growth value            # dry run
    python -m scripts.backfill_snapshots --branches growth value --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

from app.common.enums import OrderSide  # noqa: E402
from app.common.models.trade import Trade  # noqa: E402
from app.db.connection import async_session_factory  # noqa: E402
from app.db.models import PortfolioModel, PortfolioSnapshotModel, PositionModel, TradeModel  # noqa: E402
from scripts.common import init_data_platform, resolve_branch_id  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("yfinance").setLevel(logging.WARNING)
logger = logging.getLogger("backfill_snapshots")

_NY_TZ = ZoneInfo("America/New_York")


@dataclass
class DailyState:
    day: date
    cash: float
    nav: float
    unrealized_pnl: float
    realized_pnl: float
    # symbol -> {quantity, cost_basis, close (float|None), market_value}
    positions: dict[str, dict] = field(default_factory=dict)


def reconstruct_daily_states(
    trades: list[Trade],
    *,
    initial_cash: float,
    closes: dict[str, dict[date, float]],
    trading_days: list[date],
) -> list[DailyState]:
    """Replay trades over trading_days; value positions at (carried-forward) closes."""
    for t in trades:
        if t.side not in (OrderSide.BUY, OrderSide.SELL):
            raise ValueError(f"Backfill supports long-only trades, got {t.side} for {t.symbol}")

    by_day: dict[date, list[Trade]] = {}
    for t in sorted(trades, key=lambda t: t.executed_at):
        by_day.setdefault(t.executed_at.astimezone(_NY_TZ).date(), []).append(t)

    cash = initial_cash
    realized_total = 0.0
    qty: dict[str, float] = {}
    cost: dict[str, float] = {}
    last_close: dict[str, float] = {}
    states: list[DailyState] = []

    for day in sorted(trading_days):
        for t in by_day.get(day, []):
            if t.side == OrderSide.BUY:
                trade_cost = t.price * t.quantity + t.commission
                qty[t.symbol] = qty.get(t.symbol, 0.0) + t.quantity
                cost[t.symbol] = cost.get(t.symbol, 0.0) + trade_cost
                cash -= trade_cost
            else:  # SELL — same avg-cost math as handle_trade_executed
                held = qty.get(t.symbol, 0.0)
                if held < t.quantity - 1e-9:
                    raise ValueError(f"Ledger inconsistency: sell {t.quantity} {t.symbol}, hold {held}")
                avg = cost.get(t.symbol, 0.0) / held if held > 0 else 0.0
                realized_total += (t.price - avg) * t.quantity - t.commission
                cost[t.symbol] = cost.get(t.symbol, 0.0) - avg * t.quantity
                qty[t.symbol] = held - t.quantity
                cash += t.price * t.quantity - t.commission

        total_mv = 0.0
        total_cost = 0.0
        positions: dict[str, dict] = {}
        for sym, q in qty.items():
            if q <= 1e-9:
                continue
            day_close = closes.get(sym, {}).get(day)
            if day_close is not None:
                last_close[sym] = day_close
            close = last_close.get(sym)
            mv = close * q if close is not None else cost.get(sym, 0.0)
            total_mv += mv
            total_cost += cost.get(sym, 0.0)
            positions[sym] = {
                "quantity": q,
                "cost_basis": cost.get(sym, 0.0),
                "close": close,
                "market_value": mv,
            }

        states.append(
            DailyState(
                day=day,
                cash=cash,
                nav=cash + total_mv,
                unrealized_pnl=total_mv - total_cost,
                realized_pnl=realized_total,
                positions=positions,
            )
        )
    return states


def validate_final_state(
    final: DailyState, *, live_cash: float, live_positions: dict[str, float]
) -> list[str]:
    """Compare reconstruction vs live DB. Empty list == valid."""
    problems = []
    if abs(final.cash - live_cash) > 0.01:
        problems.append(f"cash mismatch: reconstructed {final.cash:.2f} vs live {live_cash:.2f}")
    recon_qty = {s: p["quantity"] for s, p in final.positions.items()}
    for sym in sorted(set(recon_qty) | set(live_positions)):
        r, l = recon_qty.get(sym, 0.0), live_positions.get(sym, 0.0)
        if abs(r - l) > 1e-6:
            problems.append(f"{sym} quantity mismatch: reconstructed {r} vs live {l}")
    return problems


def snapshot_positions_detail(state: DailyState) -> list[dict]:
    detail = [
        {
            "symbol": sym,
            "quantity": p["quantity"],
            "price": p["close"],
            "market_value": p["market_value"],
            "cost_basis": p["cost_basis"],
            "unrealized_pnl": p["market_value"] - p["cost_basis"],
            "weight": p["market_value"] / state.nav if state.nav > 0 else 0.0,
        }
        for sym, p in state.positions.items()
    ]
    detail.sort(key=lambda d: d["market_value"], reverse=True)
    return detail


async def _load_trades(session, branch_id: str) -> list[Trade]:
    stmt = (
        select(TradeModel)
        .where(TradeModel.branch_id == uuid.UUID(branch_id))
        .order_by(TradeModel.executed_at)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        Trade(
            id=str(r.id), order_id=str(r.order_id), branch_id=str(r.branch_id),
            instrument_id=str(r.instrument_id), symbol=r.symbol,
            side=OrderSide(r.side), quantity=float(r.quantity), price=float(r.price),
            commission=float(r.commission), slippage=float(r.slippage),
            execution_mode=r.execution_mode, executed_at=r.executed_at,
        )
        for r in rows
    ]


async def _fetch_closes(data_service, symbols, start: date, end: date):
    closes: dict[str, dict[date, float]] = {}
    trading_days: set[date] = set()
    for sym in sorted(symbols):
        result = await data_service.get_prices(sym, start, end + timedelta(days=1))
        closes[sym] = {}
        for bar in result.get("bars", []):
            # PriceBar.timestamp is a plain date (see app/common/interfaces/price_data.py)
            d = bar["timestamp"]
            if not isinstance(d, date):
                d = datetime.fromisoformat(str(d)).date()
            closes[sym][d] = float(bar["close"])
            trading_days.add(d)
    return closes, trading_days


async def backfill_branch(branch_name: str, data_service, *, apply: bool) -> None:
    async with async_session_factory() as session, session.begin():
        branch_id = await resolve_branch_id(session, branch_name)
        trades = await _load_trades(session, branch_id)
        if not trades:
            logger.info("%s: no trades — nothing to backfill", branch_name)
            return

        pf = (
            await session.execute(
                select(PortfolioModel).where(PortfolioModel.branch_id == uuid.UUID(branch_id))
            )
        ).scalar_one()
        live_positions = {
            r.symbol: float(r.long_quantity)
            for r in (
                await session.execute(
                    select(PositionModel).where(PositionModel.portfolio_id == pf.id)
                )
            ).scalars().all()
            if float(r.long_quantity) > 0
        }
        # initial cash: portfolio seeded with $1M before the first trade
        initial_cash = 1_000_000.0

        first_day = trades[0].executed_at.astimezone(_NY_TZ).date()
        yesterday = datetime.now(_NY_TZ).date() - timedelta(days=1)
        symbols = {t.symbol for t in trades}
        closes, trading_days = await _fetch_closes(data_service, symbols, first_day, yesterday)
        days = sorted(d for d in trading_days if first_day <= d <= yesterday)

        states = reconstruct_daily_states(
            trades, initial_cash=initial_cash, closes=closes, trading_days=days
        )

        problems = validate_final_state(
            states[-1], live_cash=float(pf.cash), live_positions=live_positions
        )
        print(f"\n=== {branch_name}: {len(states)} trading days "
              f"({states[0].day} → {states[-1].day}) ===")
        print(f"{'day':<12}{'cash':>14}{'nav':>14}{'unreal':>12}{'real':>10}{'pos':>5}")
        for s in states:
            print(f"{s.day.isoformat():<12}{s.cash:>14,.2f}{s.nav:>14,.2f}"
                  f"{s.unrealized_pnl:>12,.2f}{s.realized_pnl:>10,.2f}{len(s.positions):>5}")
        if problems:
            print("\nVALIDATION FAILED:")
            for p in problems:
                print(f"  ✗ {p}")
        else:
            print("\nValidation: reconstructed cash and quantities match live DB ✓")

        if not apply:
            logger.info("%s: dry run — nothing written", branch_name)
            return
        if problems:
            raise SystemExit(f"{branch_name}: refusing to --apply with validation failures")

        existing_days = {
            row.astimezone(_NY_TZ).date()
            for row in (
                await session.execute(
                    select(PortfolioSnapshotModel.snapshot_at).where(
                        PortfolioSnapshotModel.branch_id == uuid.UUID(branch_id)
                    )
                )
            ).scalars().all()
        }
        written = 0
        for s in states:
            if s.day in existing_days:
                continue
            total_mv = sum(p["market_value"] for p in s.positions.values())
            session.add(
                PortfolioSnapshotModel(
                    portfolio_id=pf.id,
                    branch_id=uuid.UUID(branch_id),
                    cash=s.cash,
                    nav=s.nav,
                    total_long_exposure=total_mv,
                    total_short_exposure=0.0,
                    gross_exposure=total_mv,
                    net_exposure=total_mv,
                    unrealized_pnl=s.unrealized_pnl,
                    realized_pnl=s.realized_pnl,
                    margin_used=0.0,
                    position_count=len(s.positions),
                    positions_detail=snapshot_positions_detail(s),
                    snapshot_at=datetime.combine(s.day, dtime(16, 0), tzinfo=_NY_TZ).astimezone(UTC),
                )
            )
            written += 1
        logger.info("%s: wrote %d snapshots (%d already existed)",
                    branch_name, written, len(states) - written)


async def _main_async(args) -> int:
    data_service = init_data_platform()
    for branch in args.branches:
        await backfill_branch(branch, data_service, apply=args.apply)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branches", nargs="+", default=["growth", "value"])
    parser.add_argument("--apply", action="store_true", help="Write snapshots (default: dry run)")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(_main_async(args)))
    except SystemExit:
        raise
    except Exception:
        logger.exception("Backfill failed")
        sys.exit(2)


if __name__ == "__main__":
    main()
```

Note: `PortfolioSnapshotModel.snapshot_at` has a server default; setting it explicitly is required here — verify the model accepts it (it does; plain `DateTime(timezone=True)` column).

- [ ] **Step 9.4: Run to verify pass**

Run: `pytest tests/unit/test_backfill_reconstruction.py -q` → PASS. `ruff check scripts/` → clean.

- [ ] **Step 9.5: Stage**

`git add scripts/backfill_snapshots.py tests/unit/test_backfill_reconstruction.py`

---

### Task 10: `build_report_json.py` + weekly workflow auto-commit

**Files:**
- Create: `scripts/build_report_json.py`
- Modify: `.github/workflows/weekly-rebalance.yml`
- Test: `tests/unit/test_report_json_helpers.py`

- [ ] **Step 10.1: Write the failing test (dedupe helper)**

```python
"""Snapshot series dedupe: last snapshot per NY date wins."""

from datetime import UTC, datetime

from scripts.build_report_json import dedupe_last_per_day


def _snap(iso_utc, nav):
    return {"snapshot_at": datetime.fromisoformat(iso_utc).replace(tzinfo=UTC), "nav": nav}


def test_dedupe_keeps_last_snapshot_per_ny_date():
    snaps = [
        _snap("2026-07-20T14:00:00", 100.0),  # 10:00 ET
        _snap("2026-07-20T21:30:00", 101.0),  # 17:30 ET same NY day — wins
        _snap("2026-07-21T21:30:00", 102.0),
        _snap("2026-07-22T01:00:00", 103.0),  # 21:00 ET on 07-21 — wins over 102
    ]
    out = dedupe_last_per_day(snaps)
    assert [(d, s["nav"]) for d, s in out] == [("2026-07-20", 101.0), ("2026-07-21", 103.0)]
```

- [ ] **Step 10.2: Run to verify failure**

Run: `pytest tests/unit/test_report_json_helpers.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 10.3: Implement**

`scripts/build_report_json.py`:

```python
"""Regenerate scheduled_run_results/report.json wholesale from the database.

Self-healing: every run rebuilds the full file (NAV series, holdings, trades,
attribution, inception metrics per branch), so a missed week or a manual DB fix
never leaves the report stale. Intended to be run by the weekly workflow after
the pipeline, then committed to the repo; also runnable manually.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

from app.db.connection import async_session_factory  # noqa: E402
from app.db.models import (  # noqa: E402
    AttributionReportModel,
    BranchModel,
    PortfolioModel,
    PortfolioSnapshotModel,
    TradeModel,
)
from scripts.common import resolve_branch_id  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("build_report_json")

_NY_TZ = ZoneInfo("America/New_York")


def dedupe_last_per_day(snapshots: list[dict]) -> list[tuple[str, dict]]:
    """Collapse snapshots to the last one per NY date, ascending by date."""
    by_day: dict[str, dict] = {}
    for s in sorted(snapshots, key=lambda s: s["snapshot_at"]):
        by_day[s["snapshot_at"].astimezone(_NY_TZ).date().isoformat()] = s
    return sorted(by_day.items())


async def _branch_payload(session, branch_name: str) -> dict:
    branch_id = await resolve_branch_id(session, branch_name)
    bid = uuid.UUID(branch_id)

    branch = await session.get(BranchModel, bid)
    pf = (
        await session.execute(select(PortfolioModel).where(PortfolioModel.branch_id == bid))
    ).scalar_one_or_none()

    snaps = [
        {
            "snapshot_at": r.snapshot_at,
            "nav": float(r.nav),
            "cash": float(r.cash),
            "unrealized_pnl": float(r.unrealized_pnl),
            "realized_pnl": float(r.realized_pnl),
            "position_count": r.position_count,
            "positions_detail": r.positions_detail,
        }
        for r in (
            await session.execute(
                select(PortfolioSnapshotModel)
                .where(PortfolioSnapshotModel.branch_id == bid)
                .order_by(PortfolioSnapshotModel.snapshot_at)
            )
        ).scalars().all()
    ]
    series = dedupe_last_per_day(snaps)

    trades = [
        {
            "date": r.executed_at.astimezone(_NY_TZ).date().isoformat(),
            "symbol": r.symbol,
            "side": r.side,
            "quantity": float(r.quantity),
            "price": float(r.price),
            "notional": float(r.quantity) * float(r.price),
        }
        for r in (
            await session.execute(
                select(TradeModel).where(TradeModel.branch_id == bid).order_by(TradeModel.executed_at)
            )
        ).scalars().all()
    ]

    attribution = [
        {
            "decision_date": r.decision_date.isoformat(),
            "as_of_date": r.as_of_date.isoformat(),
            "basket_return_conviction": float(r.basket_return_conviction),
            "basket_return_equal": float(r.basket_return_equal),
            "benchmark_return": float(r.benchmark_return),
            "benchmark_symbol": r.benchmark_symbol,
            "spy_return": float(r.spy_return),
            "analyst_ics": r.analyst_ics,
        }
        for r in (
            await session.execute(
                select(AttributionReportModel)
                .where(AttributionReportModel.branch_id == bid)
                .order_by(AttributionReportModel.decision_date)
            )
        ).scalars().all()
    ]

    initial = float(branch.allocated_capital) if branch else 0.0
    latest_nav = series[-1][1]["nav"] if series else (float(pf.nav) if pf else 0.0)
    latest_detail = next(
        (s["positions_detail"] for _, s in reversed(series) if s.get("positions_detail")), None
    )
    return {
        "initial_capital": initial,
        "inception_date": pf.created_at.date().isoformat() if pf and pf.created_at else None,
        "nav": latest_nav,
        "cash": float(pf.cash) if pf else None,
        "total_pnl": latest_nav - initial if initial > 0 else None,
        "total_return_pct": (latest_nav - initial) / initial if initial > 0 else None,
        "nav_series": [
            {"date": d, "nav": s["nav"], "cash": s["cash"],
             "unrealized_pnl": s["unrealized_pnl"], "realized_pnl": s["realized_pnl"]}
            for d, s in series
        ],
        "holdings": latest_detail,
        "trades": trades,
        "attribution": attribution,
    }


async def _main_async(args) -> int:
    payload: dict = {
        "generated_at": datetime.now(UTC).isoformat(),
        "branches": {},
    }
    async with async_session_factory() as session:
        for branch in args.branches:
            payload["branches"][branch] = await _branch_payload(session, branch)

    totals_initial = sum(b["initial_capital"] for b in payload["branches"].values())
    totals_nav = sum(b["nav"] for b in payload["branches"].values())
    payload["fund"] = {
        "initial_capital": totals_initial,
        "nav": totals_nav,
        "total_pnl": totals_nav - totals_initial if totals_initial > 0 else None,
        "total_return_pct": (
            (totals_nav - totals_initial) / totals_initial if totals_initial > 0 else None
        ),
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote %s", out)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branches", nargs="+", default=["growth", "value"])
    parser.add_argument("--out", default="scheduled_run_results/report.json")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(_main_async(args)))
    except Exception:
        logger.exception("report.json build failed")
        sys.exit(2)


if __name__ == "__main__":
    main()
```

(`AttributionReportModel` is the verified class name at `app/db/models.py:494`.)

- [ ] **Step 10.4: Update the weekly workflow**

`.github/workflows/weekly-rebalance.yml` — add at job level (under `jobs.run:`):

```yaml
    permissions:
      contents: write
```

Change the pipeline invocation to write the digest file:

```yaml
          python -m scripts.run_weekly_pipeline $ARGS --report-dir scheduled_run_results
```

Append after the "Run weekly pipeline" step:

```yaml
      - name: Build report.json
        if: always()
        run: python -m scripts.build_report_json --out scheduled_run_results/report.json

      - name: Commit weekly report
        if: always()
        run: |
          git config user.name "hedgefund-bot"
          git config user.email "actions@users.noreply.github.com"
          git add scheduled_run_results/
          if git diff --cached --quiet; then
            echo "No report changes to commit"
          else
            git pull --rebase origin main
            git commit -m "chore(report): weekly report $(date -u +%F) [skip ci]"
            git push origin HEAD:main
          fi
```

(`if: always()` so a failed branch still publishes what the DB has — the digest itself already reports failures.)

- [ ] **Step 10.5: Verify**

Run: `pytest tests/unit/test_report_json_helpers.py -q` → PASS. `ruff check scripts/` → clean.

- [ ] **Step 10.6: Stage**

`git add scripts/build_report_json.py tests/unit/test_report_json_helpers.py .github/workflows/weekly-rebalance.yml`

---

### Task 11: Docs (CLAUDE.md) + full verification

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 11.1: Update CLAUDE.md**

1. In the seed SQL block (Running Integration Tests → DB Reset Procedure), change branch inserts to `allocated_capital` `1000000.00` and the fund insert to `total_aum` `2000000.00`.
2. Commands section — add under "Run server":

```bash
# Investor reporting
python -m scripts.take_daily_snapshot                         # EOD mark-to-market + snapshot (idempotent per NY day)
python -m scripts.backfill_snapshots --branches growth value  # dry-run reconstruction vs live DB
python -m scripts.backfill_snapshots --branches growth value --apply
python -m scripts.build_report_json --out scheduled_run_results/report.json
```

3. Gotchas — add:

```markdown
- **NAV is marked to market weekly (pipeline) and each weekday (daily-snapshot workflow)** — between marks, `portfolios.nav` is as-of the last mark. `portfolio_snapshots` may contain two rows on Mondays (post-rebalance + EOD); consumers must dedupe to the last snapshot per NY date (see `scripts/build_report_json.dedupe_last_per_day`).
- **BUY orders are rejected at fill time if cost exceeds cash** (`TradeExecutionService`). Sizing leaves `cash_buffer_pct` (1%) uninvested and orders execute sells-first, so rejections should be rare; a rejection means sizing drifted — investigate.
```

4. "Running the Weekly Pipeline" — note the workflow now auto-commits `scheduled_run_results/` (digest md + report.json) with `[skip ci]`.

- [ ] **Step 11.1b: Review-minor cleanups (from Task-3 quality review)**

In `tests/unit/test_portfolio_service.py`: drop the private-attr assertion
`len(stmt._where_criteria) == 2` (compiled-SQL assertions already cover it) and update
the file docstring to mention it also holds Postgres repository statement-shape tests.
In `app/modules/portfolio/service.py`: add a 2-line docstring to `get_fund_summary`
(fractions not percents; None = no baseline).
From Task-4 review: in `portfolio_manager.size_positions`, extend the docstring to
state the realized max weight is `max_position_weight × (1 − cash_buffer_pct)` (gap
deliberately not redistributed — it IS the buffer); in
tests/unit/equities/test_order_generation_cash.py add one assertion pinning the
default `PortfolioConfig().cash_buffer_pct == 0.01`.
From Task-5 review: update the stale `_validate_order` BUY-branch comment in
app/modules/trade_execution/service.py (cash IS now enforced at fill time in
submit_order); in tests/unit/test_trade_execution_cash_check.py make the fake's
`handle_trade_executed` mutate `self._cash` and add a sequential test (sell fills →
buy passes; buy drains → next buy rejects) pinning re-read-per-order freshness.
From Task-7 review: in scripts/run_weekly_pipeline.py, (a) fix the skipped-path
rationale comment (the digest renders portfolio_report only for completed runs —
the point of running on skipped is refreshing marks + the once-per-day snapshot),
and (b) build the PortfolioReport inside the transaction but assign
`summary.portfolio_report` only AFTER the `async with` block commits, so a failed
commit can't leave unpersisted numbers in the digest.
From Task-10 implementation: in scripts/build_report_json.py `_branch_payload`,
guard the nullable attribution columns — `benchmark_return`/`spy_return` are
nullable in the schema; use `float(x) if x is not None else None` so one NULL row
can't crash the whole report build (exit 2 would redden an otherwise-green weekly
job).
From Task-10 quality review: atomic report write (write to `.tmp`, `os.replace`)
so a truncated file can never be committed by the always()-commit step; add
`mkdir -p scheduled_run_results` to the workflow commit step (first-run `git add`
on a missing dir exits 128); drop the dead `position_count` key from the snapshot
dicts in build_report_json.

- [ ] **Step 11.2: Full verification**

Run: `pytest tests/unit/ -q` → ALL PASS.
Run: `pytest tests/ --ignore=tests/integration -q` → PASS.
Run: `ruff check app/ tests/ scripts/ && ruff format --check app/ tests/ scripts/` → clean (run `ruff format` on touched files if needed).

- [ ] **Step 11.3: Stage**

`git add CLAUDE.md docs/superpowers/specs/2026-07-15-investor-reporting-foundation-design.md docs/superpowers/plans/2026-07-15-investor-reporting-foundation.md`

---

### Task 12: Rollout against prod (after user has reviewed staged code, or as directed)

- [ ] **Step 12.1:** Fetch the prod connection string via the Neon MCP (`get_connection_string`, project `small-firefly-14151124`) — never print the password into committed files.
- [ ] **Step 12.2:** `HEDGE_DATABASE_URL=<neon-url> python -m scripts.backfill_snapshots --branches growth value` (dry run). Review the per-day table + validation lines.
- [ ] **Step 12.3:** If validation passes: re-run with `--apply`.
- [ ] **Step 12.4:** Prod baseline SQL (Neon MCP `run_sql`, single reversible updates):

```sql
UPDATE branches SET allocated_capital = 1000000
WHERE id IN ('33333333-3333-3333-3333-333333333333', '44444444-4444-4444-4444-444444444444');
UPDATE funds SET total_aum = 2000000 WHERE id = '11111111-1111-1111-1111-111111111111';
```

- [ ] **Step 12.5:** Verify: `SELECT COUNT(*), MIN(snapshot_at), MAX(snapshot_at) FROM portfolio_snapshots;` shows ~20 trading days per branch since 2026-06-15; `HEDGE_DATABASE_URL=<neon-url> python -m scripts.build_report_json --out /tmp/report.json` produces sane inception returns.

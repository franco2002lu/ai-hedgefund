# Theme A — P&L Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close out the P&L-integrity gaps: post-run invariant checks that write the first-ever `risk_alerts` rows, sell-all semantics for full exits (kills dust), a 0.5% entry threshold, a hard error instead of the fictional $1M NAV fallback, regression tests for the 2026-06-22 negative-cash incident, and a leverage disclosure note in investor reporting.

**Architecture:** Pure check logic in a new `app/modules/equities/risk_checks.py`, persisted via a new `PostgresRiskAlertRepository` and surfaced through the existing weekly digest; `PortfolioManager.generate_orders` gains a `current_quantities` map for full exits; the trade-execution SELL path gets a 1e-6 tolerance + full-exit clamp; the service's portfolio-read block becomes a hard-failing module function.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy 2 async, pytest (`asyncio_mode=auto`), ruff.

**Spec:** `docs/superpowers/specs/2026-07-19-theme-a-pnl-integrity-design.md`

**Conventions:** Run all commands from the repo root (the worktree). All tests are unit tests — no DB or server needed. Commit after every task with the exact message given. `pytest -q` output ends `N passed` on success.

---

### Task 1: RiskAlert domain model + repository

**Files:**
- Create: `app/common/models/risk.py`
- Modify: `app/common/interfaces/repositories.py` (add abstract class at end of file)
- Modify: `app/modules/portfolio/repository.py` (add class at end of file, extend imports)
- Create: `tests/unit/test_risk_alert_repository.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_risk_alert_repository.py`:

```python
"""PostgresRiskAlertRepository persists RiskAlert domain models as rows."""

import uuid

from app.common.enums import RiskAlertLevel
from app.common.models.risk import RiskAlert
from app.modules.portfolio.repository import PostgresRiskAlertRepository


class StubSession:
    """Captures added rows; flush assigns an id like the DB default would."""

    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()


async def test_create_persists_row_and_returns_alert_with_id():
    session = StubSession()
    repo = PostgresRiskAlertRepository(session)
    alert = RiskAlert(
        level=RiskAlertLevel.CRITICAL,
        source="33333333-3333-3333-3333-333333333333",
        metric="cash",
        current_value=-55473.33,
        threshold=0.0,
        message="growth: cash is negative",
        affected_branches=["growth"],
    )

    saved = await repo.create(alert)

    assert saved.id is not None
    assert len(session.added) == 1
    row = session.added[0]
    assert row.level == "critical"
    assert row.metric == "cash"
    assert row.current_value == -55473.33
    assert row.threshold == 0.0
    assert row.message == "growth: cash is negative"
    assert row.affected_branches == ["growth"]
    assert row.resolved is False


async def test_create_preserves_action_required_none():
    session = StubSession()
    repo = PostgresRiskAlertRepository(session)
    alert = RiskAlert(
        level=RiskAlertLevel.WARNING,
        source="b-1",
        metric="cash_pct",
        current_value=0.08,
        threshold=0.05,
        message="underinvested",
    )
    await repo.create(alert)
    assert session.added[0].action_required is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_risk_alert_repository.py -q`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'app.common.models.risk'` (or ImportError for `PostgresRiskAlertRepository`).

- [ ] **Step 3: Write the implementation**

Create `app/common/models/risk.py`:

```python
"""Domain model for risk alerts (mirrors RiskAlertModel in app/db/models.py)."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.common.enums import RiskAlertLevel


class RiskAlert(BaseModel):
    id: str | None = None
    level: RiskAlertLevel
    source: str  # branch_id, or "global" for fund-level alerts
    metric: str
    current_value: float
    threshold: float
    message: str
    action_required: str | None = None
    affected_branches: list[str] = Field(default_factory=list)
    resolved: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

In `app/common/interfaces/repositories.py`, add to the imports block near the top:

```python
from app.common.models.risk import RiskAlert
```

and append at the end of the file:

```python
class RiskAlertRepository(ABC):
    @abstractmethod
    async def create(self, alert: RiskAlert) -> RiskAlert: ...
```

In `app/modules/portfolio/repository.py`, extend the existing imports — add `RiskAlertRepository` to the `app.common.interfaces.repositories` import, add:

```python
from app.common.models.risk import RiskAlert
```

add `RiskAlertModel` to the `app.db.models` import, and append at the end of the file:

```python
class PostgresRiskAlertRepository(RiskAlertRepository):
    """First writer to the risk_alerts table (dead schema until Theme A1)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, alert: RiskAlert) -> RiskAlert:
        row = RiskAlertModel(
            level=str(alert.level),
            source=alert.source,
            metric=alert.metric,
            current_value=alert.current_value,
            threshold=alert.threshold,
            message=alert.message,
            action_required=alert.action_required,
            affected_branches=alert.affected_branches,
            resolved=alert.resolved,
        )
        self.session.add(row)
        await self.session.flush()
        return alert.model_copy(update={"id": str(row.id)})
```

(Note: `RiskAlertModel.affected_branches` is typed `Mapped[dict | None]` but is a JSONB column — SQLAlchemy serializes a list fine; do not "fix" the model annotation in this task.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_risk_alert_repository.py -q`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add app/common/models/risk.py app/common/interfaces/repositories.py app/modules/portfolio/repository.py tests/unit/test_risk_alert_repository.py
git commit -m "feat(risk): RiskAlert domain model + first risk_alerts repository"
```

---

### Task 2: Invariant check logic

**Files:**
- Create: `app/modules/equities/risk_checks.py`
- Create: `tests/unit/equities/test_risk_checks.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/equities/test_risk_checks.py`:

```python
"""Post-run invariant checks: cash floor, cash-pct ceiling, position cap."""

from app.common.enums import RiskAlertLevel
from app.modules.equities.config import PortfolioConfig
from app.modules.equities.risk_checks import (
    CASH_PCT_WARN,
    POSITION_WEIGHT_TOLERANCE,
    evaluate_post_run_invariants,
)


def _run(cash=10_000.0, nav=1_000_000.0, weights=None, cap=0.50):
    return evaluate_post_run_invariants(
        cash=cash,
        nav=nav,
        position_weights=weights or {},
        portfolio_config=PortfolioConfig(max_position_weight=cap),
        branch_id="b-1",
        branch_name="growth",
    )


def test_clean_portfolio_produces_no_alerts():
    assert _run(cash=10_000.0, weights={"AAPL": 0.05}) == []


def test_negative_cash_is_critical():
    alerts = _run(cash=-55_473.33)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.level == RiskAlertLevel.CRITICAL
    assert a.metric == "cash"
    assert a.current_value == -55_473.33
    assert a.threshold == 0.0
    assert "growth" in a.message
    assert a.affected_branches == ["growth"]


def test_zero_cash_is_fine():
    assert _run(cash=0.0) == []


def test_high_cash_pct_is_warning():
    alerts = _run(cash=60_000.0)  # 6% of 1M > 5%
    assert len(alerts) == 1
    assert alerts[0].level == RiskAlertLevel.WARNING
    assert alerts[0].metric == "cash_pct"
    assert alerts[0].threshold == CASH_PCT_WARN


def test_cash_pct_at_threshold_is_fine():
    assert _run(cash=50_000.0) == []  # exactly 5%: not a breach


def test_position_above_cap_plus_tolerance_is_critical():
    alerts = _run(weights={"BAC": 0.16, "OK": 0.05}, cap=0.10)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.level == RiskAlertLevel.CRITICAL
    assert a.metric == "position_weight"
    assert "BAC" in a.message
    assert a.current_value == 0.16
    assert a.threshold == 0.10


def test_position_within_tolerance_is_fine():
    # cap 0.10 + tolerance 0.005 → 0.104 passes
    assert POSITION_WEIGHT_TOLERANCE == 0.005
    assert _run(weights={"AAPL": 0.104}, cap=0.10) == []


def test_multiple_breaches_stack():
    alerts = _run(cash=-1.0, weights={"A": 0.60, "B": 0.55}, cap=0.50)
    metrics = sorted(a.metric for a in alerts)
    assert metrics == ["cash", "position_weight", "position_weight"]


def test_zero_nav_does_not_divide():
    # cash-pct check requires nav > 0; negative-cash check still fires
    alerts = _run(cash=-5.0, nav=0.0)
    assert [a.metric for a in alerts] == ["cash"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/equities/test_risk_checks.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.modules.equities.risk_checks'`

- [ ] **Step 3: Write the implementation**

Create `app/modules/equities/risk_checks.py`:

```python
"""Post-run portfolio invariant checks (Theme A1).

Pure logic: evaluates marked portfolio state against invariants and returns
RiskAlert drafts. Persistence and digest rendering happen in the weekly CLI.
"""

from __future__ import annotations

import logging

from app.common.enums import RiskAlertLevel
from app.common.models.risk import RiskAlert
from app.modules.equities.config import PortfolioConfig

logger = logging.getLogger(__name__)

# Check parameters — invariant tolerances, not strategy knobs, so they are
# module constants rather than PortfolioConfig fields.
CASH_PCT_WARN = 0.05
POSITION_WEIGHT_TOLERANCE = 0.005


def evaluate_post_run_invariants(
    *,
    cash: float,
    nav: float,
    position_weights: dict[str, float],
    portfolio_config: PortfolioConfig,
    branch_id: str,
    branch_name: str,
) -> list[RiskAlert]:
    """Evaluate the marked book right after a rebalance.

    Invariants: cash must not be negative (long-only cash account), cash must
    not balloon (the signature of mass BUY rejections), and no position may
    exceed the configured cap beyond fill-slippage tolerance.
    """
    alerts: list[RiskAlert] = []

    if cash < 0:
        alerts.append(
            RiskAlert(
                level=RiskAlertLevel.CRITICAL,
                source=branch_id,
                metric="cash",
                current_value=cash,
                threshold=0.0,
                message=f"{branch_name}: cash is negative (${cash:,.2f}) — long-only book must not overdraw",
                action_required="Investigate order sizing/execution (see 2026-06-22 leverage incident).",
                affected_branches=[branch_name],
            )
        )

    if nav > 0 and cash / nav > CASH_PCT_WARN:
        alerts.append(
            RiskAlert(
                level=RiskAlertLevel.WARNING,
                source=branch_id,
                metric="cash_pct",
                current_value=cash / nav,
                threshold=CASH_PCT_WARN,
                message=(
                    f"{branch_name}: cash is {cash / nav:.1%} of NAV — underinvested; possible mass BUY rejections"
                ),
                affected_branches=[branch_name],
            )
        )

    cap = portfolio_config.max_position_weight
    for symbol, weight in sorted(position_weights.items()):
        if weight > cap + POSITION_WEIGHT_TOLERANCE:
            alerts.append(
                RiskAlert(
                    level=RiskAlertLevel.CRITICAL,
                    source=branch_id,
                    metric="position_weight",
                    current_value=weight,
                    threshold=cap,
                    message=f"{branch_name}: {symbol} is {weight:.1%} of NAV (cap {cap:.0%})",
                    affected_branches=[branch_name],
                )
            )

    return alerts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/equities/test_risk_checks.py -q`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add app/modules/equities/risk_checks.py tests/unit/equities/test_risk_checks.py
git commit -m "feat(risk): post-run invariant checks (cash floor, cash-pct, position cap)"
```

---

### Task 3: Alert persistence helper (rows + events)

**Files:**
- Modify: `app/modules/equities/risk_checks.py` (append function)
- Modify: `tests/unit/equities/test_risk_checks.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/equities/test_risk_checks.py`:

```python
from app.modules.equities.risk_checks import persist_alerts  # noqa: E402


class FakeRepo:
    def __init__(self):
        self.created = []

    async def create(self, alert):
        self.created.append(alert)
        return alert


class FakeEventLog:
    def __init__(self):
        self.events = []

    async def append(self, event):
        self.events.append(event)


async def test_persist_alerts_writes_rows_and_events():
    alerts = _run(cash=-1.0, weights={"A": 0.60}, cap=0.50)
    assert len(alerts) == 2
    repo, log = FakeRepo(), FakeEventLog()

    await persist_alerts(alerts, repo=repo, event_log=log)

    assert [a.metric for a in repo.created] == ["cash", "position_weight"]
    assert [e.event_type for e in log.events] == ["risk.alert", "risk.alert"]
    assert log.events[0].metric == "cash"
    assert log.events[0].level == RiskAlertLevel.CRITICAL
    assert log.events[0].affected_branches == ["growth"]


async def test_persist_alerts_empty_is_noop():
    repo, log = FakeRepo(), FakeEventLog()
    await persist_alerts([], repo=repo, event_log=log)
    assert repo.created == [] and log.events == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/equities/test_risk_checks.py -q`
Expected: FAIL with `ImportError: cannot import name 'persist_alerts'`

- [ ] **Step 3: Write the implementation**

Append to `app/modules/equities/risk_checks.py`, and add this import to its import block (below `from app.common.enums import RiskAlertLevel`):

```python
from app.common.events.risk import RiskAlertEvent
```

Then append the function:

```python
async def persist_alerts(alerts: list[RiskAlert], *, repo, event_log) -> None:
    """Persist alerts as risk_alerts rows plus risk.alert event-log entries.

    repo: RiskAlertRepository; event_log: EventLogRepository. The caller owns
    transaction scoping (the weekly CLI wraps this in a savepoint).
    """
    for alert in alerts:
        await repo.create(alert)
        await event_log.append(
            RiskAlertEvent(
                source=alert.source,
                level=alert.level,
                metric=alert.metric,
                current_value=alert.current_value,
                threshold=alert.threshold,
                message=alert.message,
                action_required=alert.action_required,
                affected_branches=alert.affected_branches,
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/equities/test_risk_checks.py -q`
Expected: `11 passed`

- [ ] **Step 5: Commit**

```bash
git add app/modules/equities/risk_checks.py tests/unit/equities/test_risk_checks.py
git commit -m "feat(risk): persist_alerts writes risk_alerts rows + first risk.alert events"
```

---

### Task 4: Digest rendering of risk alerts

**Files:**
- Modify: `app/modules/equities/weekly_runner.py` (`PortfolioReport` dataclass ~line 55; `render_digest` ~lines 446-449)
- Modify: `tests/unit/equities/test_digest_portfolio_report.py` (append tests; reuse its existing report-construction helper if one exists — read the file first)

- [ ] **Step 1: Write the failing tests**

Read `tests/unit/equities/test_digest_portfolio_report.py` first and reuse its helper for building a `WeeklyRunSummary`+`PortfolioReport` if present; otherwise append the following self-contained tests (adjust constructor calls to match the file's existing style):

```python
from datetime import date

from app.modules.equities.weekly_runner import PortfolioReport, WeeklyRunSummary, render_digest


def _summary_with_alerts(alerts):
    report = PortfolioReport(
        nav=1_000_000.0,
        cash=-55_473.33,
        cash_pct=-0.055,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        initial_capital=1_000_000.0,
        inception_return_pct=0.0,
        wow_return_pct=None,
        top_holdings=[],
        trades=[],
        unpriced=0,
        risk_alerts=alerts,
    )
    return WeeklyRunSummary(
        run_id="2026-07-27-growth",
        branch_name="growth",
        status="completed",
        universe_count=50,
        screened_count=25,
        orders_placed=5,
        trades_executed=5,
        duration_seconds=60.0,
        portfolio_report=report,
    )


def test_digest_renders_critical_and_warning_alert_lines():
    s = _summary_with_alerts(
        [
            {"level": "critical", "message": "growth: cash is negative (-$55,473.33)"},
            {"level": "warning", "message": "growth: cash is 6.0% of NAV"},
        ]
    )
    digest = render_digest([s], run_date=date(2026, 7, 27))
    assert "- ❌ CRITICAL: growth: cash is negative (-$55,473.33)" in digest
    assert "- ⚠️ WARNING: growth: cash is 6.0% of NAV" in digest


def test_digest_negative_cash_line_comes_from_alerts_not_hardcode():
    # No alerts attached → no negative-cash line even though cash < 0
    # (the hardcoded hint is superseded by the alert-driven rendering).
    s = _summary_with_alerts([])
    digest = render_digest([s], run_date=date(2026, 7, 27))
    assert "Negative cash balance" not in digest
    assert "CRITICAL" not in digest
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/equities/test_digest_portfolio_report.py -q`
Expected: FAIL — `TypeError: PortfolioReport.__init__() got an unexpected keyword argument 'risk_alerts'`. (Existing tests in the file must still pass once implemented — `risk_alerts` needs a default.)

- [ ] **Step 3: Write the implementation**

In `app/modules/equities/weekly_runner.py`:

1. Change the dataclass import line `from dataclasses import dataclass` to `from dataclasses import dataclass, field`.
2. Add to `PortfolioReport` after `unpriced: int = 0`:

```python
    # [{"level": "critical"|"warning", "message": str}, ...] from risk_checks
    risk_alerts: list[dict] = field(default_factory=list)
```

3. In `render_digest`, replace:

```python
                if r.cash < 0:
                    lines.append("- ⚠️ Negative cash balance — check order sizing")
```

with:

```python
                for alert in r.risk_alerts:
                    icon = "❌ CRITICAL" if alert["level"] == "critical" else "⚠️ WARNING"
                    lines.append(f"- {icon}: {alert['message']}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/equities/test_digest_portfolio_report.py tests/unit/equities/test_weekly_runner.py -q`
Expected: all pass (if an existing test asserted the old "Negative cash balance" string, update it to construct `risk_alerts` and assert the new line instead — that string was the hardcoded hint this task supersedes).

- [ ] **Step 5: Commit**

```bash
git add app/modules/equities/weekly_runner.py tests/unit/equities/test_digest_portfolio_report.py
git commit -m "feat(risk): digest renders invariant breaches; drop hardcoded negative-cash hint"
```

---

### Task 5: Wire risk checks into the weekly CLI

**Files:**
- Modify: `scripts/run_weekly_pipeline.py` (`_mark_snapshot_and_report`, and its call in `_run_one_branch`)

No new unit test: this is thin script glue in the same style as the rest of `_mark_snapshot_and_report` (repo convention — the logic is fully covered by Tasks 2-4). The function already never raises.

- [ ] **Step 1: Extend the imports**

In `scripts/run_weekly_pipeline.py` add to the existing import block:

```python
from app.modules.equities.risk_checks import evaluate_post_run_invariants, persist_alerts  # noqa: E402
from app.modules.portfolio.repository import PostgresRiskAlertRepository  # noqa: E402
```

(`PostgresRiskAlertRepository` joins the existing `from app.modules.portfolio.repository import (...)` block.)

- [ ] **Step 2: Pass the portfolio config through**

In `_run_one_branch`, extend the `_mark_snapshot_and_report(...)` call with one argument:

```python
        await _mark_snapshot_and_report(
            branch_id=branch_id,
            branch_name=branch_name,
            data_service=equities_service.data_service,
            summary=summary,
            run_started_at=run_started_at,
            portfolio_config=equities_service.config.portfolio,
        )
```

and add the parameter to the signature of `_mark_snapshot_and_report`:

```python
async def _mark_snapshot_and_report(
    *,
    branch_id: str,
    branch_name: str,
    data_service,
    summary: WeeklyRunSummary,
    run_started_at: datetime,
    portfolio_config,
) -> None:
```

- [ ] **Step 3: Evaluate + persist after the report is built**

Inside the `async with ... session.begin():` block, immediately after `report = PortfolioReport(...)` is constructed (still inside the session block), add:

```python
            # Theme A1: post-run invariant checks — completed runs only
            # (a skipped idempotent rerun must not duplicate alert rows).
            if summary.status == "completed":
                weights = {d["symbol"]: d["weight"] for d in mtm.positions_detail}
                alerts = evaluate_post_run_invariants(
                    cash=mtm.cash,
                    nav=mtm.nav,
                    position_weights=weights,
                    portfolio_config=portfolio_config,
                    branch_id=branch_id,
                    branch_name=branch_name,
                )
                report.risk_alerts = [{"level": str(a.level), "message": a.message} for a in alerts]
                if alerts:
                    try:
                        # Savepoint: a persistence failure must not roll back
                        # the snapshot/mark in the enclosing transaction.
                        async with session.begin_nested():
                            await persist_alerts(
                                alerts,
                                repo=PostgresRiskAlertRepository(session),
                                event_log=event_log,
                            )
                    except Exception:
                        logger.warning(
                            "Risk alert persistence failed for %s — digest still shows them",
                            branch_name,
                            exc_info=True,
                        )
                        report.risk_alerts.append(
                            {"level": "warning", "message": "Risk alert persistence failed — see run logs"}
                        )
```

- [ ] **Step 4: Verify nothing broke**

Run: `pytest tests/unit/test_weekly_cli_ordering.py tests/unit/equities/test_weekly_runner.py -q`
Expected: all pass.
Run: `python -c "import scripts.run_weekly_pipeline"`
Expected: no output, exit 0 (imports resolve).

- [ ] **Step 5: Commit**

```bash
git add scripts/run_weekly_pipeline.py
git commit -m "feat(risk): weekly CLI evaluates + persists post-run invariant alerts"
```

---

### Task 6: Sell-all full exits in generate_orders

**Files:**
- Modify: `app/modules/equities/agents/portfolio_manager.py` (`generate_orders`)
- Modify: `app/modules/equities/service.py` (portfolio-read block ~line 153 and the `deps` dict ~line 318)
- Modify: `app/modules/equities/agents/graph.py` (`portfolio_decision`, ~line 234)
- Modify: `tests/unit/equities/test_portfolio_manager.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/equities/test_portfolio_manager.py` (match the file's existing construction style for `PortfolioManager`; the default configs work — `AgentsConfig()` weights sum to 1.0):

```python
def test_full_exit_sells_entire_held_quantity_even_below_threshold():
    pm = PortfolioManager(agents_config=AgentsConfig(), portfolio_config=PortfolioConfig())
    orders = pm.generate_orders(
        target=[],  # name no longer selected
        current_positions={"KLAC": 0.00005},  # $55 of $1M — far below 2% band
        nav=1_000_000.0,
        prices={"KLAC": 55.0},
        current_quantities={"KLAC": 1.0},
    )
    assert len(orders) == 1
    o = orders[0]
    assert (o.symbol, str(o.side), o.reason) == ("KLAC", "sell", "removed_position")
    assert o.quantity == 1.0  # exact held quantity, not delta*nav/price


def test_full_exit_uses_held_quantity_not_weight_math():
    pm = PortfolioManager(agents_config=AgentsConfig(), portfolio_config=PortfolioConfig())
    orders = pm.generate_orders(
        target=[],
        current_positions={"SCHW": 0.13},
        nav=1_000_000.0,
        prices={"SCHW": 92.0889},
        current_quantities={"SCHW": 1434.8511},
    )
    assert orders[0].quantity == 1434.8511


def test_full_exit_without_price_is_skipped():
    pm = PortfolioManager(agents_config=AgentsConfig(), portfolio_config=PortfolioConfig())
    orders = pm.generate_orders(
        target=[],
        current_positions={"KLAC": 0.00005},
        nav=1_000_000.0,
        prices={},  # unpriced → stays, visible via digest 'unpriced'
        current_quantities={"KLAC": 1.0},
    )
    assert orders == []


def test_legacy_call_without_quantities_keeps_threshold_behavior():
    pm = PortfolioManager(agents_config=AgentsConfig(), portfolio_config=PortfolioConfig())
    orders = pm.generate_orders(
        target=[],
        current_positions={"KLAC": 0.00005},  # below 2% → silently kept (old behavior)
        nav=1_000_000.0,
        prices={"KLAC": 55.0},
    )
    assert orders == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/equities/test_portfolio_manager.py -q`
Expected: the four new tests FAIL (`TypeError: ... unexpected keyword argument 'current_quantities'`); existing tests still pass.

- [ ] **Step 3: Implement generate_orders**

In `app/modules/equities/agents/portfolio_manager.py`, replace the whole `generate_orders` method with:

```python
    def generate_orders(
        self,
        target: list[CompositeScore],
        current_positions: dict[str, float],
        nav: float,
        prices: dict[str, float],
        current_quantities: dict[str, float] | None = None,
    ) -> list[RebalanceOrder]:
        """Generate BUY/SELL orders by diffing target vs current portfolio.

        current_quantities (symbol -> held share count) enables full exits: a
        held name with target weight 0 sells its ENTIRE held quantity,
        bypassing the rebalance threshold — no fractional dust survives an
        exit. Callers that omit it get the legacy delta-only behavior.
        """
        orders = []
        threshold = self.portfolio_config.min_rebalance_threshold
        target_map = {s.symbol: s.target_weight for s in target}
        # sorted() is required: iterating a set of symbol strings produces
        # hash-randomized order (PYTHONHASHSEED), which causes the resulting
        # orders list to vary across runs. Downstream execution consumes cash
        # and participation budget in list order, so this flips trade outcomes.
        all_symbols = sorted(set(target_map.keys()) | set(current_positions.keys()))
        for symbol in all_symbols:
            target_weight = target_map.get(symbol, 0.0)
            current_weight = current_positions.get(symbol, 0.0)
            price = prices.get(symbol)
            held = (current_quantities or {}).get(symbol, 0.0)
            if target_weight == 0.0 and current_weight > 0.0 and held > 0.0:
                # Full exit: sell exactly what is held. Unpriced names are
                # still skipped — execution could not fill them anyway and
                # they stay visible via the digest 'unpriced' warning.
                if not price or price <= 0:
                    continue
                orders.append(
                    RebalanceOrder(
                        symbol=symbol,
                        side="sell",
                        quantity=held,
                        reason="removed_position",
                    )
                )
                continue
            delta = target_weight - current_weight
            if abs(delta) < threshold:
                continue
            if not price or price <= 0:
                continue
            quantity = round(abs(delta * nav) / price, 4)
            if quantity == 0:
                continue
            if delta > 0:
                side = "buy"
                reason = "new_position" if current_weight == 0.0 else "weight_adjustment"
            else:
                side = "sell"
                reason = "removed_position" if target_weight == 0.0 else "weight_adjustment"
            orders.append(
                RebalanceOrder(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    reason=reason,
                )
            )
        # Sells first so proceeds fund the buys; alphabetical within side keeps
        # the deterministic ordering downstream execution depends on.
        orders.sort(key=lambda o: (0 if o.side == OrderSide.SELL else 1, o.symbol))
        return orders
```

- [ ] **Step 4: Plumb quantities from the service through the graph**

In `app/modules/equities/service.py`, in the portfolio-read block (the `# --- Gap 2 ...` section, ~line 153), add a `current_quantities` dict built in the same loop as `current_positions`:

```python
        current_positions: dict[str, float] = {}
        current_quantities: dict[str, float] = {}
```

and inside the `for pos in portfolio.positions:` loop, as the first line under the `if pos.long_quantity > 0 and nav > 0:` guard:

```python
                            current_quantities[pos.symbol] = float(pos.long_quantity)
```

Then in the `deps` dict (~line 318, next to `"current_positions": current_positions,`):

```python
                "current_quantities": current_quantities,
```

In `app/modules/equities/agents/graph.py` `portfolio_decision`, add after `current_positions = deps.get("current_positions", {})`:

```python
        current_quantities = deps.get("current_quantities")
```

and change the order-generation call to:

```python
        orders = pm.generate_orders(sized, current_positions, nav, prices, current_quantities=current_quantities)
```

- [ ] **Step 5: Run the affected suites**

Run: `pytest tests/unit/equities/ -q`
Expected: all pass (graph tests that omit `current_quantities` fall back to legacy behavior via the `.get()` default).

- [ ] **Step 6: Commit**

```bash
git add app/modules/equities/agents/portfolio_manager.py app/modules/equities/service.py app/modules/equities/agents/graph.py tests/unit/equities/test_portfolio_manager.py
git commit -m "feat(pm): full exits sell the entire held quantity — dust dies at the source"
```

---

### Task 7: 0.5% entry threshold

**Files:**
- Modify: `app/modules/equities/config.py` (`PortfolioConfig`, ~line 38)
- Modify: `app/modules/equities/agents/portfolio_manager.py` (`generate_orders` threshold logic; add module logger)
- Modify: `tests/unit/equities/test_portfolio_manager.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/equities/test_portfolio_manager.py`:

```python
def _score(symbol, weight):
    return CompositeScore(
        symbol=symbol,
        composite_score=6.0,
        composite_confidence=6.0,
        conviction=36.0,
        target_weight=weight,
    )


def test_entry_between_half_and_two_percent_now_trades():
    pm = PortfolioManager(agents_config=AgentsConfig(), portfolio_config=PortfolioConfig())
    orders = pm.generate_orders(
        target=[_score("NEW", 0.01)],  # 1% entry: below old 2% band, above 0.5%
        current_positions={},
        nav=1_000_000.0,
        prices={"NEW": 100.0},
    )
    assert len(orders) == 1
    assert (orders[0].symbol, str(orders[0].side), orders[0].reason) == ("NEW", "buy", "new_position")
    assert orders[0].quantity == 100.0  # 0.01 * 1M / 100


def test_entry_below_half_percent_still_skipped():
    pm = PortfolioManager(agents_config=AgentsConfig(), portfolio_config=PortfolioConfig())
    orders = pm.generate_orders(
        target=[_score("TINY", 0.004)],
        current_positions={},
        nav=1_000_000.0,
        prices={"TINY": 100.0},
    )
    assert orders == []


def test_weight_adjustment_keeps_two_percent_band():
    pm = PortfolioManager(agents_config=AgentsConfig(), portfolio_config=PortfolioConfig())
    orders = pm.generate_orders(
        target=[_score("HELD", 0.05)],
        current_positions={"HELD": 0.039},  # delta 1.1% < 2% band
        nav=1_000_000.0,
        prices={"HELD": 100.0},
    )
    assert orders == []


def test_min_entry_weight_default_is_half_percent():
    assert PortfolioConfig().min_entry_weight == 0.005
```

(`CompositeScore` is already imported by the file's existing tests; if not, import it from `app.modules.equities.models`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/equities/test_portfolio_manager.py -q`
Expected: new tests FAIL (`AttributeError: ... no attribute 'min_entry_weight'` / first test gets 0 orders).

- [ ] **Step 3: Implement**

In `app/modules/equities/config.py`, add to `PortfolioConfig` after `min_rebalance_threshold`:

```python
    # Entries trade down to 0.5% targets; the 2% band above applies only to
    # adjustments of names already held (2026-07-19 Theme A3 decision).
    min_entry_weight: float = 0.005
```

In `app/modules/equities/agents/portfolio_manager.py`:

1. Add at module top (below the existing imports):

```python
import logging

logger = logging.getLogger(__name__)
```

(ruff's isort will want `import logging` above the `from` imports — put it first.)

2. In `generate_orders`, delete the pre-loop line `threshold = self.portfolio_config.min_rebalance_threshold` and replace the delta/threshold block inside the loop:

```python
            delta = target_weight - current_weight
            if abs(delta) < threshold:
                continue
```

with:

```python
            delta = target_weight - current_weight
            is_entry = current_weight == 0.0
            threshold = (
                self.portfolio_config.min_entry_weight if is_entry else self.portfolio_config.min_rebalance_threshold
            )
            if abs(delta) < threshold:
                if is_entry and target_weight > 0.0:
                    logger.info(
                        "Skipping sub-threshold entry %s: target %.3%% < %.3%%",
                        symbol,
                        target_weight * 100,
                        threshold * 100,
                    )
                continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/equities/test_portfolio_manager.py tests/unit/equities/test_order_generation_cash.py tests/unit/equities/test_config.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/modules/equities/config.py app/modules/equities/agents/portfolio_manager.py tests/unit/equities/test_portfolio_manager.py
git commit -m "feat(pm): entries trade down to 0.5% targets; 2% band now adjustment-only"
```

---

### Task 8: SELL tolerance + full-exit clamp in trade execution

**Files:**
- Modify: `app/modules/trade_execution/service.py` (`submit_order` ~line 92, `_validate_order` ~line 236)
- Create: `tests/unit/test_trade_execution_sequence.py` (fakes + clamp tests; Task 9 appends the 06-22 scenarios)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_trade_execution_sequence.py`:

```python
"""Execution-sequence regressions: full-exit clamp + the 2026-06-22 incident.

Fakes mirror tests/unit/test_trade_execution_cash_check.py but track per-symbol
positions and a price map so multi-symbol sequences can be replayed.
"""

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


class FakePriceMapBroker:
    """Fills every order at the symbol's mapped price (no slippage/commission)."""

    def __init__(self, prices: dict[str, float]):
        self.prices = prices

    async def submit_order(self, req):
        return OrderResult(
            success=True,
            trade=Trade(
                id=f"t-{req.symbol}",
                order_id="",
                branch_id=req.branch_id,
                instrument_id=req.instrument_id,
                symbol=req.symbol,
                side=req.side,
                quantity=req.quantity,
                price=self.prices[req.symbol],
                commission=0.0,
                slippage=0.0,
                execution_mode="paper",
                executed_at=datetime.now(UTC),
            ),
        )


class _Pos:
    def __init__(self, qty):
        self.long_quantity = qty


class FakeBookPortfolioService:
    """Tracks cash and per-symbol long quantities; records the cash low-water mark."""

    def __init__(self, cash: float, positions: dict[str, float]):
        self._cash = cash
        self.positions = dict(positions)
        self.min_cash_seen = cash

    async def get_portfolio(self, branch_id):
        class S:
            pass

        s = S()
        s.cash = self._cash
        return s

    async def get_position_by_symbol(self, branch_id, symbol):
        qty = self.positions.get(symbol)
        return _Pos(qty) if qty is not None else None

    async def handle_trade_executed(self, trade):
        if trade.side == OrderSide.BUY:
            self._cash -= trade.price * trade.quantity + trade.commission
            self.positions[trade.symbol] = self.positions.get(trade.symbol, 0.0) + trade.quantity
        elif trade.side == OrderSide.SELL:
            self._cash += trade.price * trade.quantity - trade.commission
            remaining = self.positions.get(trade.symbol, 0.0) - trade.quantity
            if remaining == 0.0:
                del self.positions[trade.symbol]  # delete_if_flat
            else:
                self.positions[trade.symbol] = remaining
        self.min_cash_seen = min(self.min_cash_seen, self._cash)


class FakeEventLog:
    def __init__(self):
        self.events = []

    async def append(self, event):
        self.events.append(event)


def _svc(cash, prices, positions=None):
    return TradeExecutionService(
        order_repo=FakeOrderRepo(),
        trade_repo=FakeTradeRepo(),
        broker=FakePriceMapBroker(prices),
        event_log=FakeEventLog(),
        portfolio_service=FakeBookPortfolioService(cash, positions or {}),
    )


def _req(symbol, side, qty):
    return OrderRequest(
        branch_id="b-1",
        instrument_id=f"in-{symbol}",
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        quantity=qty,
    )


async def test_sell_a_hair_above_held_clamps_to_full_exit():
    held = 435.0051
    svc = _svc(cash=0.0, prices={"ACN": 120.0}, positions={"ACN": held})
    result = await svc.submit_order(_req("ACN", OrderSide.SELL, held + 5e-7))
    assert result["success"] is True
    assert svc.trade_repo.created[0].quantity == held  # clamped to exactly held
    assert "ACN" not in svc.portfolio_service.positions  # position closed flat


async def test_sell_a_hair_below_held_clamps_up_to_full_exit():
    held = 435.0051
    svc = _svc(cash=0.0, prices={"ACN": 120.0}, positions={"ACN": held})
    result = await svc.submit_order(_req("ACN", OrderSide.SELL, held - 5e-7))
    assert result["success"] is True
    assert svc.trade_repo.created[0].quantity == held
    assert "ACN" not in svc.portfolio_service.positions  # no 5e-7 dust row


async def test_sell_materially_above_held_is_rejected():
    svc = _svc(cash=0.0, prices={"ACN": 120.0}, positions={"ACN": 10.0})
    result = await svc.submit_order(_req("ACN", OrderSide.SELL, 10.001))
    assert result["success"] is False
    assert "Insufficient position" in result["message"]


async def test_partial_sell_is_not_clamped():
    svc = _svc(cash=0.0, prices={"ACN": 120.0}, positions={"ACN": 10.0})
    result = await svc.submit_order(_req("ACN", OrderSide.SELL, 4.0))
    assert result["success"] is True
    assert svc.trade_repo.created[0].quantity == 4.0
    assert svc.portfolio_service.positions["ACN"] == pytest.approx(6.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_trade_execution_sequence.py -q`
Expected: `test_sell_a_hair_above_held_clamps_to_full_exit` FAILS (rejected: "Insufficient position"), `test_sell_a_hair_below_held_clamps_up_to_full_exit` FAILS (quantity not clamped / dust remains). The other two pass already.

- [ ] **Step 3: Implement tolerance + clamp**

In `app/modules/trade_execution/service.py`:

1. Add a module constant below the imports:

```python
# Full-exit quantities travel through Decimal->float conversions; treat a
# quantity within this tolerance of the held quantity as "sell everything".
FULL_EXIT_QTY_TOLERANCE = 1e-6
```

2. In `submit_order`, immediately after the validation block (after the `if validation_error: return ...`) and BEFORE the `order = Order(...)` construction, add:

```python
        # Clamp near-held SELL quantities to exactly the held quantity so a
        # full exit closes the position to exactly zero (delete_if_flat fires)
        # and can never trip handle_trade_executed's oversell guard. Must
        # happen before the Order row is created so order and trade agree.
        if req.side == OrderSide.SELL:
            position = await self.portfolio_service.get_position_by_symbol(req.branch_id, req.symbol)
            if position is not None:
                held = position.long_quantity
                if held > 0 and abs(req.quantity - held) <= FULL_EXIT_QTY_TOLERANCE and req.quantity != held:
                    req = req.model_copy(update={"quantity": held})
```

3. In `_validate_order`, replace the SELL branch:

```python
        elif req.side == OrderSide.SELL:
            position = await self.portfolio_service.get_position_by_symbol(req.branch_id, req.symbol)
            if position is None or position.long_quantity < req.quantity:
                held = position.long_quantity if position else 0
                return f"Insufficient position: hold {held} {req.symbol}, tried to sell {req.quantity}"
```

with:

```python
        elif req.side == OrderSide.SELL:
            position = await self.portfolio_service.get_position_by_symbol(req.branch_id, req.symbol)
            held = position.long_quantity if position else 0
            if position is None or req.quantity > held + FULL_EXIT_QTY_TOLERANCE:
                return f"Insufficient position: hold {held} {req.symbol}, tried to sell {req.quantity}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_trade_execution_sequence.py tests/unit/test_trade_execution_cash_check.py tests/unit/test_trade_execution_service.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/modules/trade_execution/service.py tests/unit/test_trade_execution_sequence.py
git commit -m "feat(exec): 1e-6 SELL tolerance + full-exit clamp so positions close exactly flat"
```

---

### Task 9: 2026-06-22 sequence regression tests

**Files:**
- Modify: `tests/unit/test_trade_execution_sequence.py` (append — uses Task 8's fakes)

Test-only task: it pins the incident against the CURRENT code (gate + sells-first). All three scenarios must pass without production changes; a failure means the gate regressed.

- [ ] **Step 1: Write the tests**

Append to `tests/unit/test_trade_execution_sequence.py`:

```python
# ---------------------------------------------------------------------------
# 2026-06-22 incident regression: the value branch filled $368k of buys BEFORE
# its $132k sell (alphabetical execution, no cash gate) and ran cash to
# -$236,553 (~24% unintended leverage). These scenarios replay the real order
# set and assert the fill-time cash gate + sells-first ordering prevent it.
# Real fills from prod: ACN 435.0051@120.8804, BLK 19.8133@1055.6726,
# CRM 829.952@147.9439, DIS 821.6356@101.6608, T 3984.7789@22.1776,
# SELL SCHW 1434.8511@92.0889; starting cash -499.75.
# ---------------------------------------------------------------------------

_JUN22_PRICES = {
    "ACN": 120.8804,
    "BLK": 1055.6726,
    "CRM": 147.9439,
    "DIS": 101.6608,
    "SCHW": 92.0889,
    "T": 22.1776,
}
_JUN22_ORDERS = {
    "ACN": ("buy", 435.0051),
    "BLK": ("buy", 19.8133),
    "CRM": ("buy", 829.952),
    "DIS": ("buy", 821.6356),
    "SCHW": ("sell", 1434.8511),
    "T": ("buy", 3984.7789),
}
_JUN22_START_CASH = -499.75
_SCHW_PROCEEDS = 1434.8511 * 92.0889  # ≈ 132,133.86


def _jun22_svc():
    return _svc(
        cash=_JUN22_START_CASH,
        prices=_JUN22_PRICES,
        positions={"SCHW": 1434.8511},
    )


async def _submit_all(svc, symbols):
    results = {}
    for sym in symbols:
        side, qty = _JUN22_ORDERS[sym]
        results[sym] = await svc.submit_order(
            _req(sym, OrderSide.BUY if side == "buy" else OrderSide.SELL, qty)
        )
    return results


async def test_jun22_alphabetical_replay_gate_blocks_the_overdraft():
    """Historical submission order (alphabetical). Without the gate this ran
    cash to -$236,553; with it, every buy ahead of the sell is rejected and
    cash never drops below its starting value."""
    svc = _jun22_svc()
    results = await _submit_all(svc, ["ACN", "BLK", "CRM", "DIS", "SCHW", "T"])

    assert results["ACN"]["success"] is False  # cash was -499.75
    assert results["BLK"]["success"] is False
    assert results["CRM"]["success"] is False
    assert results["DIS"]["success"] is False
    assert results["SCHW"]["success"] is True  # sells ignore the cash gate
    assert results["T"]["success"] is True  # funded by the sell proceeds

    book = svc.portfolio_service
    assert book.min_cash_seen == _JUN22_START_CASH  # never went lower
    expected_final = _JUN22_START_CASH + _SCHW_PROCEEDS - 3984.7789 * 22.1776
    assert book._cash == pytest.approx(expected_final, abs=0.01)  # ≈ +43,260


async def test_jun22_sells_first_replay_funds_buys_until_cash_runs_out():
    """Current generate_orders ordering (sells first, alphabetical within
    side). The order SET is unfundable — the gate converts what used to be
    -$236k of leverage into rejections of the unaffordable tail."""
    svc = _jun22_svc()
    results = await _submit_all(svc, ["SCHW", "ACN", "BLK", "CRM", "DIS", "T"])

    assert results["SCHW"]["success"] is True
    assert results["ACN"]["success"] is True  # 52,584 ≤ 131,634
    assert results["BLK"]["success"] is True  # 20,916 ≤ 79,050
    assert results["CRM"]["success"] is False  # 122,787 > 58,134
    assert results["DIS"]["success"] is False
    assert results["T"]["success"] is False

    book = svc.portfolio_service
    assert book.min_cash_seen == _JUN22_START_CASH
    expected_final = (
        _JUN22_START_CASH + _SCHW_PROCEEDS - 435.0051 * 120.8804 - 19.8133 * 1055.6726
    )
    assert book._cash == pytest.approx(expected_final, abs=0.01)  # ≈ +58,134


async def test_deleverage_from_negative_cash_ends_non_negative():
    """The 2026-07-20 situation: an overdrawn book (growth cash -$55,473) with
    a net-selling rebalance delevers cleanly — sells first, then buys fit."""
    svc = _svc(
        cash=-55_473.33,
        prices={"AAA": 100.0, "BBB": 100.0},
        positions={"AAA": 1_000.0, "BBB": 500.0},
    )
    sell = await svc.submit_order(_req("AAA", OrderSide.SELL, 700.0))  # +70,000
    buy = await svc.submit_order(_req("BBB", OrderSide.BUY, 100.0))  # -10,000

    assert sell["success"] is True and buy["success"] is True
    book = svc.portfolio_service
    assert book._cash == pytest.approx(4_526.67, abs=0.01)
    assert book._cash >= 0
```

- [ ] **Step 2: Run the tests — they must pass against current code**

Run: `pytest tests/unit/test_trade_execution_sequence.py -q`
Expected: all pass (Task 8 tests + these three). If any 06-22 scenario fails, STOP — that is a real gate/ordering regression, not a test problem.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_trade_execution_sequence.py
git commit -m "test(exec): pin the 2026-06-22 negative-cash incident as sequence regressions"
```

---

### Task 10: NAV hard error (no fictional $1M)

**Files:**
- Modify: `app/modules/equities/service.py` (portfolio-read block ~lines 153-176, extract to module function)
- Modify: `app/modules/equities/agents/graph.py` (`portfolio_decision` nav default, ~line 220)
- Create: `tests/unit/equities/test_service_portfolio_state.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/equities/test_service_portfolio_state.py`:

```python
"""read_portfolio_state hard-fails instead of sizing against a fictional NAV."""

import pytest

from app.modules.equities.service import read_portfolio_state


class _Pos:
    def __init__(self, symbol, qty, cost_basis):
        self.symbol = symbol
        self.long_quantity = qty
        self.long_cost_basis = cost_basis


class _Portfolio:
    def __init__(self, nav, positions):
        self.nav = nav
        self.positions = positions


class FakePS:
    def __init__(self, portfolio=None, raises=False):
        self._portfolio = portfolio
        self._raises = raises

    async def get_portfolio(self, branch_id):
        if self._raises:
            raise ConnectionError("db down")
        return self._portfolio


class FakeDataService:
    def __init__(self, prices):
        self._prices = prices

    async def get_current_price(self, symbol):
        return self._prices.get(symbol)


async def test_missing_portfolio_service_raises():
    with pytest.raises(RuntimeError, match="Portfolio service unavailable"):
        await read_portfolio_state(None, None, branch_id="b-1", branch_name="growth")


async def test_missing_portfolio_row_raises():
    with pytest.raises(RuntimeError, match="No portfolio row"):
        await read_portfolio_state(FakePS(portfolio=None), None, branch_id="b-1", branch_name="growth")


async def test_read_failure_raises_with_cause():
    with pytest.raises(RuntimeError, match="Portfolio read failed") as excinfo:
        await read_portfolio_state(FakePS(raises=True), None, branch_id="b-1", branch_name="growth")
    assert isinstance(excinfo.value.__cause__, ConnectionError)


async def test_non_positive_nav_raises():
    ps = FakePS(portfolio=_Portfolio(nav=0.0, positions=[]))
    with pytest.raises(RuntimeError, match="Non-positive NAV"):
        await read_portfolio_state(ps, None, branch_id="b-1", branch_name="growth")


async def test_empty_book_is_valid_first_run():
    ps = FakePS(portfolio=_Portfolio(nav=1_000_000.0, positions=[]))
    nav, weights, quantities = await read_portfolio_state(ps, None, branch_id="b-1", branch_name="growth")
    assert nav == 1_000_000.0
    assert weights == {} and quantities == {}


async def test_priced_position_weighted_at_market_value():
    ps = FakePS(portfolio=_Portfolio(nav=1_000_000.0, positions=[_Pos("AAPL", 100.0, 40_000.0)]))
    data = FakeDataService({"AAPL": 500.0})
    nav, weights, quantities = await read_portfolio_state(ps, data, branch_id="b-1", branch_name="growth")
    assert weights == {"AAPL": pytest.approx(0.05)}  # 100 * 500 / 1M
    assert quantities == {"AAPL": 100.0}


async def test_unpriced_position_falls_back_to_cost_basis_weight():
    ps = FakePS(portfolio=_Portfolio(nav=1_000_000.0, positions=[_Pos("AAPL", 100.0, 40_000.0)]))
    data = FakeDataService({})
    nav, weights, quantities = await read_portfolio_state(ps, data, branch_id="b-1", branch_name="growth")
    assert weights == {"AAPL": pytest.approx(0.04)}  # total cost basis / nav
    assert quantities == {"AAPL": 100.0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/equities/test_service_portfolio_state.py -q`
Expected: FAIL with `ImportError: cannot import name 'read_portfolio_state'`

- [ ] **Step 3: Implement**

In `app/modules/equities/service.py`, add a module-level function (place it above the `EquitiesBranchService` class):

```python
async def read_portfolio_state(
    ps,
    data_service,
    *,
    branch_id: str,
    branch_name: str,
) -> tuple[float, dict[str, float], dict[str, float]]:
    """Read (nav, current weight map, held-quantity map) for a branch.

    Hard-fails on unreadable state: sizing a rebalance against a fictional
    default NAV (the old 1_000_000.0 fallback) with an empty holdings map
    would regenerate the whole book as buys. A genuinely empty book on a
    successful read is valid (first run).
    """
    if ps is None:
        raise RuntimeError(
            f"Portfolio service unavailable for branch {branch_name} — refusing to size against a fictional NAV"
        )
    try:
        portfolio = await ps.get_portfolio(branch_id)
    except Exception as exc:
        raise RuntimeError(f"Portfolio read failed for branch {branch_name}") from exc
    if portfolio is None:
        raise RuntimeError(f"No portfolio row for branch {branch_name} ({branch_id})")
    nav = float(portfolio.nav)
    if nav <= 0:
        raise RuntimeError(f"Non-positive NAV {nav} for branch {branch_name}")

    current_positions: dict[str, float] = {}
    current_quantities: dict[str, float] = {}
    for pos in portfolio.positions:
        if pos.long_quantity > 0:
            current_quantities[pos.symbol] = float(pos.long_quantity)
            price = None
            if data_service:
                price = await data_service.get_current_price(pos.symbol)
            if price:
                # Market value so weights reflect current allocation
                current_positions[pos.symbol] = (price * float(pos.long_quantity)) / nav
            else:
                # long_cost_basis is TOTAL dollars (not per-share)
                current_positions[pos.symbol] = pos.long_cost_basis / nav
    return nav, current_positions, current_quantities
```

Then replace the whole `# --- Gap 2: Read real portfolio state ---` block in `run_pipeline` (from the comment through the `except Exception:` fallback, currently ~lines 153-176 — including the Task 6 edits made there) with:

```python
        # --- Gap 2: Read real portfolio state (hard-fails; no fictional NAV) ---
        nav, current_positions, current_quantities = await read_portfolio_state(
            ps, self.data_service, branch_id=branch_id, branch_name=branch_name
        )
```

In `app/modules/equities/agents/graph.py` `portfolio_decision`, replace:

```python
        nav = deps.get("nav", 1_000_000.0)
```

with:

```python
        if "nav" not in deps:
            raise RuntimeError("portfolio_decision requires deps['nav'] — refusing to size against a default NAV")
        nav = deps["nav"]
```

- [ ] **Step 4: Run the full unit suite and fix fallout**

Run: `pytest tests/unit/ -q`
Expected: the new tests pass. Some existing tests may fail for exactly two legitimate reasons — fix ONLY these patterns:
- a graph/pipeline test builds `deps` without `"nav"` → add a real value (e.g. `"nav": 1_000_000.0`) to that test's deps dict;
- a service/pipeline test relied on running without a portfolio service or portfolio row → give it a fake portfolio service returning a portfolio with `nav > 0` (pattern in `tests/unit/equities/test_service_portfolio_state.py`), or assert the new `RuntimeError`.

If a failure doesn't match those patterns, STOP and investigate — do not weaken production code to make a test pass.

- [ ] **Step 5: Commit**

```bash
git add app/modules/equities/service.py app/modules/equities/agents/graph.py tests/unit/equities/test_service_portfolio_state.py tests/unit/
git commit -m "feat(service): hard-fail on unreadable portfolio state — no fictional 1M NAV"
```

---

### Task 11: Leverage disclosure note in report.json

**Files:**
- Modify: `scripts/build_report_json.py` (extract fund summary into a pure helper + add notes)
- Modify: `tests/unit/test_report_json_helpers.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_report_json_helpers.py`:

```python
import pytest  # noqa: E402  (skip if the file already imports pytest)

from scripts.build_report_json import FUND_NOTES, build_fund_summary  # noqa: E402


def test_fund_summary_totals_and_notes():
    branches = {
        "growth": {"initial_capital": 1_000_000.0, "nav": 1_009_640.78},
        "value": {"initial_capital": 1_000_000.0, "nav": 1_028_440.77},
    }
    fund = build_fund_summary(branches)
    assert fund["initial_capital"] == pytest.approx(2_000_000.0)
    assert fund["nav"] == pytest.approx(2_038_081.55)
    assert fund["total_pnl"] == pytest.approx(38_081.55)
    assert fund["total_return_pct"] == pytest.approx(38_081.55 / 2_000_000.0)
    assert fund["notes"] == FUND_NOTES


def test_fund_notes_disclose_the_leverage_window():
    assert len(FUND_NOTES) == 1
    note = FUND_NOTES[0]
    assert note["period"] == "2026-06-15/2026-07-20"
    assert "negative cash" in note["note"]
    assert "2026-07-16" in note["note"]


def test_fund_summary_zero_capital_yields_none_returns():
    fund = build_fund_summary({"g": {"initial_capital": 0.0, "nav": 0.0}})
    assert fund["total_pnl"] is None
    assert fund["total_return_pct"] is None
    assert fund["notes"] == FUND_NOTES
```

(If the existing file imports from `scripts.build_report_json` differently, match its import style.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_report_json_helpers.py -q`
Expected: FAIL with `ImportError: cannot import name 'FUND_NOTES'`

- [ ] **Step 3: Implement**

In `scripts/build_report_json.py`, add below `dedupe_last_per_day`:

```python
# Standing investor-reporting disclosures. Rebuilds are wholesale, so notes
# must live in code to survive every regeneration.
FUND_NOTES = [
    {
        "period": "2026-06-15/2026-07-20",
        "note": (
            "Execution before 2026-07-16 could fill buys ahead of sells with no "
            "fill-time cash check; branches ran negative cash (unintended leverage — "
            "the value branch peaked near -24% of NAV in the week of 2026-06-22). "
            "Fixed on 2026-07-16 (fill-time cash gate + sells-first ordering). "
            "Returns in this window reflect exposure above 100% of allocated capital."
        ),
    },
]


def build_fund_summary(branches: dict) -> dict:
    """Fund-level rollup across branch payloads, including standing notes."""
    totals_initial = sum(b["initial_capital"] for b in branches.values())
    totals_nav = sum(b["nav"] for b in branches.values())
    return {
        "initial_capital": totals_initial,
        "nav": totals_nav,
        "total_pnl": totals_nav - totals_initial if totals_initial > 0 else None,
        "total_return_pct": ((totals_nav - totals_initial) / totals_initial if totals_initial > 0 else None),
        "notes": FUND_NOTES,
    }
```

Then in `_main_async`, replace:

```python
    totals_initial = sum(b["initial_capital"] for b in payload["branches"].values())
    totals_nav = sum(b["nav"] for b in payload["branches"].values())
    payload["fund"] = {
        "initial_capital": totals_initial,
        "nav": totals_nav,
        "total_pnl": totals_nav - totals_initial if totals_initial > 0 else None,
        "total_return_pct": ((totals_nav - totals_initial) / totals_initial if totals_initial > 0 else None),
    }
```

with:

```python
    payload["fund"] = build_fund_summary(payload["branches"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_report_json_helpers.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/build_report_json.py tests/unit/test_report_json_helpers.py
git commit -m "feat(reporting): fund-level notes disclose the 06-15..07-20 leverage window"
```

---

### Task 12: Full verification

**Files:** none new.

- [ ] **Step 1: Full unit suite**

Run: `pytest tests/unit/ -q`
Expected: all pass (baseline before this plan: 1,193 passed; expect ~1,220+ now). Zero failures, zero errors.

- [ ] **Step 2: Lint + format**

Run: `ruff check app/ tests/ scripts/ && ruff format --check app/ tests/ scripts/`
Expected: `All checks passed!` and no reformat needed. Fix anything flagged (then re-run the suite).

- [ ] **Step 3: Non-e2e collection sanity**

Run: `pytest tests/ --ignore=tests/integration -q`
Expected: all pass (catches import breakage outside tests/unit).

- [ ] **Step 4: Commit any lint fixups**

```bash
git status --short
# if anything changed:
git add -A && git commit -m "chore: lint/format fixups for Theme A"
```

---

## Execution notes

- Tasks 1→5 are the A1 chain (model → checks → persistence → digest → wiring) and must run in order. Task 6 must precede Task 7 (both rewrite `generate_orders`) and Task 10 (which replaces the Gap-2 block Task 6 edited). Task 8 must precede Task 9 (shared test file). Task 11 is independent.
- Deployment: nothing ships until the branch is merged/pushed. First Monday run after deployment sweeps the existing dust positions (KLAC, CAT, CSCO, MU, DIS, T, TMO) via Task 6's full exits.
- Out of scope (do not add): sizing/selection changes, sector caps, notifications, migrations, broker fill-price changes, standalone dust-sweep scripts.

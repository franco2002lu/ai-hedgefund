"""Unit tests for WeeklyRunner idempotency and summary rendering."""

from __future__ import annotations

from datetime import UTC, date
from datetime import datetime as dt
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.db.models import PipelineRunModel
from app.modules.equities.attribution import AttributionReport
from app.modules.equities.models import RunResult
from app.modules.equities.weekly_runner import (
    ManualInterventionRequired,
    RunInFlightError,
    WeeklyRunner,
    WeeklyRunSummary,
    render_digest,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeBegin:
    """Context manager for `session.begin()` — commits on clean exit."""

    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeBegin:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self._session._recorder.append(f"{self._session._label}-commit")
        else:
            self._session._recorder.append(f"{self._session._label}-rollback")


class _FakeSession:
    """Minimal async session stub usable as `async with factory() as s, s.begin():`."""

    def __init__(self, label: str, recorder: list[str], *, execute_result: Any = None) -> None:
        self._label = label
        self._recorder = recorder
        self._execute_result = execute_result

    def begin(self) -> _FakeBegin:
        return _FakeBegin(self)

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        pass  # begin() handles commit/rollback recording


class _FakeFactory:
    """Callable session factory.  Each call returns the next pre-configured session."""

    def __init__(self, sessions: list[_FakeSession]) -> None:
        self._sessions = list(sessions)
        self._index = 0
        self.created: list[_FakeSession] = []

    def __call__(self) -> _FakeSession:
        if self._index >= len(self._sessions):
            raise RuntimeError(
                f"_FakeFactory ran out of sessions (requested index {self._index}, only {len(self._sessions)} provided)"
            )
        s = self._sessions[self._index]
        self._index += 1
        self.created.append(s)
        return s


def _make_sessions(recorder: list[str], labels: list[str]) -> list[_FakeSession]:
    return [_FakeSession(label, recorder) for label in labels]


# ---------------------------------------------------------------------------
# Fake PipelineRunsRepository
# ---------------------------------------------------------------------------


class _FakeRepo:
    """Stub that records calls and returns canned find_latest results."""

    def __init__(self, *, find_latest_return: PipelineRunModel | None = None) -> None:
        self._find_latest_return = find_latest_return
        self.inserted: list[dict] = []
        self.completed: list[tuple] = []
        self.failed: list[tuple] = []

    async def find_latest(self, branch_id: str, run_date: date) -> PipelineRunModel | None:
        return self._find_latest_return

    async def insert(self, *, run_id: str, branch_id: str, run_date: date, attempt: int, status: str) -> None:
        self.inserted.append({"run_id": run_id, "branch_id": branch_id, "attempt": attempt, "status": status})

    async def mark_completed(self, run_id: str, summary: dict) -> None:
        self.completed.append((run_id, summary))

    async def mark_failed(self, run_id: str, error_msg: str) -> None:
        self.failed.append((run_id, error_msg))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run_result(branch: str, order_flow: dict | None = None) -> RunResult:
    return RunResult(
        branch_name=branch,
        universe_count=50,
        screened_count=30,
        signals=[],
        composite_scores=[],
        orders=[],
        trades_executed=10,
        order_flow=order_flow,
    )


def _row(status: str, run_id: str, attempt: int = 1):
    return PipelineRunModel(
        run_id=run_id,
        branch_id=uuid4(),
        run_date=date(2026, 4, 27),
        attempt=attempt,
        status=status,
        started_at=dt.now(UTC),
    )


def _make_factory_and_repo(
    recorder: list[str],
    labels: list[str],
    *,
    find_latest_return: PipelineRunModel | None = None,
) -> tuple[_FakeFactory, _FakeRepo]:
    """Build a factory and a FakeRepo.

    The caller is responsible for patching weekly_runner.PipelineRunsRepository
    to return the FakeRepo (this helper does no patching itself).
    """
    sessions = _make_sessions(recorder, labels)
    factory = _FakeFactory(sessions)
    repo = _FakeRepo(find_latest_return=find_latest_return)
    return factory, repo


# ---------------------------------------------------------------------------
# Basic dataclass / exception tests (unchanged)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Existing behavioural tests — rewritten for new constructor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_creates_row_when_no_prior_run():
    recorder: list[str] = []
    factory, repo = _make_factory_and_repo(
        recorder,
        ["bk1", "trading", "bk2"],
        find_latest_return=None,
    )
    branch_id = str(uuid4())

    with patch("app.modules.equities.weekly_runner.PipelineRunsRepository", return_value=repo):
        runner = WeeklyRunner(session_factory=factory)
        summary = await runner.execute(
            branch_name="growth",
            branch_id=branch_id,
            run_date=date(2026, 4, 27),
            force_retry=False,
            run_fn=AsyncMock(return_value=_make_run_result("growth")),
        )

    assert len(repo.inserted) == 1
    inserted = repo.inserted[0]
    assert inserted["run_id"] == "2026-04-27-growth"
    assert inserted["attempt"] == 1
    assert inserted["status"] == "running"

    assert len(repo.completed) == 1
    assert summary.status == "completed"
    assert summary.run_id == "2026-04-27-growth"


@pytest.mark.asyncio
async def test_execute_propagates_order_flow_into_summary_and_summary_json():
    FLOW = {
        "generated": 2,
        "persisted": 2,
        "filled": 1,
        "rejected": 1,
        "dropped": 0,
        "skipped_unpriced": 0,
        "skipped_below_entry": 0,
        "rejections": [],
        "skips": [],
    }
    recorder: list[str] = []
    factory, repo = _make_factory_and_repo(
        recorder,
        ["bk1", "trading", "bk2"],
        find_latest_return=None,
    )
    branch_id = str(uuid4())

    with patch("app.modules.equities.weekly_runner.PipelineRunsRepository", return_value=repo):
        runner = WeeklyRunner(session_factory=factory)
        summary = await runner.execute(
            branch_name="growth",
            branch_id=branch_id,
            run_date=date(2026, 4, 27),
            force_retry=False,
            run_fn=AsyncMock(return_value=_make_run_result("growth", order_flow=FLOW)),
        )

    assert len(repo.inserted) == 1
    inserted = repo.inserted[0]
    assert inserted["run_id"] == "2026-04-27-growth"
    assert inserted["attempt"] == 1
    assert inserted["status"] == "running"

    assert len(repo.completed) == 1
    assert summary.status == "completed"
    assert summary.run_id == "2026-04-27-growth"
    assert summary.order_flow == FLOW
    assert repo.completed[0][1]["order_flow"] == FLOW


@pytest.mark.asyncio
async def test_execute_skips_when_completed_row_exists():
    existing = _row("completed", "2026-04-27-growth")
    recorder: list[str] = []
    factory, repo = _make_factory_and_repo(
        recorder,
        ["bk1"],  # only the bookkeeping session is needed; no trading session
        find_latest_return=existing,
    )
    run_fn = AsyncMock()

    with patch("app.modules.equities.weekly_runner.PipelineRunsRepository", return_value=repo):
        runner = WeeklyRunner(session_factory=factory)
        summary = await runner.execute(
            branch_name="growth",
            branch_id=str(existing.branch_id),
            run_date=date(2026, 4, 27),
            force_retry=False,
            run_fn=run_fn,
        )

    assert summary.status == "skipped"
    run_fn.assert_not_called()
    assert len(repo.inserted) == 0


@pytest.mark.asyncio
async def test_execute_aborts_when_running_row_exists():
    existing = _row("running", "2026-04-27-growth")
    recorder: list[str] = []
    factory, repo = _make_factory_and_repo(recorder, ["bk1"], find_latest_return=existing)

    with patch("app.modules.equities.weekly_runner.PipelineRunsRepository", return_value=repo):
        runner = WeeklyRunner(session_factory=factory)
        with pytest.raises(RunInFlightError):
            await runner.execute(
                branch_name="growth",
                branch_id=str(existing.branch_id),
                run_date=date(2026, 4, 27),
                force_retry=False,
                run_fn=AsyncMock(),
            )


@pytest.mark.asyncio
async def test_execute_aborts_on_failed_without_retry():
    existing = _row("failed", "2026-04-27-growth")
    recorder: list[str] = []
    factory, repo = _make_factory_and_repo(recorder, ["bk1"], find_latest_return=existing)

    with patch("app.modules.equities.weekly_runner.PipelineRunsRepository", return_value=repo):
        runner = WeeklyRunner(session_factory=factory)
        with pytest.raises(ManualInterventionRequired):
            await runner.execute(
                branch_name="growth",
                branch_id=str(existing.branch_id),
                run_date=date(2026, 4, 27),
                force_retry=False,
                run_fn=AsyncMock(),
            )


@pytest.mark.asyncio
async def test_execute_creates_attempt2_row_on_force_retry():
    existing = _row("failed", "2026-04-27-growth", attempt=1)
    recorder: list[str] = []
    factory, repo = _make_factory_and_repo(
        recorder,
        ["bk1", "trading", "bk2"],
        find_latest_return=existing,
    )
    run_fn = AsyncMock(return_value=_make_run_result("growth"))

    with patch("app.modules.equities.weekly_runner.PipelineRunsRepository", return_value=repo):
        runner = WeeklyRunner(session_factory=factory)
        summary = await runner.execute(
            branch_name="growth",
            branch_id=str(existing.branch_id),
            run_date=date(2026, 4, 27),
            force_retry=True,
            run_fn=run_fn,
        )

    assert repo.inserted[0]["run_id"] == "2026-04-27-growth-attempt2"
    assert repo.inserted[0]["attempt"] == 2
    assert summary.status == "completed"


@pytest.mark.asyncio
async def test_execute_marks_failed_on_pipeline_exception():
    recorder: list[str] = []
    factory, repo = _make_factory_and_repo(recorder, ["bk1", "trading", "bk-fail"], find_latest_return=None)
    run_fn = AsyncMock(side_effect=RuntimeError("boom"))

    with patch("app.modules.equities.weekly_runner.PipelineRunsRepository", return_value=repo):
        runner = WeeklyRunner(session_factory=factory)
        with pytest.raises(RuntimeError, match="boom"):
            await runner.execute(
                branch_name="growth",
                branch_id=str(uuid4()),
                run_date=date(2026, 4, 27),
                force_retry=False,
                run_fn=run_fn,
            )

    assert len(repo.failed) == 1
    run_id_failed, error_msg = repo.failed[0]
    assert "boom" in error_msg


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


# ---------------------------------------------------------------------------
# New transaction-isolation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_running_row_committed_before_pipeline_runs():
    """The bookkeeping session that inserted "running" commits before run_fn runs."""
    recorder: list[str] = []
    sessions = _make_sessions(recorder, ["bk1", "trading", "bk2"])
    factory = _FakeFactory(sessions)
    repo = _FakeRepo(find_latest_return=None)

    async def _run_fn(session, run_id):
        recorder.append("run_fn")
        return _make_run_result("growth")

    with patch("app.modules.equities.weekly_runner.PipelineRunsRepository", return_value=repo):
        runner = WeeklyRunner(session_factory=factory)
        await runner.execute(
            branch_name="growth",
            branch_id=str(uuid4()),
            run_date=date(2026, 4, 27),
            force_retry=False,
            run_fn=_run_fn,
        )

    # The "running" row was inserted inside bk1, and bk1 committed before run_fn
    assert len(repo.inserted) == 1
    assert repo.inserted[0]["status"] == "running"
    assert recorder.index("bk1-commit") < recorder.index("run_fn")


@pytest.mark.asyncio
async def test_completed_marked_after_trading_commit():
    """Order: bk1-commit < run_fn < trading-commit < bk2-commit."""
    recorder: list[str] = []
    sessions = _make_sessions(recorder, ["bk1", "trading", "bk2"])
    factory = _FakeFactory(sessions)
    repo = _FakeRepo(find_latest_return=None)

    async def _run_fn(session, run_id):
        recorder.append("run_fn")
        return _make_run_result("growth")

    # Patch mark_completed to record its timing
    original_mc = repo.mark_completed

    async def _patched_mc(run_id, summary):
        recorder.append("mark_completed-called")
        return await original_mc(run_id, summary)

    repo.mark_completed = _patched_mc

    with patch("app.modules.equities.weekly_runner.PipelineRunsRepository", return_value=repo):
        runner = WeeklyRunner(session_factory=factory)
        await runner.execute(
            branch_name="growth",
            branch_id=str(uuid4()),
            run_date=date(2026, 4, 27),
            force_retry=False,
            run_fn=_run_fn,
        )

    # Expected sequence in recorder:
    # ["bk1-commit", "run_fn", "trading-commit", "mark_completed-called", "bk2-commit"]
    assert recorder.index("bk1-commit") < recorder.index("run_fn")
    assert recorder.index("run_fn") < recorder.index("trading-commit")
    assert recorder.index("trading-commit") < recorder.index("mark_completed-called")
    assert recorder.index("mark_completed-called") < recorder.index("bk2-commit")


@pytest.mark.asyncio
async def test_failed_run_marks_failed_outside_trading_txn():
    """run_fn raises; mark_failed uses a DIFFERENT session than the trading session."""
    recorder: list[str] = []
    sessions = _make_sessions(recorder, ["bk1", "trading", "bk-fail"])
    factory = _FakeFactory(sessions)
    repo = _FakeRepo(find_latest_return=None)

    trading_session_used: list[_FakeSession] = []
    fail_session_used: list[_FakeSession] = []

    async def _run_fn(session, run_id):
        trading_session_used.append(session)
        raise ValueError("pipeline boom")

    original_mf = repo.mark_failed

    async def _patched_mf(run_id, error_msg):
        # Which session is currently open? We intercept from the factory
        fail_session_used.append(factory.created[-1])
        return await original_mf(run_id, error_msg)

    repo.mark_failed = _patched_mf

    with patch("app.modules.equities.weekly_runner.PipelineRunsRepository", return_value=repo):
        runner = WeeklyRunner(session_factory=factory)
        with pytest.raises(ValueError, match="pipeline boom"):
            await runner.execute(
                branch_name="growth",
                branch_id=str(uuid4()),
                run_date=date(2026, 4, 27),
                force_retry=False,
                run_fn=_run_fn,
            )

    # Trading session rolled back
    assert "trading-rollback" in recorder
    # The mark_failed session committed
    assert "bk-fail-commit" in recorder
    # The sessions used for trading vs mark_failed are DIFFERENT objects
    assert len(trading_session_used) == 1
    assert len(fail_session_used) == 1
    assert trading_session_used[0] is not fail_session_used[0]


@pytest.mark.asyncio
async def test_mark_failed_bookkeeping_error_does_not_mask_pipeline_error():
    """Even if mark_failed itself raises, the original pipeline error propagates."""
    recorder: list[str] = []
    sessions = _make_sessions(recorder, ["bk1", "trading", "bk-fail"])
    factory = _FakeFactory(sessions)
    repo = _FakeRepo(find_latest_return=None)

    async def _run_fn(session, run_id):
        raise ValueError("pipeline boom")

    async def _failing_mark_failed(run_id, error_msg):
        raise OSError("DB connection lost during mark_failed")

    repo.mark_failed = _failing_mark_failed

    with patch("app.modules.equities.weekly_runner.PipelineRunsRepository", return_value=repo):
        runner = WeeklyRunner(session_factory=factory)
        exc = None
        try:
            await runner.execute(
                branch_name="growth",
                branch_id=str(uuid4()),
                run_date=date(2026, 4, 27),
                force_retry=False,
                run_fn=_run_fn,
            )
        except Exception as e:
            exc = e

    assert exc is not None
    # The ORIGINAL pipeline error must propagate, not the bookkeeping error
    assert isinstance(exc, ValueError), f"Expected ValueError, got {type(exc)}: {exc}"
    assert "pipeline boom" in str(exc)


@pytest.mark.asyncio
async def test_skip_path_never_opens_trading_session():
    """When prior run is completed, only one (bookkeeping) session is created."""
    existing = _row("completed", "2026-04-27-growth")
    recorder: list[str] = []
    sessions = _make_sessions(recorder, ["bk1"])  # only one session available
    factory = _FakeFactory(sessions)
    repo = _FakeRepo(find_latest_return=existing)
    run_fn = AsyncMock()

    with patch("app.modules.equities.weekly_runner.PipelineRunsRepository", return_value=repo):
        runner = WeeklyRunner(session_factory=factory)
        summary = await runner.execute(
            branch_name="growth",
            branch_id=str(existing.branch_id),
            run_date=date(2026, 4, 27),
            force_retry=False,
            run_fn=run_fn,
        )

    assert summary.status == "skipped"
    run_fn.assert_not_called()
    # Only one session was ever created (bookkeeping)
    assert len(factory.created) == 1


# ---------------------------------------------------------------------------
# configure_service and render_digest tests (unchanged)
# ---------------------------------------------------------------------------


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
        universe_count=50,
        screened_count=30,
        orders_placed=10,
        trades_executed=8,
        duration_seconds=300.0,
        error=None,
    )
    s2 = WeeklyRunSummary(
        run_id="2026-04-27-value",
        branch_name="value",
        status="failed",
        universe_count=0,
        screened_count=0,
        orders_placed=0,
        trades_executed=0,
        duration_seconds=12.0,
        error="RuntimeError: yfinance rate limited",
    )
    out = render_digest([s1, s2], run_date=date(2026, 4, 27))

    assert "## growth" in out
    assert "## value" in out
    assert "❌" in out or "failed" in out
    assert "yfinance rate limited" in out


class TestDigestAttribution:
    def test_digest_includes_attribution_section(self):
        report = AttributionReport(
            branch_name="growth",
            decision_date=date(2026, 6, 1),
            as_of_date=date(2026, 6, 8),
            basket_return_conviction=0.012,
            basket_return_equal=0.009,
            benchmark_return=0.004,
            benchmark_symbol="VOOG",
            spy_return=-0.003,
            analyst_ics={"fundamentals": 0.11, "news": -0.18, "technical": None, "composite": 0.05},
            n_holdings=20,
            n_holdings_priced=20,
        )
        summary = WeeklyRunSummary(
            run_id="2026-06-08-growth",
            branch_name="growth",
            status="completed",
            universe_count=50,
            screened_count=24,
            orders_placed=20,
            trades_executed=20,
            duration_seconds=75.0,
            attribution=report,
        )
        digest = render_digest([summary], run_date=date(2026, 6, 8))
        assert "Last week (2026-06-01)" in digest
        assert "+1.20%" in digest  # conviction basket
        assert "eq-wt +0.90%" in digest
        assert "VOOG +0.40%" in digest
        assert "fund +0.11" in digest
        assert "tech n/a" in digest
        assert "comp +0.05" in digest

    def test_digest_without_attribution_unchanged(self):
        summary = WeeklyRunSummary(
            run_id="x",
            branch_name="growth",
            status="completed",
            universe_count=1,
            screened_count=1,
            orders_placed=1,
            trades_executed=1,
            duration_seconds=1.0,
        )
        digest = render_digest([summary], run_date=date(2026, 6, 8))
        assert "Last week" not in digest


class TestDigestAnalystWeights:
    @staticmethod
    def _weights_report(mode="adaptive", reason="ok", alerts=None):
        from app.modules.equities.adaptive_weights import AnalystWeightsReport

        return AnalystWeightsReport(
            weights={"fundamentals": 0.65, "news": 0.19, "technical": 0.16},
            mode=mode,
            reason=reason,
            ewics={"fundamentals": 0.09, "news": 0.0, "technical": -0.08},
            valid_weeks={"fundamentals": 9, "news": 9, "technical": 9},
            alerts=alerts or [],
        )

    @staticmethod
    def _summary(**overrides):
        base = dict(
            run_id="r",
            branch_name="growth",
            status="completed",
            universe_count=1,
            screened_count=1,
            orders_placed=1,
            trades_executed=1,
            duration_seconds=1.0,
        )
        base.update(overrides)
        return WeeklyRunSummary(**base)

    def test_digest_renders_adaptive_weights_line(self):
        s = self._summary(analyst_weights_report=self._weights_report())
        digest = render_digest([s], run_date=date(2026, 7, 20))
        assert (
            "- Weights: fund 0.65 / news 0.19 / tech 0.16 (adaptive, 9 wks; EWIC fund +0.09 / news +0.00 / tech -0.08)"
        ) in digest

    def test_digest_renders_static_reason(self):
        from app.modules.equities.adaptive_weights import AnalystWeightsReport

        s = self._summary(
            analyst_weights_report=AnalystWeightsReport(
                weights={"fundamentals": 0.60, "news": 0.20, "technical": 0.20},
                mode="static",
                reason="insufficient_history",
                valid_weeks={"fundamentals": 4, "news": 4, "technical": 4},
            )
        )
        digest = render_digest([s], run_date=date(2026, 7, 20))
        assert "- Weights: fund 0.60 / news 0.20 / tech 0.20 (static — insufficient_history, min 4 valid wks)" in digest

    def test_digest_renders_ic_alert(self):
        s = self._summary(
            branch_name="value",
            analyst_weights_report=self._weights_report(alerts=[{"analyst": "technical", "streak": 4, "ewic": -0.17}]),
        )
        digest = render_digest([s], run_date=date(2026, 7, 20))
        assert "- ⚠️ technical rolling IC ≤ 0 for 4 consecutive weeks (EWIC -0.17)" in digest

    def test_digest_without_weights_report_unchanged(self):
        digest = render_digest([self._summary()], run_date=date(2026, 7, 20))
        assert "Weights:" not in digest

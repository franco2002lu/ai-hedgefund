"""Theme A1 alert-wiring seam in the weekly CLI (_evaluate_and_persist_alerts).

Covers the completed-only gate, flush-before-savepoint ordering, and the
degrade-to-digest-notice fallback when persistence fails. No DB — fakes only.
"""

from types import SimpleNamespace

from app.modules.equities.config import PortfolioConfig
from scripts.run_weekly_pipeline import _evaluate_and_persist_alerts


class FakeSession:
    """Records flush/begin_nested call order.

    The savepoint context manager does nothing on clean exit and propagates
    exceptions raised inside (matching AsyncSession.begin_nested semantics
    as far as this seam observes them).
    """

    def __init__(self):
        self.calls: list[str] = []

    async def flush(self):
        self.calls.append("flush")

    def begin_nested(self):
        self.calls.append("begin_nested")
        return _FakeSavepoint()


class _FakeSavepoint:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False  # propagate exceptions raised inside the savepoint


class FakeRepo:
    def __init__(self, raise_on_create: bool = False):
        self.created = []
        self.raise_on_create = raise_on_create

    async def create(self, alert):
        if self.raise_on_create:
            raise RuntimeError("simulated risk_alerts insert failure")
        self.created.append(alert)
        return alert


class FakeEventLog:
    def __init__(self):
        self.events = []

    async def append(self, event):
        self.events.append(event)


def _mtm(cash=10_000.0, nav=1_000_000.0, positions_detail=None):
    return SimpleNamespace(cash=cash, nav=nav, positions_detail=positions_detail or [])


async def _call(*, status="completed", mtm=None, repo=None, order_flow=None):
    session, log = FakeSession(), FakeEventLog()
    repo = repo if repo is not None else FakeRepo()
    result = await _evaluate_and_persist_alerts(
        session=session,
        alert_repo=repo,
        event_log=log,
        mtm=mtm if mtm is not None else _mtm(),
        portfolio_config=PortfolioConfig(),
        branch_id="b-1",
        branch_name="growth",
        status=status,
        order_flow=order_flow,
    )
    return result, session, repo, log


async def test_skipped_run_is_gated_out_entirely():
    # Breach-y book on purpose: the status gate must win.
    result, session, repo, log = await _call(status="skipped", mtm=_mtm(cash=-5.0))
    assert result == []
    assert repo.created == [] and log.events == []
    assert session.calls == []  # flush never called


async def test_completed_clean_book_returns_empty_and_persists_nothing():
    result, session, repo, log = await _call(
        mtm=_mtm(cash=10_000.0, positions_detail=[{"symbol": "AAPL", "weight": 0.05}])
    )
    assert result == []
    assert repo.created == [] and log.events == []
    assert session.calls == []  # no alerts → no flush/savepoint churn


async def test_negative_cash_persists_and_flushes_before_savepoint():
    result, session, repo, log = await _call(mtm=_mtm(cash=-55_473.33))
    assert len(result) == 1
    assert result[0]["level"] == "critical"
    assert "cash is negative (-$55,473.33)" in result[0]["message"]
    assert [a.metric for a in repo.created] == ["cash"]
    assert [e.event_type for e in log.events] == ["risk.alert"]
    assert session.calls == ["flush", "begin_nested"]


async def test_persistence_failure_degrades_to_critical_notice_without_raising():
    result, session, repo, log = await _call(mtm=_mtm(cash=-55_473.33), repo=FakeRepo(raise_on_create=True))
    assert repo.created == []
    assert len(result) == 2
    assert result[0]["level"] == "critical" and "cash is negative" in result[0]["message"]
    assert result[-1] == {"level": "critical", "message": "Risk alert persistence failed — see run logs"}


async def test_warning_only_breach_degrade_notice_stays_warning():
    result, session, repo, log = await _call(
        mtm=_mtm(cash=60_000.0),  # 6% of NAV > 3% warn → warning-only breach
        repo=FakeRepo(raise_on_create=True),
    )
    assert [d["level"] for d in result] == ["warning", "warning"]
    assert result[-1]["message"] == "Risk alert persistence failed — see run logs"


async def test_order_flow_lost_orders_persists_critical_and_reaches_digest():
    flow = {
        "generated": 2,
        "persisted": 1,
        "dropped": 1,
        "rejected": 0,
        "rejections": [{"symbol": "GONE", "side": "sell", "kind": "dropped", "reason": "Insufficient position"}],
        "skips": [],
    }
    result, session, repo, log = await _call(order_flow=flow)
    lost = [a for a in repo.created if a.metric == "orders_lost"]
    assert len(lost) == 1
    assert "GONE" in lost[0].message
    assert "Insufficient position" in lost[0].message
    assert any(d["level"] == "critical" for d in result)


async def test_evaluation_failure_degrades_to_digest_warning(monkeypatch):
    def _raise(**kwargs):
        raise RuntimeError("malformed order_flow")

    monkeypatch.setattr("scripts.run_weekly_pipeline.evaluate_post_run_invariants", _raise)

    # Breach-y book on purpose: even a book that would normally CRITICAL must
    # not leak past a crashing evaluator — the mark/snapshot transaction
    # matters more than these alerts. The crash itself must still surface on
    # the digest (last silent link, closed in final review) without touching
    # the DB.
    result, session, repo, log = await _call(mtm=_mtm(cash=-55_473.33))
    assert result == [{"level": "warning", "message": "Risk checks failed to evaluate — see run logs"}]
    assert repo.created == [] and log.events == []
    assert session.calls == []  # never reaches flush/savepoint

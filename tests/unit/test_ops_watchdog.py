"""Unit tests for the scheduled ops watchdog."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from scripts.ops_watchdog import (
    business_days_ago,
    check_monday_run,
    check_snapshot_freshness,
    check_stuck_runs,
    check_unnotified_criticals,
    ensure_issue,
)


class FakeProc:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""


class FakeRunner:
    """Records gh invocations; scripted stdout for `gh issue list`."""

    def __init__(self, open_issues: list[dict] | None = None):
        self.calls: list[list[str]] = []
        self.kwargs_log: list[dict] = []
        self.open_issues = open_issues or []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        self.kwargs_log.append(dict(kwargs))
        if cmd[:3] == ["gh", "issue", "list"]:
            return FakeProc(stdout=json.dumps(self.open_issues))
        return FakeProc()


NOW = datetime(2026, 7, 30, 18, 0, tzinfo=UTC)


class TestBusinessDaysAgo:
    def test_wednesday_minus_two_is_monday(self):
        assert business_days_ago(date(2026, 7, 29), 2) == date(2026, 7, 27)

    def test_monday_minus_two_is_thursday(self):
        assert business_days_ago(date(2026, 7, 27), 2) == date(2026, 7, 23)

    def test_tuesday_minus_two_is_friday(self):
        assert business_days_ago(date(2026, 7, 28), 2) == date(2026, 7, 24)

    def test_zero_days_returns_today_unchanged_even_on_saturday(self):
        assert business_days_ago(date(2026, 8, 1), 0) == date(2026, 8, 1)

    def test_saturday_minus_two_is_thursday(self):
        assert business_days_ago(date(2026, 8, 1), 2) == date(2026, 7, 30)


class TestEnsureIssue:
    def test_dry_run_without_token(self, monkeypatch):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        runner = FakeRunner()
        assert ensure_issue("X failed", "body", run=runner, now=NOW) == "dry-run"
        assert runner.calls == []

    def test_creates_issue_when_none_open(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "t")
        runner = FakeRunner(open_issues=[])
        assert ensure_issue("X failed", "http://run", run=runner, now=NOW) == "created"
        create = [c for c in runner.calls if c[:3] == ["gh", "issue", "create"]]
        assert len(create) == 1
        assert "[ops-alert] X failed — 2026-07-30" in create[0]
        # label is force-created first (idempotent)
        assert runner.calls[0][:3] == ["gh", "label", "create"]
        label_idx = next(i for i, c in enumerate(runner.calls) if c[:3] == ["gh", "label", "create"])
        create_idx = next(i for i, c in enumerate(runner.calls) if c[:3] == ["gh", "issue", "create"])
        assert runner.kwargs_log[label_idx]["check"] is False
        assert runner.kwargs_log[create_idx]["check"] is True

    def test_comments_on_existing_same_title(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "t")
        runner = FakeRunner(open_issues=[{"number": 7, "title": "[ops-alert] X failed — 2026-07-30"}])
        assert ensure_issue("X failed", "http://run", run=runner, now=NOW) == "commented"
        comment = [c for c in runner.calls if c[:3] == ["gh", "issue", "comment"]]
        assert len(comment) == 1 and comment[0][3] == "7"
        assert not any(c[:3] == ["gh", "issue", "create"] for c in runner.calls)

    def test_different_date_creates_new_issue(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "t")
        runner = FakeRunner(open_issues=[{"number": 7, "title": "[ops-alert] X failed — 2026-07-29"}])
        assert ensure_issue("X failed", "http://run", run=runner, now=NOW) == "created"


class FakeRow:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """Returns scripted results keyed by a substring of the SQL text."""

    def __init__(self, results: dict[str, list]):
        self.results = results
        self.queries: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.queries.append(sql)
        for key, rows in self.results.items():
            if key in sql:
                return FakeResult(rows)
        return FakeResult([])


BID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture(autouse=True)
def _one_branch(monkeypatch):
    monkeypatch.setattr("scripts.ops_watchdog.settings.equities_enabled_branches", ["growth"])

    async def fake_resolve(session, name):
        return BID

    monkeypatch.setattr("scripts.ops_watchdog.resolve_branch_id", fake_resolve)


class TestStuckRuns:
    async def test_alerts_on_stuck_row(self):
        started = datetime(2026, 7, 27, 15, 0, tzinfo=UTC)
        session = FakeSession({"status = 'running'": [FakeRow(run_id="2026-07-27-growth", started_at=started)]})
        alerts = await check_stuck_runs(session)
        assert len(alerts) == 1
        assert "stuck" in alerts[0][0]
        assert "2026-07-27-growth" in alerts[0][1]

    async def test_quiet_when_none(self):
        assert await check_stuck_runs(FakeSession({})) == []


class TestMondayRun:
    async def test_not_monday_is_quiet(self, monkeypatch):
        monkeypatch.setattr("scripts.ops_watchdog.today_ny", lambda: date(2026, 7, 30))  # Thursday
        assert await check_monday_run(FakeSession({}), datetime(2026, 7, 30, 18, 0, tzinfo=UTC)) == []

    async def test_monday_before_deadline_is_quiet(self, monkeypatch):
        monkeypatch.setattr("scripts.ops_watchdog.today_ny", lambda: date(2026, 8, 3))  # Monday
        assert await check_monday_run(FakeSession({}), datetime(2026, 8, 3, 16, 0, tzinfo=UTC)) == []

    async def test_monday_after_deadline_missing_run_alerts(self, monkeypatch):
        monkeypatch.setattr("scripts.ops_watchdog.today_ny", lambda: date(2026, 8, 3))
        session = FakeSession({"status = 'completed'": []})
        alerts = await check_monday_run(session, datetime(2026, 8, 3, 17, 0, tzinfo=UTC))
        assert len(alerts) == 1
        assert "growth" in alerts[0][0]

    async def test_monday_with_completed_run_is_quiet(self, monkeypatch):
        monkeypatch.setattr("scripts.ops_watchdog.today_ny", lambda: date(2026, 8, 3))
        session = FakeSession({"status = 'completed'": [FakeRow(one=1)]})
        assert await check_monday_run(session, datetime(2026, 8, 3, 17, 0, tzinfo=UTC)) == []


class TestSnapshotFreshness:
    async def test_fresh_snapshot_is_quiet(self, monkeypatch):
        monkeypatch.setattr("scripts.ops_watchdog.today_ny", lambda: date(2026, 7, 30))
        latest = datetime(2026, 7, 29, 22, 28, tzinfo=UTC)
        session = FakeSession({"max(snapshot_at)": [FakeRow(latest=latest)]})
        assert await check_snapshot_freshness(session) == []

    async def test_stale_snapshot_alerts(self, monkeypatch):
        monkeypatch.setattr("scripts.ops_watchdog.today_ny", lambda: date(2026, 7, 30))
        latest = datetime(2026, 7, 24, 22, 28, tzinfo=UTC)  # last Friday — 4 business days
        session = FakeSession({"max(snapshot_at)": [FakeRow(latest=latest)]})
        alerts = await check_snapshot_freshness(session)
        assert len(alerts) == 1
        assert "growth" in alerts[0][0]

    async def test_never_snapshotted_alerts(self, monkeypatch):
        monkeypatch.setattr("scripts.ops_watchdog.today_ny", lambda: date(2026, 7, 30))
        session = FakeSession({"max(snapshot_at)": [FakeRow(latest=None)]})
        alerts = await check_snapshot_freshness(session)
        assert len(alerts) == 1
        assert "never" in alerts[0][1]


class TestUnnotifiedCriticals:
    async def test_recent_unresolved_critical_alerts(self):
        created = datetime(2026, 8, 3, 16, 0, tzinfo=UTC)
        session = FakeSession(
            {"FROM risk_alerts": [FakeRow(metric="cash", message="growth: cash is negative", created_at=created)]}
        )
        alerts = await check_unnotified_criticals(session)
        assert len(alerts) == 1
        assert "cash" in alerts[0][0]

    async def test_quiet_when_none(self):
        assert await check_unnotified_criticals(FakeSession({})) == []

"""Unit tests for the scheduled ops watchdog."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from scripts.ops_watchdog import business_days_ago, ensure_issue


class FakeProc:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""


class FakeRunner:
    """Records gh invocations; scripted stdout for `gh issue list`."""

    def __init__(self, open_issues: list[dict] | None = None):
        self.calls: list[list[str]] = []
        self.open_issues = open_issues or []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
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

# Alerting + Order-Path Integrity (D1+S2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push-notify every production failure (workflow failure, stuck run, missing run, stale snapshots, unnotified CRITICAL) via GitHub issues, and make the order path fully reconciling (generated = persisted = filled + rejected, skips accounted) in the digest, risk checks, and DB.

**Architecture:** First-party `gh` CLI alerting (a shared shell helper for workflow `if: failure()` steps + a scheduled watchdog script querying Neon). Order-flow accounting threads through the existing LangGraph pipeline: `generate_orders_with_skips` emits skip metadata, the execute node keeps every `submit_order` result, a pure `build_order_flow` helper tallies them, and the summary flows into `pipeline_runs.summary_json`, the digest, and `risk_checks`. One hot-path change: `submit_order` persists validation failures as REJECTED order rows.

**Tech Stack:** Python 3.12, pytest, SQLAlchemy (async, `text()` for the watchdog), GitHub Actions, `gh` CLI. Spec: `docs/superpowers/specs/2026-07-30-alerting-order-integrity-design.md`.

**Working directory:** `/Users/franco_lu/dev/ai-hedgefund-final` (healthy clone; the Desktop checkout is iCloud-degraded — do NOT work there). Branch: `feat/alerting-order-integrity`. Always use `.venv/bin/pytest` and `.venv/bin/ruff` from this clone. Baseline: 1,248 tests green, ruff clean.

---

### Task 1: `ops_alert.sh` — shared failure-alert helper

**Files:**
- Create: `.github/scripts/ops_alert.sh`
- Test: `tests/unit/test_ops_alert_script.py`

- [ ] **Step 1: Write the failing test**

```python
"""Syntax-check the ops_alert shell helper (no gh execution — that needs a token)."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "ops_alert.sh"


def test_ops_alert_script_exists():
    assert SCRIPT.is_file(), f"missing {SCRIPT}"


def test_ops_alert_script_bash_syntax():
    proc = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_ops_alert_script_requires_two_args():
    # With no args and set -u, the script must exit non-zero before calling gh.
    proc = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True)
    assert proc.returncode != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/franco_lu/dev/ai-hedgefund-final && .venv/bin/pytest tests/unit/test_ops_alert_script.py -v`
Expected: FAIL — `missing .../.github/scripts/ops_alert.sh`

- [ ] **Step 3: Write the script**

Create `.github/scripts/ops_alert.sh`:

```bash
#!/usr/bin/env bash
# Open (or comment on) a GitHub issue labeled ops-alert.
# Usage: ops_alert.sh <subject> <run-url>
# Dedup: one open issue per "[ops-alert] <subject> — <UTC date>" title;
# repeats become comments on it.
set -euo pipefail

SUBJECT="$1"
RUN_URL="$2"
TITLE="[ops-alert] ${SUBJECT} — $(date -u +%F)"

gh label create ops-alert --force \
  --description "Automated operational alert" --color D93F0B

EXISTING=$(gh issue list --state open --label ops-alert --json number,title \
  --jq ".[] | select(.title == \"${TITLE}\") | .number" | head -1)

if [ -n "${EXISTING}" ]; then
  gh issue comment "${EXISTING}" --body "Recurred: ${RUN_URL}"
else
  gh issue create --title "${TITLE}" --label ops-alert --body "Run: ${RUN_URL}"
fi
```

Then: `chmod +x .github/scripts/ops_alert.sh`

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_ops_alert_script.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/ops_alert.sh tests/unit/test_ops_alert_script.py
git commit -m "feat(ops): shared gh-issue alert helper for workflow failures"
```

---

### Task 2: Failure-alert steps in the two existing workflows

**Files:**
- Modify: `.github/workflows/weekly-rebalance.yml` (permissions block at lines 38-39; append step after line 96)
- Modify: `.github/workflows/daily-snapshot.yml` (no permissions block today — add one after line 18; append step after line 40)

No unit-test harness exists for workflow YAML; correctness is by exact-diff review here plus `python -c "import yaml, sys; yaml.safe_load(open(sys.argv[1]))"` as a parse check.

- [ ] **Step 1: Edit `weekly-rebalance.yml`**

Change the permissions block (currently lines 38-39):

```yaml
    permissions:
      contents: write
      issues: write
```

Append as the LAST step of the job (after the "Commit weekly report" step):

```yaml
      - name: Alert on failure
        if: failure()
        env:
          GH_TOKEN: ${{ github.token }}
        run: bash .github/scripts/ops_alert.sh "Weekly rebalance failed" "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
```

- [ ] **Step 2: Edit `daily-snapshot.yml`**

Insert a permissions block between `timeout-minutes: 15` and `env:` (IMPORTANT: adding any permissions block zeroes unlisted scopes — `contents: read` is required or `actions/checkout` breaks):

```yaml
    permissions:
      contents: read
      issues: write
```

Append as the LAST step of the job:

```yaml
      - name: Alert on failure
        if: failure()
        env:
          GH_TOKEN: ${{ github.token }}
        run: bash .github/scripts/ops_alert.sh "Daily snapshot failed" "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
```

- [ ] **Step 3: Verify both files still parse**

Run: `.venv/bin/python -c "import yaml; [yaml.safe_load(open(p)) for p in ['.github/workflows/weekly-rebalance.yml', '.github/workflows/daily-snapshot.yml']]; print('YAML OK')"`
Expected: `YAML OK`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/weekly-rebalance.yml .github/workflows/daily-snapshot.yml
git commit -m "feat(ops): open ops-alert issue when weekly/daily workflows fail"
```

---

### Task 3: Watchdog helpers — `business_days_ago` + `ensure_issue`

**Files:**
- Create: `scripts/ops_watchdog.py` (helpers only in this task; checks in Task 4)
- Test: `tests/unit/test_ops_watchdog.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_ops_watchdog.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_ops_watchdog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.ops_watchdog'`

- [ ] **Step 3: Create `scripts/ops_watchdog.py` with the helpers**

```python
"""Scheduled ops watchdog: stuck runs, missing Monday runs, stale snapshots,
unnotified CRITICAL risk alerts.

Alerts by opening/commenting GitHub issues labeled `ops-alert` via the `gh`
CLI (preinstalled on Actions runners; token via GH_TOKEN). Without a token it
logs would-be alerts and exits 0 — safe local dry-run.

Run by .github/workflows/ops-watchdog.yml (Mon 17:00 UTC + weekdays 23:30 UTC).

Exit codes:
  0 — checks completed (whether or not alerts were raised)
  1 — the watchdog itself failed (DB unreachable, bug) — the workflow's own
      failure-alert step then reports it
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import uuid
from datetime import UTC, date, datetime, time as dtime, timedelta

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.connection import async_session_factory  # noqa: E402
from app.modules.equities.weekly_runner import ny_date, today_ny  # noqa: E402
from scripts.common import resolve_branch_id  # noqa: E402

logger = logging.getLogger("ops_watchdog")

STUCK_RUN_HOURS = 2
SNAPSHOT_STALE_BUSINESS_DAYS = 2
MONDAY_RUN_DEADLINE_UTC = dtime(16, 30)
CRITICAL_LOOKBACK_HOURS = 25


def business_days_ago(today: date, n: int) -> date:
    """Walk back n weekdays from today (weekends don't count)."""
    d = today
    remaining = n
    while remaining > 0:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            remaining -= 1
    return d


def ensure_issue(
    subject: str,
    body: str,
    *,
    run=subprocess.run,
    now: datetime | None = None,
) -> str:
    """Open (or comment on) the open ops-alert issue for this subject+date.

    Returns 'created' | 'commented' | 'dry-run'. Mirrors .github/scripts/
    ops_alert.sh — keep the two in sync.
    """
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%d")
    title = f"[ops-alert] {subject} — {stamp}"
    if not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        logger.warning("DRY-RUN (no GH token) — would alert: %s | %s", title, body)
        return "dry-run"
    run(
        ["gh", "label", "create", "ops-alert", "--force",
         "--description", "Automated operational alert", "--color", "D93F0B"],
        check=False, capture_output=True, text=True,
    )
    listing = run(
        ["gh", "issue", "list", "--state", "open", "--label", "ops-alert",
         "--json", "number,title"],
        check=True, capture_output=True, text=True,
    )
    for issue in json.loads(listing.stdout or "[]"):
        if issue["title"] == title:
            run(
                ["gh", "issue", "comment", str(issue["number"]), "--body", f"Recurred: {body}"],
                check=True, capture_output=True, text=True,
            )
            return "commented"
    run(
        ["gh", "issue", "create", "--title", title, "--label", "ops-alert", "--body", body],
        check=True, capture_output=True, text=True,
    )
    return "created"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_ops_watchdog.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/ops_watchdog.py tests/unit/test_ops_watchdog.py
git commit -m "feat(ops): watchdog helpers — business-day math + gh issue dedup"
```

---

### Task 4: Watchdog checks + main

**Files:**
- Modify: `scripts/ops_watchdog.py` (append checks + main)
- Test: `tests/unit/test_ops_watchdog.py` (append)

- [ ] **Step 1: Write the failing tests (append to `tests/unit/test_ops_watchdog.py`)**

```python
import pytest

from scripts.ops_watchdog import (
    check_monday_run,
    check_snapshot_freshness,
    check_stuck_runs,
    check_unnotified_criticals,
)


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_ops_watchdog.py -v`
Expected: new tests FAIL — `ImportError: cannot import name 'check_monday_run'`

- [ ] **Step 3: Append checks + main to `scripts/ops_watchdog.py`**

```python
async def check_stuck_runs(session) -> list[tuple[str, str]]:
    cutoff = datetime.now(UTC) - timedelta(hours=STUCK_RUN_HOURS)
    rows = (
        await session.execute(
            text("SELECT run_id, started_at FROM pipeline_runs WHERE status = 'running' AND started_at < :cutoff"),
            {"cutoff": cutoff},
        )
    ).all()
    return [
        (
            "Pipeline run stuck in running",
            f"`{r.run_id}` started {r.started_at.isoformat()} (> {STUCK_RUN_HOURS}h ago). "
            "Flip status to 'failed' and re-dispatch with force_retry — see CLAUDE.md recovery steps.",
        )
        for r in rows
    ]


async def check_monday_run(session, now_utc: datetime) -> list[tuple[str, str]]:
    today = today_ny()
    if today.weekday() != 0 or now_utc.time() < MONDAY_RUN_DEADLINE_UTC:
        return []
    alerts: list[tuple[str, str]] = []
    for branch in settings.equities_enabled_branches:
        bid = uuid.UUID(await resolve_branch_id(session, branch))
        row = (
            await session.execute(
                text(
                    "SELECT 1 FROM pipeline_runs "
                    "WHERE branch_id = :b AND run_date = :d AND status = 'completed' LIMIT 1"
                ),
                {"b": bid, "d": today},
            )
        ).first()
        if row is None:
            alerts.append(
                (
                    f"Weekly run missing for {branch}",
                    f"No completed pipeline run for {today.isoformat()} as of "
                    f"{now_utc.strftime('%H:%M')} UTC — cron may not have fired, or the run failed/stuck.",
                )
            )
    return alerts


async def check_snapshot_freshness(session) -> list[tuple[str, str]]:
    cutoff_date = business_days_ago(today_ny(), SNAPSHOT_STALE_BUSINESS_DAYS)
    alerts: list[tuple[str, str]] = []
    for branch in settings.equities_enabled_branches:
        bid = uuid.UUID(await resolve_branch_id(session, branch))
        row = (
            await session.execute(
                text("SELECT max(snapshot_at) AS latest FROM portfolio_snapshots WHERE branch_id = :b"),
                {"b": bid},
            )
        ).first()
        latest = row.latest if row else None
        if latest is None or ny_date(latest) < cutoff_date:
            seen = ny_date(latest).isoformat() if latest else "never"
            alerts.append(
                (
                    f"No fresh snapshot for {branch}",
                    f"Latest snapshot NY-date: {seen}; expected ≥ {cutoff_date.isoformat()}. "
                    "Daily-snapshot workflow may be failing silently.",
                )
            )
    return alerts


async def check_unnotified_criticals(session) -> list[tuple[str, str]]:
    cutoff = datetime.now(UTC) - timedelta(hours=CRITICAL_LOOKBACK_HOURS)
    rows = (
        await session.execute(
            text(
                "SELECT metric, message, created_at FROM risk_alerts "
                "WHERE lower(level) = 'critical' AND resolved = false AND created_at > :cutoff "
                "ORDER BY created_at"
            ),
            {"cutoff": cutoff},
        )
    ).all()
    return [
        (
            f"CRITICAL risk alert: {r.metric}",
            f"{r.message} (raised {r.created_at.isoformat()}). "
            "Set resolved = true on the risk_alerts row once handled.",
        )
        for r in rows
    ]


async def _main_async() -> int:
    now_utc = datetime.now(UTC)
    alerts: list[tuple[str, str]] = []
    async with async_session_factory() as session:
        alerts += await check_stuck_runs(session)
        alerts += await check_monday_run(session, now_utc)
        alerts += await check_snapshot_freshness(session)
        alerts += await check_unnotified_criticals(session)
    for subject, body in alerts:
        logger.warning("ALERT: %s — %s", subject, body)
        ensure_issue(subject, body)
    logger.info("Watchdog done: %d alert(s) raised", len(alerts))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return asyncio.run(_main_async())
    except Exception:
        logger.exception("Watchdog failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_ops_watchdog.py -v`
Expected: 18 PASS (7 from Task 3 + 11 new)

- [ ] **Step 5: Local dry-run against Neon is OPTIONAL and read-only; skip unless asked.**

- [ ] **Step 6: Commit**

```bash
git add scripts/ops_watchdog.py tests/unit/test_ops_watchdog.py
git commit -m "feat(ops): watchdog checks — stuck runs, missing Monday run, stale snapshots, unnotified CRITICALs"
```

---

### Task 5: `ops-watchdog.yml` workflow

**Files:**
- Create: `.github/workflows/ops-watchdog.yml`

- [ ] **Step 1: Create the workflow**

```yaml
name: Ops Watchdog

on:
  schedule:
    # 17:00 UTC Monday — fast check that the weekly run happened (cron 13:00
    # + observed GH delay ~2-2.5h). 23:30 UTC weekdays — snapshot freshness,
    # stuck runs, unnotified CRITICALs (daily snapshot runs 21:30 UTC).
    - cron: "0 17 * * 1"
    - cron: "30 23 * * 1-5"
  workflow_dispatch: {}

concurrency:
  group: ops-watchdog
  cancel-in-progress: false

jobs:
  watch:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    permissions:
      contents: read
      issues: write
    env:
      HEDGE_DATABASE_URL: ${{ secrets.HEDGE_DATABASE_URL }}
      HEDGE_EQUITIES_ENABLED_BRANCHES: ${{ vars.HEDGE_EQUITIES_ENABLED_BRANCHES || 'growth,value' }}
      GH_TOKEN: ${{ github.token }}
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

      - name: Run watchdog
        run: python -m scripts.ops_watchdog

      - name: Alert on failure
        if: failure()
        env:
          GH_TOKEN: ${{ github.token }}
        run: bash .github/scripts/ops_alert.sh "Ops watchdog crashed" "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
```

- [ ] **Step 2: Verify it parses**

Run: `.venv/bin/python -c "import yaml; yaml.safe_load(open('.github/workflows/ops-watchdog.yml')); print('YAML OK')"`
Expected: `YAML OK`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ops-watchdog.yml
git commit -m "feat(ops): scheduled ops-watchdog workflow (Mon 17:00 + weekdays 23:30 UTC)"
```

---

### Task 6: `generate_orders_with_skips` — skip metadata

**Files:**
- Modify: `app/modules/equities/agents/portfolio_manager.py:156-234`
- Test: `tests/unit/equities/test_portfolio_manager.py` (append)

The existing `generate_orders` signature/return is untouched (≈22 test call sites depend on it). The implementation moves into `generate_orders_with_skips`, which also returns skip metadata; `generate_orders` delegates.

- [ ] **Step 1: Write the failing tests (append to `tests/unit/equities/test_portfolio_manager.py`)**

Match the file's existing fixture style — it builds a `PortfolioManager` as `pm` and `CompositeScore` targets via the module's helpers; reuse the same constructors the neighboring full-exit tests at lines 648-718 use. The tests below spell out the essential inputs:

```python
class TestGenerateOrdersWithSkips:
    def _pm(self):
        # identical construction to the full-exit tests above
        return PortfolioManager(
            agents_config=AgentsConfig(),
            portfolio_config=PortfolioConfig(),
            analyst_weights={"news": 0.2, "fundamentals": 0.65, "technical": 0.15},
        )

    def test_unpriced_full_exit_is_reported_as_exit_skip(self):
        pm = self._pm()
        # held name, target 0, NO price → previously an invisible skip
        orders, skips = pm.generate_orders_with_skips(
            [], {"AAPL": 0.05}, 1_000_000.0, {}, current_quantities={"AAPL": 100.0}
        )
        assert orders == []
        assert skips == [{"symbol": "AAPL", "reason": "unpriced", "is_exit": True}]

    def test_below_entry_threshold_skip_is_reported(self):
        pm = self._pm()
        score = _make_composite_score(symbol="TINY", target_weight=0.004)  # < min_entry_weight 0.005
        orders, skips = pm.generate_orders_with_skips(
            [score], {}, 1_000_000.0, {"TINY": 10.0}
        )
        assert orders == []
        assert skips == [{"symbol": "TINY", "reason": "below_entry_threshold", "is_exit": False}]

    def test_unpriced_adjustment_skip_is_reported(self):
        pm = self._pm()
        score = _make_composite_score(symbol="NEWP", target_weight=0.05)
        orders, skips = pm.generate_orders_with_skips(
            [score], {}, 1_000_000.0, {}
        )
        assert orders == []
        assert skips == [{"symbol": "NEWP", "reason": "unpriced", "is_exit": False}]

    def test_generate_orders_still_returns_bare_list(self):
        pm = self._pm()
        result = pm.generate_orders([], {"AAPL": 0.05}, 1_000_000.0, {}, current_quantities={"AAPL": 100.0})
        assert result == []  # list, not tuple

    def test_clean_run_has_no_skips(self):
        pm = self._pm()
        score = _make_composite_score(symbol="MSFT", target_weight=0.05)
        orders, skips = pm.generate_orders_with_skips([score], {}, 1_000_000.0, {"MSFT": 400.0})
        assert len(orders) == 1
        assert skips == []
```

(If `_make_composite_score` in `tests/conftest.py` uses different argument names, mirror the neighboring tests' usage — the intent is a single target with the given symbol and target_weight.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/equities/test_portfolio_manager.py -k "WithSkips or still_returns_bare" -v`
Expected: FAIL — `AttributeError: ... has no attribute 'generate_orders_with_skips'`

- [ ] **Step 3: Implement**

In `portfolio_manager.py`, rename the existing `generate_orders` body into the new method and add skip collection at the three skip points (docstring/comments preserved from the original — shown here in full):

```python
    def generate_orders(
        self,
        target: list[CompositeScore],
        current_positions: dict[str, float],
        nav: float,
        prices: dict[str, float],
        current_quantities: dict[str, float] | None = None,
    ) -> list[RebalanceOrder]:
        """Back-compat wrapper: orders only. See generate_orders_with_skips."""
        orders, _ = self.generate_orders_with_skips(
            target, current_positions, nav, prices, current_quantities=current_quantities
        )
        return orders

    def generate_orders_with_skips(
        self,
        target: list[CompositeScore],
        current_positions: dict[str, float],
        nav: float,
        prices: dict[str, float],
        current_quantities: dict[str, float] | None = None,
    ) -> tuple[list[RebalanceOrder], list[dict]]:
        """Generate BUY/SELL orders by diffing target vs current portfolio.

        current_quantities (symbol -> held share count) enables full exits: a
        held name with target weight 0 sells its ENTIRE held quantity,
        bypassing the rebalance threshold — no fractional dust survives an
        exit. Callers that omit it get the legacy delta-only behavior.

        Also returns skip metadata [{symbol, reason, is_exit}] for every
        symbol it declined to order: reason "unpriced" (no usable price) or
        "below_entry_threshold" (new entry under min_entry_weight). By-design
        sub-threshold ADJUSTMENTS of held names are not reported (noise).
        """
        orders = []
        skips: list[dict] = []
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
                # still skipped — execution could not fill them anyway — but
                # now reported so the digest/risk checks can flag a stuck exit.
                if not price or price <= 0:
                    skips.append({"symbol": symbol, "reason": "unpriced", "is_exit": True})
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
            is_entry = current_weight == 0.0
            threshold = (
                self.portfolio_config.min_entry_weight if is_entry else self.portfolio_config.min_rebalance_threshold
            )
            if abs(delta) < threshold:
                if is_entry and target_weight > 0.0:
                    logger.info(
                        "Skipping sub-threshold entry %s: target %.3f%% < %.3f%%",
                        symbol,
                        target_weight * 100,
                        threshold * 100,
                    )
                    skips.append({"symbol": symbol, "reason": "below_entry_threshold", "is_exit": False})
                continue
            if not price or price <= 0:
                skips.append(
                    {
                        "symbol": symbol,
                        "reason": "unpriced",
                        "is_exit": target_weight == 0.0 and current_weight > 0.0,
                    }
                )
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
        return orders, skips
```

- [ ] **Step 4: Run the full portfolio-manager suite**

Run: `.venv/bin/pytest tests/unit/equities/test_portfolio_manager.py tests/unit/equities/test_order_generation_cash.py -q`
Expected: all PASS (old call sites untouched + new tests green)

- [ ] **Step 5: Commit**

```bash
git add app/modules/equities/agents/portfolio_manager.py tests/unit/equities/test_portfolio_manager.py
git commit -m "feat(equities): generate_orders_with_skips — report unpriced/below-entry skips"
```

---

### Task 7: `build_order_flow` + threading through graph and service

**Files:**
- Create: `app/modules/equities/order_flow.py`
- Modify: `app/modules/equities/agents/graph.py` (state lines 22-35, portfolio_decision lines 237-239, execute_trades lines 241-253)
- Modify: `app/modules/equities/models.py:61-72` (RunResult)
- Modify: `app/modules/equities/service.py` (initial_state ~line 335; result unpacking ~line 367-405)
- Test: `tests/unit/equities/test_order_flow.py` (new), `tests/unit/equities/test_graph.py` (append)

- [ ] **Step 1: Write the failing tests — `tests/unit/equities/test_order_flow.py`**

```python
"""Order-flow accounting: reconcile generated orders vs execution results."""

from app.modules.equities.models import RebalanceOrder
from app.modules.equities.order_flow import build_order_flow


def _order(symbol: str, side: str = "buy") -> RebalanceOrder:
    return RebalanceOrder(symbol=symbol, side=side, quantity=10.0, reason="weight_adjustment")


def test_all_filled():
    orders = [_order("AAA"), _order("BBB")]
    results = [
        {"success": True, "order_id": "1", "status": "filled"},
        {"success": True, "order_id": "2", "status": "filled"},
    ]
    flow = build_order_flow(orders, results, [])
    assert flow["generated"] == 2
    assert flow["persisted"] == 2
    assert flow["filled"] == 2
    assert flow["rejected"] == 0
    assert flow["dropped"] == 0
    assert flow["rejections"] == []


def test_rejection_with_reason_is_counted_and_listed():
    orders = [_order("AAA")]
    results = [{"success": False, "order_id": "1", "status": "rejected", "message": "Insufficient cash: cost 5 > available 1"}]
    flow = build_order_flow(orders, results, [])
    assert flow["rejected"] == 1
    assert flow["persisted"] == 1
    assert flow["rejections"] == [
        {"symbol": "AAA", "side": "buy", "reason": "Insufficient cash: cost 5 > available 1"}
    ]


def test_none_result_is_dropped():
    # e.g. missing instrument_id or an exception swallowed by _execute_trade
    orders = [_order("AAA", side="sell")]
    flow = build_order_flow(orders, [None], [])
    assert flow["dropped"] == 1
    assert flow["persisted"] == 0
    assert flow["rejections"][0]["symbol"] == "AAA"
    assert "never submitted" in flow["rejections"][0]["reason"]


def test_order_id_none_result_is_dropped_with_its_message():
    # legacy validation-drop shape (pre hot-path fix)
    orders = [_order("BLK", side="sell")]
    results = [{"success": False, "order_id": None, "status": "rejected", "message": "Insufficient position: hold 74.0804 BLK, tried to sell 74.0814"}]
    flow = build_order_flow(orders, results, [])
    assert flow["dropped"] == 1
    assert "Insufficient position" in flow["rejections"][0]["reason"]


def test_skips_are_tallied():
    skips = [
        {"symbol": "AAPL", "reason": "unpriced", "is_exit": True},
        {"symbol": "TINY", "reason": "below_entry_threshold", "is_exit": False},
    ]
    flow = build_order_flow([], [], skips)
    assert flow["skipped_unpriced"] == 1
    assert flow["skipped_below_entry"] == 1
    assert flow["skips"] == skips


def test_missing_results_tail_counts_as_dropped():
    # execution loop crashed midway: fewer results than orders
    orders = [_order("AAA"), _order("BBB")]
    results = [{"success": True, "order_id": "1", "status": "filled"}]
    flow = build_order_flow(orders, results, [])
    assert flow["generated"] == 2
    assert flow["filled"] == 1
    assert flow["dropped"] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/equities/test_order_flow.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.modules.equities.order_flow'`

- [ ] **Step 3: Create `app/modules/equities/order_flow.py`**

```python
"""Order-flow accounting: reconcile generated orders against execution results.

Pure logic (no I/O) so the tally is unit-testable and identical for live and
backtest paths. The counts must reconcile: generated = persisted + dropped,
persisted = filled + rejected. `dropped` > 0 means an order left no DB row —
the silent-loss mode behind the 2026-07-20/27 starved buys.
"""

from __future__ import annotations

from app.modules.equities.models import RebalanceOrder


def build_order_flow(
    orders: list[RebalanceOrder],
    order_results: list[dict | None],
    skips: list[dict],
) -> dict:
    """Tally execute-node result dicts (submit_order returns) per order.

    order_results[i] corresponds to orders[i]; None (or a missing tail entry)
    means the order never produced a result — dropped before submission.
    """
    filled = rejected = dropped = 0
    rejections: list[dict] = []
    for i, order in enumerate(orders):
        res = order_results[i] if i < len(order_results) else None
        if not isinstance(res, dict) or res.get("order_id") is None:
            dropped += 1
            reason = (
                res.get("message", "no message")
                if isinstance(res, dict)
                else "never submitted (missing instrument id or execution exception — see logs)"
            )
            rejections.append({"symbol": order.symbol, "side": str(order.side), "reason": reason})
        elif res.get("status") == "filled":
            filled += 1
        else:
            rejected += 1
            rejections.append(
                {"symbol": order.symbol, "side": str(order.side), "reason": res.get("message", "unknown")}
            )
    return {
        "generated": len(orders),
        "persisted": len(orders) - dropped,
        "filled": filled,
        "rejected": rejected,
        "dropped": dropped,
        "skipped_unpriced": sum(1 for s in skips if s.get("reason") == "unpriced"),
        "skipped_below_entry": sum(1 for s in skips if s.get("reason") == "below_entry_threshold"),
        "rejections": rejections,
        "skips": list(skips),
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/unit/equities/test_order_flow.py -v`
Expected: 7 PASS

- [ ] **Step 5: Update the graph-test harness for the new PM method, then write the failing test**

`tests/unit/equities/test_graph.py` centralizes its portfolio-manager stub in `_initial_state` (the `portfolio_manager` is a `MagicMock()` from `_make_deps`, line 26). Once the node calls `generate_orders_with_skips`, unpacking an unconfigured MagicMock raises `TypeError`, so configure the tuple return in `_initial_state` — currently:

```python
        pm.generate_orders.return_value = [
            RebalanceOrder(symbol=f"SYM{i}", side="buy", quantity=1.0, reason="new_position") for i in range(n_orders)
        ]
```

becomes:

```python
        generated = [
            RebalanceOrder(symbol=f"SYM{i}", side="buy", quantity=1.0, reason="new_position") for i in range(n_orders)
        ]
        pm.generate_orders.return_value = generated
        pm.generate_orders_with_skips.return_value = (generated, [])
```

(If any other test in the file stubs `generate_orders` directly rather than via `_initial_state`, give it the same `(orders, [])` tuple stub for `generate_orders_with_skips`.)

Then append inside the SAME class as `test_trades_counts_only_successful_fills`:

```python
    async def test_execute_trades_returns_order_results(self):
        """execute_trades keeps every submit_order result (fills, rejects, None) in order."""
        trade_results = [
            {"success": True, "order_id": "o1", "status": "filled"},
            {"success": False, "order_id": None, "status": "rejected", "message": "Insufficient position"},
            None,
        ]
        state, _ = self._initial_state(trade_results, n_orders=3)

        graph = build_equities_graph("growth")
        result = await graph.ainvoke(state)

        assert result["trades"] == [{"success": True, "order_id": "o1", "status": "filled"}]
        assert result["order_results"] == trade_results
```

- [ ] **Step 6: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/equities/test_graph.py -k order_results -v`
Expected: FAIL — `KeyError: 'order_results'` (node doesn't return it yet)

- [ ] **Step 7: Modify `graph.py`**

State (lines 22-35) — add two keys:

```python
class EquitiesWorkflowState(TypedDict, total=False):
    """State passed through the LangGraph workflow."""

    branch_name: str
    branch_id: str
    universe: list
    screened: list
    signals: Annotated[list, operator.add]
    scores: list
    targets: list
    orders: list
    order_skips: list
    order_results: list
    trades: list
    deps: dict
    news_context: dict
```

`portfolio_decision` node (lines 237-239):

```python
        orders, order_skips = pm.generate_orders_with_skips(
            sized, current_positions, nav, prices, current_quantities=current_quantities
        )
        logger.info(
            "Portfolio manager generated %d orders (%d skipped)", len(orders), len(order_skips)
        )
        return {"scores": scores, "targets": sized, "orders": orders, "order_skips": order_skips}
```

`execute_trades` node (lines 241-253):

```python
    async def execute_trades(state: EquitiesWorkflowState) -> dict:
        deps = state["deps"]
        trade_fn = deps.get("execute_trade_fn")
        trades = []
        order_results: list[dict | None] = []
        if trade_fn:
            for order in state.get("orders", []):
                trade = await trade_fn(order)
                # submit_order returns a dict for both fills and rejections;
                # None means the order never reached the broker. Keep every
                # result so order-flow accounting can reconcile the run.
                order_results.append(trade if isinstance(trade, dict) else None)
                if isinstance(trade, dict) and trade.get("success"):
                    trades.append(trade)
        logger.info("Executed %d trades", len(trades))
        return {"trades": trades, "order_results": order_results}
```

- [ ] **Step 8: Modify `models.py` RunResult (lines 61-72) — add one field**

```python
    # Weights the composite actually used (2026-07-16 adaptive weights spec)
    analyst_weights_report: AnalystWeightsReport | None = None
    # Reconciling order accounting (see order_flow.build_order_flow); None
    # when the run executed no orders path (no execution service wired).
    order_flow: dict | None = None
```

- [ ] **Step 9: Modify `service.py`**

Add the import near the other equities imports at the top of the file:

```python
from app.modules.equities.order_flow import build_order_flow
```

In `initial_state` (~line 335), add the two new keys after `"orders": []`:

```python
            "orders": [],
            "order_skips": [],
            "order_results": [],
```

After the result unpacking (lines 369-374), add:

```python
        order_skips = result.get("order_skips", [])
        order_flow = None
        if execute_trade_fn is not None:
            order_flow = build_order_flow(orders, result.get("order_results", []), order_skips)
            if order_flow["dropped"] or order_flow["rejected"]:
                logger.warning(
                    "Order flow for %s: %d generated / %d persisted / %d filled / %d rejected / %d dropped",
                    branch_name,
                    order_flow["generated"],
                    order_flow["persisted"],
                    order_flow["filled"],
                    order_flow["rejected"],
                    order_flow["dropped"],
                )
```

And in the `RunResult(...)` return (lines 396-405), add:

```python
            order_flow=order_flow,
```

- [ ] **Step 10: Run the affected suites**

Run: `.venv/bin/pytest tests/unit/equities/ -q`
Expected: all PASS (incl. the new graph test)

- [ ] **Step 11: Commit**

```bash
git add app/modules/equities/order_flow.py app/modules/equities/agents/graph.py app/modules/equities/models.py app/modules/equities/service.py tests/unit/equities/test_order_flow.py tests/unit/equities/test_graph.py
git commit -m "feat(equities): order-flow accounting threaded through pipeline result"
```

---

### Task 8: Order-flow into summary + digest

(amended per Task 8 code review: dropped count + kind-split sub-lines)

**Files:**
- Modify: `app/modules/equities/weekly_runner.py` (`WeeklyRunSummary` lines 74-87; `execute` lines 253-274; `render_digest` lines 372-379)
- Test: `tests/unit/equities/test_digest_portfolio_report.py` (append), `tests/unit/equities/test_weekly_runner.py` (extend helper + append)

- [ ] **Step 1: Write the failing digest tests (append to `tests/unit/equities/test_digest_portfolio_report.py`)**

The file has a `_summary(**over)` builder for `WeeklyRunSummary` (defaults: `orders_placed=8`) — use it directly:

```python
def _flow(**kw):
    base = {
        "generated": 13, "persisted": 13, "filled": 8, "rejected": 5, "dropped": 0,
        "skipped_unpriced": 0, "skipped_below_entry": 0,
        "rejections": [
            {"symbol": s, "side": "buy", "reason": "Insufficient cash: cost 9 > available 1"}
            for s in ("BKNG", "CSCO", "JPM", "MA", "MU")
        ],
        "skips": [],
    }
    base.update(kw)
    return base


def test_digest_renders_reconciling_orders_line():
    digest = render_digest([_summary(order_flow=_flow())], run_date=date(2026, 8, 3))
    assert "- Orders: 13 generated / 13 persisted / 8 filled / 5 rejected" in digest
    assert "rejected: BKNG, CSCO, JPM, MA, MU — Insufficient cash" in digest
    assert "Orders placed:" not in digest


def test_digest_renders_skips_line_with_exit_flag():
    flow = _flow(
        rejected=0, filled=13, rejections=[],
        skipped_unpriced=1,
        skips=[{"symbol": "AAPL", "reason": "unpriced", "is_exit": True}],
    )
    digest = render_digest([_summary(order_flow=flow)], run_date=date(2026, 8, 3))
    assert "skipped: AAPL (unpriced, exit)" in digest


def test_digest_legacy_fallback_without_order_flow():
    digest = render_digest([_summary()], run_date=date(2026, 8, 3))
    assert "- Orders placed: 8" in digest
```

- [ ] **Step 2: Write the failing runner test (`tests/unit/equities/test_weekly_runner.py`)**

First extend the file's `_make_run_result` helper (line 118) with a pass-through parameter:

```python
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
```

Then copy `test_execute_creates_row_when_no_prior_run` (line 190) verbatim as `test_execute_propagates_order_flow_into_summary_and_summary_json`, with exactly two deltas: define

```python
    FLOW = {"generated": 2, "persisted": 2, "filled": 1, "rejected": 1, "dropped": 0,
            "skipped_unpriced": 0, "skipped_below_entry": 0, "rejections": [], "skips": []}
```

make the run_fn return `_make_run_result(branch, order_flow=FLOW)` instead of `_make_run_result(branch)`, and append these assertions at the end (the file's `_FakeRepo` records `mark_completed` calls as `(run_id, summary)` tuples in `repo.completed`):

```python
    assert summary.order_flow == FLOW
    assert repo.completed[0][1]["order_flow"] == FLOW
```

- [ ] **Step 3: Run to verify failures**

Run: `.venv/bin/pytest tests/unit/equities/test_digest_portfolio_report.py tests/unit/equities/test_weekly_runner.py -q`
Expected: new tests FAIL (`TypeError: RunResult ... unexpected keyword 'order_flow'` until Task 7's model change is present — Task 7 precedes this — then `WeeklyRunSummary ... unexpected keyword 'order_flow'` / missing digest line)

- [ ] **Step 4: Implement in `weekly_runner.py`**

`WeeklyRunSummary` (after line 87's `analyst_weights_report` field):

```python
    order_flow: dict | None = None
```

`execute` (lines 253-274) — thread it through:

```python
        duration = time.monotonic() - t0
        orders_placed = len(result.orders)
        order_flow = getattr(result, "order_flow", None)
        summary_dict = {
            "universe_count": result.universe_count,
            "screened_count": result.screened_count,
            "orders_placed": orders_placed,
            "trades_executed": result.trades_executed,
            "duration_seconds": duration,
            "order_flow": order_flow,
        }
        await self._mark_completed(run_id, summary_dict)

        return WeeklyRunSummary(
            run_id=run_id,
            branch_name=branch_name,
            status="completed",
            universe_count=result.universe_count,
            screened_count=result.screened_count,
            orders_placed=orders_placed,
            trades_executed=result.trades_executed,
            duration_seconds=duration,
            analyst_weights_report=getattr(result, "analyst_weights_report", None),
            order_flow=order_flow,
        )
```

`render_digest` — replace lines 375-379 (`- Orders placed:` through the `⚠️ 0 orders` warning) with:

```python
            if s.order_flow:
                flow = s.order_flow
                head = (
                    f"- Orders: {flow['generated']} generated / {flow['persisted']} persisted / "
                    f"{flow['filled']} filled / {flow['rejected']} rejected"
                )
                if flow.get("dropped"):
                    head += f" / {flow['dropped']} DROPPED"
                lines.append(head)
                for kind, label in (("dropped", "dropped (no DB row)"), ("rejected", "rejected")):
                    group = [r for r in flow.get("rejections", []) if r.get("kind") == kind]
                    if group:
                        syms = ", ".join(r["symbol"] for r in group)
                        lines.append(f"  - {label}: {syms} — {str(group[0].get('reason', ''))[:80]}")
                for skip in flow.get("skips", []):
                    suffix = ", exit" if skip.get("is_exit") else ""
                    lines.append(f"  - skipped: {skip['symbol']} ({skip['reason']}{suffix})")
            else:
                lines.append(f"- Orders placed: {s.orders_placed}")
            lines.append(f"- Trades executed: {s.trades_executed}")
            lines.append(f"- Duration: {_format_duration(s.duration_seconds)}")
            if s.orders_placed == 0:
                lines.append("- ⚠️ 0 orders — check data freshness")
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/pytest tests/unit/equities/test_digest_portfolio_report.py tests/unit/equities/test_weekly_runner.py tests/unit/test_weekly_cli_ordering.py -q`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add app/modules/equities/weekly_runner.py tests/unit/equities/test_digest_portfolio_report.py tests/unit/equities/test_weekly_runner.py
git commit -m "feat(equities): reconciling order-flow line in summary_json and digest"
```

---

### Task 9: Risk checks — lost/rejected/unpriced + CASH_PCT_WARN 0.03

**Files:**
- Modify: `app/modules/equities/risk_checks.py` (constant line 17; `evaluate_post_run_invariants` lines 21-85)
- Modify: `scripts/run_weekly_pipeline.py` (`_evaluate_and_persist_alerts` lines 179-208 and its call site ~line 313)
- Test: `tests/unit/equities/test_risk_checks.py` (append + adjust threshold tests), `tests/unit/test_weekly_risk_wiring.py` (adjust)

- [ ] **Step 1: Write the failing tests (append to `tests/unit/equities/test_risk_checks.py`)**

Follow the file's existing call style (it calls `evaluate_post_run_invariants(cash=..., nav=..., position_weights=..., portfolio_config=..., branch_id=..., branch_name=...)` 13 times):

```python
def _flow(**kw):
    base = {"generated": 0, "persisted": 0, "filled": 0, "rejected": 0, "dropped": 0,
            "skipped_unpriced": 0, "skipped_below_entry": 0, "rejections": [], "skips": []}
    base.update(kw)
    return base


class TestOrderFlowChecks:
    def _eval(self, flow):
        return evaluate_post_run_invariants(
            cash=10_000.0, nav=1_000_000.0, position_weights={},
            portfolio_config=PortfolioConfig(), branch_id="b-1", branch_name="growth",
            order_flow=flow,
        )

    def test_lost_orders_is_critical(self):
        alerts = self._eval(_flow(generated=13, persisted=9, filled=4, rejected=5, dropped=4))
        lost = [a for a in alerts if a.metric == "orders_lost"]
        assert len(lost) == 1
        assert str(lost[0].level) == "critical"
        assert "4" in lost[0].message

    def test_rejected_orders_is_warning_with_symbols(self):
        flow = _flow(generated=2, persisted=2, filled=1, rejected=1,
                     rejections=[{"symbol": "MSFT", "side": "buy", "reason": "Insufficient cash"}])
        alerts = self._eval(flow)
        rej = [a for a in alerts if a.metric == "orders_rejected"]
        assert len(rej) == 1
        assert str(rej[0].level) == "warning"
        assert "MSFT" in rej[0].message

    def test_unpriced_exit_skip_is_warning_naming_the_exit(self):
        flow = _flow(skipped_unpriced=1,
                     skips=[{"symbol": "AAPL", "reason": "unpriced", "is_exit": True}])
        alerts = self._eval(flow)
        skip = [a for a in alerts if a.metric == "orders_skipped_unpriced"]
        assert len(skip) == 1
        assert "AAPL" in skip[0].message

    def test_below_entry_skips_do_not_alert(self):
        flow = _flow(skipped_below_entry=3,
                     skips=[{"symbol": "T", "reason": "below_entry_threshold", "is_exit": False}] * 3)
        assert self._eval(flow) == []

    def test_none_order_flow_keeps_legacy_behavior(self):
        alerts = evaluate_post_run_invariants(
            cash=10_000.0, nav=1_000_000.0, position_weights={},
            portfolio_config=PortfolioConfig(), branch_id="b-1", branch_name="growth",
        )
        assert alerts == []

    def test_clean_flow_is_quiet(self):
        assert self._eval(_flow(generated=5, persisted=5, filled=5)) == []


def test_cash_pct_warn_is_now_three_percent():
    alerts = evaluate_post_run_invariants(
        cash=35_000.0, nav=1_000_000.0, position_weights={},
        portfolio_config=PortfolioConfig(), branch_id="b-1", branch_name="value",
    )
    assert [a.metric for a in alerts] == ["cash_pct"]  # 3.5% now trips the 3% warn
```

Also UPDATE the existing cash-pct boundary tests in this file: any test asserting 4-5% cash is quiet must move its quiet case below 3% (e.g. 2.9%) — locate with `grep -n "cash_pct\|CASH_PCT" tests/unit/equities/test_risk_checks.py` and adjust the constants, keeping the test intent (boundary just-below / just-above).

- [ ] **Step 2: Run to verify failures**

Run: `.venv/bin/pytest tests/unit/equities/test_risk_checks.py -q`
Expected: new tests FAIL (`TypeError: unexpected keyword argument 'order_flow'`), plus the 3.5%-cash test failing under the old 5% constant.

- [ ] **Step 3: Implement in `risk_checks.py`**

Line 17: `CASH_PCT_WARN = 0.03` (update the comment: sizing targets 1% cash; 3% = drift worth a look — value sat at 3.49% unalerted on 2026-07-27).

Signature: add the parameter after `branch_name`:

```python
def evaluate_post_run_invariants(
    *,
    cash: float,
    nav: float,
    position_weights: dict[str, float],
    portfolio_config: PortfolioConfig,
    branch_id: str,
    branch_name: str,
    order_flow: dict | None = None,
) -> list[RiskAlert]:
```

Before the final `return alerts` (after the position-cap loop, line 83), append:

```python
    # Order-path integrity (2026-07-30 S2). order_flow is None for callers
    # predating the accounting (backtests, old tests) — checks skip.
    if order_flow:
        lost = int(order_flow.get("generated", 0)) - int(order_flow.get("persisted", 0))
        if lost > 0:
            alerts.append(
                RiskAlert(
                    level=RiskAlertLevel.CRITICAL,
                    source=branch_id,
                    metric="orders_lost",
                    current_value=float(lost),
                    threshold=0.0,
                    message=(
                        f"{branch_name}: {lost} order(s) vanished between generation and submission "
                        "— every generated order must persist as filled or rejected"
                    ),
                    action_required="Investigate trade_execution logs (silent-drop mode of 2026-07-20/27).",
                    affected_branches=[branch_name],
                )
            )
        rejected = int(order_flow.get("rejected", 0))
        if rejected > 0:
            syms = ", ".join(r.get("symbol", "?") for r in order_flow.get("rejections", [])) or "unknown"
            alerts.append(
                RiskAlert(
                    level=RiskAlertLevel.WARNING,
                    source=branch_id,
                    metric="orders_rejected",
                    current_value=float(rejected),
                    threshold=0.0,
                    message=f"{branch_name}: {rejected} order(s) rejected ({syms})",
                    affected_branches=[branch_name],
                )
            )
        unpriced = [s for s in order_flow.get("skips", []) if s.get("reason") == "unpriced"]
        if unpriced:
            exits = [s["symbol"] for s in unpriced if s.get("is_exit")]
            message = f"{branch_name}: {len(unpriced)} order(s) skipped for missing prices"
            if exits:
                message += f" — includes EXIT(s) {', '.join(exits)}: position stuck until priced"
            alerts.append(
                RiskAlert(
                    level=RiskAlertLevel.WARNING,
                    source=branch_id,
                    metric="orders_skipped_unpriced",
                    current_value=float(len(unpriced)),
                    threshold=0.0,
                    message=message,
                    affected_branches=[branch_name],
                )
            )

    return alerts
```

- [ ] **Step 4: Wire through `scripts/run_weekly_pipeline.py`**

`_evaluate_and_persist_alerts` signature (lines 179-189) — add parameter:

```python
async def _evaluate_and_persist_alerts(
    *,
    session,
    alert_repo,
    event_log,
    mtm,
    portfolio_config,
    branch_id: str,
    branch_name: str,
    status: str,
    order_flow: dict | None = None,
) -> list[dict]:
```

The `evaluate_post_run_invariants` call (lines 201-208) — add `order_flow=order_flow,`.

The call site (~line 313, inside `_mark_snapshot_and_report`) — add `order_flow=summary.order_flow,` to the `_evaluate_and_persist_alerts(...)` arguments (the summary is in scope; check the surrounding kwargs and match style).

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/pytest tests/unit/equities/test_risk_checks.py tests/unit/test_weekly_risk_wiring.py -q`
Expected: all PASS (adjust any `test_weekly_risk_wiring.py` fixture that constructs `WeeklyRunSummary` without `order_flow` — the field defaults to None, so most should pass unchanged).

- [ ] **Step 6: Commit**

```bash
git add app/modules/equities/risk_checks.py scripts/run_weekly_pipeline.py tests/unit/equities/test_risk_checks.py tests/unit/test_weekly_risk_wiring.py
git commit -m "feat(risk): order-flow invariants (lost=CRITICAL, rejected/unpriced=WARNING); cash warn 5%->3%"
```

---

### Task 10: Hot path — persist validation rejections as REJECTED rows

**Files:**
- Modify: `app/modules/trade_execution/service.py:51-54` (the validation early-return)
- Test: `tests/unit/test_trade_execution_service.py` (append + adjust), `tests/unit/test_trade_execution_sequence.py` (adjust one assertion if needed)

- [ ] **Step 1: Write the failing test**

`tests/unit/test_trade_execution_service.py` provides `deps` (a tuple of five `AsyncMock`s: order_repo, trade_repo, broker, event_log, portfolio_service) and `service` fixtures, plus `_make_order_request` / `_make_portfolio` helpers. Append this test inside the SAME class as `test_sell_insufficient_position_fails` (which sits at ~line 271 and builds a `Position` the same way):

```python
    async def test_validation_rejection_persists_rejected_order_row(self, service, deps):
        """BLK replay (2026-07-20): oversell by 0.001 sh leaves a REJECTED row + event, broker untouched."""
        order_repo, _, broker, event_log, portfolio_service = deps
        portfolio_service.get_portfolio.return_value = _make_portfolio()
        portfolio_service.get_position_by_symbol.return_value = Position(
            id="p1",
            portfolio_id="port-1",
            instrument_id="inst-1",
            symbol="BLK",
            long_quantity=74.0804,
            updated_at=datetime.now(UTC),
        )
        order_repo.create.side_effect = lambda o: o  # echo the order back, like the real repo

        req = _make_order_request(symbol="BLK", side=OrderSide.SELL, quantity=74.0814)
        result = await service.submit_order(req)

        assert result["success"] is False
        assert result["status"] == "rejected"
        assert result["order_id"] is not None  # ← the new behavior (was None)
        assert "Insufficient position" in result["message"]
        # order persisted then flipped to REJECTED with the validation reason
        order_repo.create.assert_awaited_once()
        assert order_repo.create.await_args.args[0].symbol == "BLK"
        order_repo.update_status.assert_awaited_once()
        upd_args, upd_kwargs = order_repo.update_status.await_args
        assert upd_args[1] == OrderStatus.REJECTED
        assert "Insufficient position" in upd_kwargs["rejection_reason"]
        # rejection event logged; broker never called
        event_log.append.assert_awaited_once()
        assert type(event_log.append.await_args.args[0]).__name__ == "TradeRejectedEvent"
        broker.submit_order.assert_not_awaited()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/test_trade_execution_service.py -k validation_rejection_persists -v`
Expected: FAIL — `result["order_id"] is None`

- [ ] **Step 3: Implement in `trade_execution/service.py`**

Replace lines 51-54:

```python
        # Validate
        validation_error = await self._validate_order(req)
        if validation_error:
            return {"success": False, "order_id": None, "status": "rejected", "message": validation_error}
```

with (create-then-update mirrors the proven fill-time rejection path at lines 123-135, so no new repo capabilities are needed):

```python
        # Validate. A failed validation is persisted as a REJECTED order row +
        # rejection event — before 2026-07-30 it returned bare, leaving no
        # trace (8 sells ≈$530k silently dropped on 07-20/27, starving buys).
        validation_error = await self._validate_order(req)
        if validation_error:
            order = Order(
                id=str(uuid.uuid4()),
                branch_id=req.branch_id,
                instrument_id=req.instrument_id,
                symbol=req.symbol,
                side=req.side,
                order_type=req.order_type,
                quantity=req.quantity,
                limit_price=req.limit_price,
                stop_price=req.stop_price,
                time_in_force=req.time_in_force,
                status=OrderStatus.PENDING,
                confidence=req.confidence,
                reasoning=req.reasoning,
                agent_signals=req.agent_signals,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            order = await self.order_repo.create(order)
            await self.order_repo.update_status(
                order.id, OrderStatus.REJECTED, rejection_reason=validation_error
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
                    rejection_reason=validation_error,
                )
            )
            return {"success": False, "order_id": order.id, "status": "rejected", "message": validation_error}
```

- [ ] **Step 4: Run the trade-execution suites; fix stale expectations**

Run: `.venv/bin/pytest tests/unit/test_trade_execution_service.py tests/unit/test_trade_execution_sequence.py tests/unit/test_trade_execution_cash_check.py tests/unit/equities/test_graph.py -q`

Verified impact of the change on existing tests:
- `test_trade_execution_service.py:271-297` (`test_sell_insufficient_position_fails`, `test_sell_no_position_fails`) call `service._validate_order(req)` DIRECTLY — they test the validator in isolation and are unaffected.
- `test_trade_execution_sequence.py:163` asserts `"Insufficient position" in result["message"]` after a real `submit_order` — the message is unchanged, so it passes; if that test (or a neighbor) also asserts order-repo call counts or `order_id is None`, update it to the persisted-row expectation (non-None id, one `create` + one REJECTED `update_status`).
- `test_graph.py:199` feeds a stubbed `{"order_id": None}` result INTO the graph — still-valid input (the tally must tolerate the legacy shape); no change.

All must pass before proceeding.

- [ ] **Step 5: Commit**

```bash
git add app/modules/trade_execution/service.py tests/unit/test_trade_execution_service.py tests/unit/test_trade_execution_sequence.py
git commit -m "feat(execution): persist validation failures as REJECTED order rows + events"
```

---

### Task 11: Full verification gate + handoff

- [ ] **Step 1: Full unit suite**

Run: `cd /Users/franco_lu/dev/ai-hedgefund-final && .venv/bin/pytest tests/unit/ -q`
Expected: **≥ 1,248 + ~35 new, 0 failed**. Investigate ANY failure — do not skip/xfail around one.

- [ ] **Step 2: Lint**

Run: `.venv/bin/ruff check app/ tests/ scripts/ && .venv/bin/ruff format --check app/modules/equities/order_flow.py scripts/ops_watchdog.py tests/unit/test_ops_watchdog.py tests/unit/test_ops_alert_script.py tests/unit/equities/test_order_flow.py`
Expected: clean. (`ruff format` only on NEW files — ~40 files of pre-existing format drift are expected and must not be reformatted.)

- [ ] **Step 3: Reconciliation sanity check of the whole feature**

Run: `.venv/bin/pytest tests/unit/equities/test_order_flow.py tests/unit/equities/test_risk_checks.py tests/unit/test_ops_watchdog.py -q`
Expected: all PASS.

- [ ] **Step 4: Final commit if anything is pending, then report**

Deliverable: branch `feat/alerting-order-integrity` in `~/dev/ai-hedgefund-final` with Tasks 1-10 as separate commits, suite green. Do NOT push and do NOT merge to main — the user merges and pushes (deploy trigger).

**Post-merge (user actions, documented for the report):** merge → push before Sunday night; manually dispatch `Ops Watchdog` once from the Actions tab (expect green, likely no issues); Monday checklist gains "no `ops-alert` issues open; digest orders line reconciles".

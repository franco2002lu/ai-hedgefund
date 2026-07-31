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

import json
import logging
import os
import subprocess
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime

from dotenv import load_dotenv

load_dotenv()

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
        [
            "gh",
            "label",
            "create",
            "ops-alert",
            "--force",
            "--description",
            "Automated operational alert",
            "--color",
            "D93F0B",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    listing = run(
        ["gh", "issue", "list", "--state", "open", "--label", "ops-alert", "--json", "number,title"],
        check=True,
        capture_output=True,
        text=True,
    )
    for issue in json.loads(listing.stdout or "[]"):
        if issue["title"] == title:
            run(
                ["gh", "issue", "comment", str(issue["number"]), "--body", f"Recurred: {body}"],
                check=True,
                capture_output=True,
                text=True,
            )
            return "commented"
    run(
        ["gh", "issue", "create", "--title", title, "--label", "ops-alert", "--body", body],
        check=True,
        capture_output=True,
        text=True,
    )
    return "created"

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
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

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
    """Walk back n weekdays from today (weekends don't count).

    Holidays are NOT accounted for — weekends only (see design §2).
    """
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

    Returns 'created' | 'commented' | 'dry-run'. Shared contract with
    .github/scripts/ops_alert.sh: identical title format
    `[ops-alert] {subject} — {UTC date}`, the `ops-alert` label, and
    comment-vs-create dedup by exact open-issue title. Bodies/label-failure
    handling intentionally differ.
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

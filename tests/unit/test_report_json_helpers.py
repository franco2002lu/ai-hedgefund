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

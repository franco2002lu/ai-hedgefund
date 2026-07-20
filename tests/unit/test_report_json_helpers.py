"""Snapshot series dedupe: last snapshot per NY date wins."""

from datetime import UTC, datetime

import pytest

from scripts.build_report_json import FUND_NOTES, build_fund_summary, dedupe_last_per_day


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

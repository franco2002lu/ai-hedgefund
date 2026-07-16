"""Fund summary derived inception metrics (service-level, fake repo)."""

import pytest

from app.modules.portfolio.service import PortfolioService


class FakeRepo:
    def __init__(self, summary):
        self._summary = summary

    async def get_fund_summary(self, fund_id):
        return self._summary


def _svc(summary):
    return PortfolioService(
        portfolio_repo=FakeRepo(summary),
        position_repo=None,
        snapshot_repo=None,
        event_log=None,
    )


async def test_fund_summary_adds_branch_and_fund_returns():
    raw = {
        "fund_id": "f-1",
        "total_aum": 2_000_000.0,
        "total_nav": 2_030_000.0,
        "total_cash": 30_000.0,
        "total_long_exposure": 2_000_000.0,
        "total_short_exposure": 0.0,
        "execution_mode": "paper",
        "branches": [
            {"branch_id": "b-1", "allocated_capital": 1_000_000.0, "nav": 1_010_000.0, "inception_date": "2026-06-10"},
            {"branch_id": "b-2", "allocated_capital": 1_000_000.0, "nav": 1_020_000.0, "inception_date": "2026-06-10"},
        ],
    }
    out = await _svc(raw).get_fund_summary("f-1")
    b1 = out["branches"][0]
    assert b1["initial_capital"] == 1_000_000.0
    assert b1["total_pnl"] == pytest.approx(10_000.0)
    assert b1["total_return_pct"] == pytest.approx(0.01)
    assert out["total_initial_capital"] == 2_000_000.0
    assert out["total_pnl"] == pytest.approx(30_000.0)
    assert out["total_return_pct"] == pytest.approx(0.015)


async def test_fund_summary_zero_initial_capital_yields_none_pct():
    raw = {
        "fund_id": "f-1",
        "total_aum": 0.0,
        "total_nav": 500.0,
        "total_cash": 500.0,
        "total_long_exposure": 0.0,
        "total_short_exposure": 0.0,
        "execution_mode": "paper",
        "branches": [{"branch_id": "b-1", "allocated_capital": 0.0, "nav": 500.0, "inception_date": None}],
    }
    out = await _svc(raw).get_fund_summary("f-1")
    assert out["branches"][0]["total_return_pct"] is None
    assert out["total_return_pct"] is None

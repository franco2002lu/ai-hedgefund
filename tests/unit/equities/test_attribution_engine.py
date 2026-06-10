"""tests/unit/equities/test_attribution_engine.py"""

import json
import uuid
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.equities.attribution import AttributionEngine

BRANCH_ID = str(uuid.uuid4())


def _decision_row(decided_at: date):
    row = MagicMock()
    row.decided_at = datetime(decided_at.year, decided_at.month, decided_at.day, 16, 0)
    row.branch_name = "growth"
    row.target_holdings = {"A": 0.6, "B": 0.4}
    row.composite_scores = {}
    row.orders_generated = [
        {"symbol": "A", "side": "buy", "quantity": 1, "reason": "new_position"},
        {"symbol": "B", "side": "buy", "quantity": 1, "reason": "new_position"},
    ]
    row.screening_run_id = uuid.uuid4()
    return row


def _signal_row(symbol, a_type, score):
    row = MagicMock()
    row.symbol, row.analyst_type, row.bullish_score = symbol, a_type, score
    return row


def _data_service(price_map):
    ds = MagicMock()

    async def get_prices(symbol, start_date, end_date, **kw):
        closes = price_map.get(symbol)
        if closes is None:
            raise RuntimeError("no data")
        return {
            "bars": [
                {"timestamp": (start_date + timedelta(days=i)).isoformat(), "close": c} for i, c in enumerate(closes)
            ]
        }

    ds.get_prices = AsyncMock(side_effect=get_prices)
    return ds


def _session_returning(decision, signals, existing_report=None):
    """Mock AsyncSession whose execute() returns decision, then signals, then existing report."""
    session = MagicMock()
    results = []
    for payload in (decision, signals, existing_report):
        r = MagicMock()
        if isinstance(payload, list):
            r.scalars.return_value.all.return_value = payload
        else:
            r.scalar_one_or_none.return_value = payload
        results.append(r)
    session.execute = AsyncMock(side_effect=results)
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


async def test_compute_returns_report_and_persists_new_row():
    decision = _decision_row(date(2026, 6, 1))
    signals = [_signal_row(s, "fundamentals", i + 1) for i, s in enumerate("ABCDE")]
    prices = {
        "A": [100, 110],
        "B": [50, 55],
        "C": [10, 10],
        "D": [20, 20],
        "E": [30, 30],
        "VOOG": [200, 202],
        "SPY": [400, 400],
    }
    session = _session_returning(decision, signals)
    engine = AttributionEngine(data_service=_data_service(prices))

    report = await engine.compute_and_persist(
        session, branch_id=BRANCH_ID, branch_name="growth", as_of=date(2026, 6, 8)
    )

    assert report is not None
    assert report.basket_return_conviction == pytest.approx(0.6 * 0.10 + 0.4 * 0.10)
    session.add.assert_called_once()
    persisted = session.add.call_args.args[0]
    assert persisted.branch_name == "growth"
    assert json.dumps(persisted.analyst_ics)  # JSON-serializable


async def test_returns_none_when_no_prior_decision():
    session = _session_returning(None, [])
    engine = AttributionEngine(data_service=_data_service({}))
    report = await engine.compute_and_persist(
        session, branch_id=BRANCH_ID, branch_name="growth", as_of=date(2026, 6, 8)
    )
    assert report is None


async def test_returns_none_when_decision_stale():
    decision = _decision_row(date(2026, 5, 1))  # > 14 days before as_of
    session = _session_returning(decision, [])
    engine = AttributionEngine(data_service=_data_service({}))
    report = await engine.compute_and_persist(
        session, branch_id=BRANCH_ID, branch_name="growth", as_of=date(2026, 6, 8)
    )
    assert report is None


async def test_upsert_overwrites_existing_row():
    decision = _decision_row(date(2026, 6, 1))
    existing = MagicMock()
    prices = {"A": [100, 110], "B": [50, 55], "VOOG": [200, 202], "SPY": [400, 400]}
    session = _session_returning(decision, [], existing_report=existing)
    engine = AttributionEngine(data_service=_data_service(prices))

    report = await engine.compute_and_persist(
        session, branch_id=BRANCH_ID, branch_name="growth", as_of=date(2026, 6, 8)
    )

    assert report is not None
    session.add.assert_not_called()  # updated in place instead
    assert existing.basket_return_conviction == pytest.approx(report.basket_return_conviction)

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


def _date_bar_data_service(price_map):
    """Mock data service emitting bars whose timestamps are `date` objects,
    matching real PriceBar.model_dump() output from DataPlatformService."""
    ds = MagicMock()

    async def get_prices(symbol, start_date, end_date, **kw):
        closes = price_map.get(symbol)
        if closes is None:
            raise RuntimeError("no data")
        return {"bars": [{"timestamp": start_date + timedelta(days=i), "close": c} for i, c in enumerate(closes)]}

    ds.get_prices = AsyncMock(side_effect=get_prices)
    return ds


async def test_date_object_timestamps_parsed_correctly():
    """Real PriceBar.timestamp is a datetime.date — engine must not crash on it."""
    decision = _decision_row(date(2026, 6, 1))
    prices = {"A": [100, 110], "B": [50, 55], "VOOG": [200, 202], "SPY": [400, 400]}
    session = _session_returning(decision, [])
    engine = AttributionEngine(data_service=_date_bar_data_service(prices))

    report = await engine.compute_and_persist(
        session, branch_id=BRANCH_ID, branch_name="growth", as_of=date(2026, 6, 8)
    )

    assert report is not None
    assert report.basket_return_conviction == pytest.approx(0.6 * 0.10 + 0.4 * 0.10)
    assert report.n_holdings_priced == 2
    session.add.assert_called_once()


async def test_partial_price_failure_still_computes_from_remaining():
    """One symbol's fetch raising must not sink the report (except branch)."""
    decision = _decision_row(date(2026, 6, 1))
    # "B" missing from map -> get_prices raises for it
    prices = {"A": [100, 110], "VOOG": [200, 202], "SPY": [400, 400]}
    session = _session_returning(decision, [])
    engine = AttributionEngine(data_service=_data_service(prices))

    report = await engine.compute_and_persist(
        session, branch_id=BRANCH_ID, branch_name="growth", as_of=date(2026, 6, 8)
    )

    assert report is not None
    assert report.n_holdings == 2
    assert report.n_holdings_priced == 1
    assert report.basket_return_conviction == pytest.approx(0.10)  # renormalized to A only
    session.add.assert_called_once()


def _end_exclusive_data_service(series_map):
    """Mock matching the real YahooFinanceAdapter: yfinance's `end` is EXCLUSIVE,
    so bars are emitted only for dates >= start_date and STRICTLY BEFORE end_date."""
    ds = MagicMock()

    async def get_prices(symbol, start_date, end_date, **kw):
        series = series_map.get(symbol)
        if series is None:
            raise RuntimeError("no data")
        return {"bars": [{"timestamp": d.isoformat(), "close": c} for d, c in series if start_date <= d < end_date]}

    ds.get_prices = AsyncMock(side_effect=get_prices)
    return ds


async def test_window_end_close_included_despite_exclusive_end():
    """Regression: yfinance end dates are exclusive — the engine must pad the
    fetch so the close ON as_of is included, else every weekly window is
    measured to as_of−1 (Mon→Fri instead of Mon→next-Mon close)."""
    d0, d1 = date(2026, 6, 1), date(2026, 6, 8)

    def daily(closes):
        return [(d0 + timedelta(days=i), c) for i, c in enumerate(closes)]

    # 8 bars covering d0..d1; the close ON d1 differs from the d1−1 close.
    series = {
        "A": daily([100, 100, 100, 100, 100, 100, 105, 110]),
        "B": daily([50, 50, 50, 50, 50, 50, 52, 55]),
        "VOOG": daily([200, 200, 200, 200, 200, 200, 201, 204]),
        "SPY": daily([400] * 8),
    }
    decision = _decision_row(d0)
    session = _session_returning(decision, [])
    engine = AttributionEngine(data_service=_end_exclusive_data_service(series))

    report = await engine.compute_and_persist(session, branch_id=BRANCH_ID, branch_name="growth", as_of=d1)

    assert report is not None
    # Must measure to the d1 close (A: 110/100, B: 55/50), not d1−1 (105, 52).
    assert report.basket_return_conviction == pytest.approx(0.6 * 0.10 + 0.4 * 0.10)
    assert report.benchmark_return == pytest.approx(204 / 200 - 1)


async def test_all_price_fetches_fail_returns_none_without_persisting():
    """A full price-source outage must not overwrite a good row with zeros."""
    decision = _decision_row(date(2026, 6, 1))
    existing = MagicMock()
    session = _session_returning(decision, [], existing_report=existing)
    engine = AttributionEngine(data_service=_data_service({}))  # every fetch raises

    report = await engine.compute_and_persist(
        session, branch_id=BRANCH_ID, branch_name="growth", as_of=date(2026, 6, 8)
    )

    assert report is None
    session.add.assert_not_called()
    session.flush.assert_not_called()
    # upsert never ran: only decision + signals queries were executed
    assert session.execute.await_count == 2

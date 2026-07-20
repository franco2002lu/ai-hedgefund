"""read_portfolio_state hard-fails instead of sizing against a fictional NAV."""

import pytest

from app.modules.equities.service import read_portfolio_state


class _Pos:
    def __init__(self, symbol, qty, cost_basis):
        self.symbol = symbol
        self.long_quantity = qty
        self.long_cost_basis = cost_basis


class _Portfolio:
    def __init__(self, nav, positions):
        self.nav = nav
        self.positions = positions


class FakePS:
    def __init__(self, portfolio=None, raises=False):
        self._portfolio = portfolio
        self._raises = raises

    async def get_portfolio(self, branch_id):
        if self._raises:
            raise ConnectionError("db down")
        return self._portfolio


class FakeDataService:
    def __init__(self, prices):
        self._prices = prices

    async def get_current_price(self, symbol):
        return self._prices.get(symbol)


async def test_missing_portfolio_service_raises():
    with pytest.raises(RuntimeError, match="Portfolio service unavailable"):
        await read_portfolio_state(None, None, branch_id="b-1", branch_name="growth")


async def test_missing_portfolio_row_raises():
    with pytest.raises(RuntimeError, match="No portfolio row"):
        await read_portfolio_state(FakePS(portfolio=None), None, branch_id="b-1", branch_name="growth")


async def test_read_failure_raises_with_cause():
    with pytest.raises(RuntimeError, match="Portfolio read failed") as excinfo:
        await read_portfolio_state(FakePS(raises=True), None, branch_id="b-1", branch_name="growth")
    assert isinstance(excinfo.value.__cause__, ConnectionError)


async def test_non_positive_nav_raises():
    ps = FakePS(portfolio=_Portfolio(nav=0.0, positions=[]))
    with pytest.raises(RuntimeError, match="Non-positive NAV"):
        await read_portfolio_state(ps, None, branch_id="b-1", branch_name="growth")


async def test_empty_book_is_valid_first_run():
    ps = FakePS(portfolio=_Portfolio(nav=1_000_000.0, positions=[]))
    nav, weights, quantities = await read_portfolio_state(ps, None, branch_id="b-1", branch_name="growth")
    assert nav == 1_000_000.0
    assert weights == {} and quantities == {}


async def test_priced_position_weighted_at_market_value():
    ps = FakePS(portfolio=_Portfolio(nav=1_000_000.0, positions=[_Pos("AAPL", 100.0, 40_000.0)]))
    data = FakeDataService({"AAPL": 500.0})
    nav, weights, quantities = await read_portfolio_state(ps, data, branch_id="b-1", branch_name="growth")
    assert weights == {"AAPL": pytest.approx(0.05)}  # 100 * 500 / 1M
    assert quantities == {"AAPL": 100.0}


async def test_unpriced_position_falls_back_to_cost_basis_weight():
    ps = FakePS(portfolio=_Portfolio(nav=1_000_000.0, positions=[_Pos("AAPL", 100.0, 40_000.0)]))
    data = FakeDataService({})
    nav, weights, quantities = await read_portfolio_state(ps, data, branch_id="b-1", branch_name="growth")
    assert weights == {"AAPL": pytest.approx(0.04)}  # total cost basis / nav
    assert quantities == {"AAPL": 100.0}

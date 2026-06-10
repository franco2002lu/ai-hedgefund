"""Unit tests for the Portfolio Manager -- composite scoring, stock selection,
position sizing, and order generation."""

import pytest

from app.modules.equities.agents.portfolio_manager import PortfolioManager
from app.modules.equities.config import AgentsConfig, PortfolioConfig
from app.modules.equities.models import CompositeScore, StockSignal

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _make_signal(
    symbol="AAPL",
    analyst_type="fundamentals",
    bullish_score=7,
    confidence=8,
):
    return StockSignal(
        symbol=symbol,
        analyst_type=analyst_type,
        bullish_score=bullish_score,
        confidence=confidence,
        summary="test",
    )


def _make_signals_for_stock(symbol, news=(7, 8), fund=(8, 7), tech=(6, 6)):
    """Returns 3 signals for one stock. Tuple is (bullish_score, confidence)."""
    return [
        _make_signal(symbol, "news", news[0], news[1]),
        _make_signal(symbol, "fundamentals", fund[0], fund[1]),
        _make_signal(symbol, "technical", tech[0], tech[1]),
    ]


def _make_pm(weights=None, portfolio_config=None):
    agents_config = AgentsConfig()
    if weights:
        agents_config = AgentsConfig(
            weight_fundamentals=weights[0],
            weight_news=weights[1],
            weight_technical=weights[2],
        )
    pc = portfolio_config or PortfolioConfig()
    return PortfolioManager(agents_config=agents_config, portfolio_config=pc)


# ---------------------------------------------------------------------------
# TestCompositeScoreCalculation
# ---------------------------------------------------------------------------


class TestCompositeScoreCalculation:
    def test_formula_matches_doc(self):
        """Explicit weights fund=0.40, news=0.35, tech=0.25.
        AAPL: news=(7,8), fund=(8,7), tech=(6,6)
        composite_score  = 0.40*8 + 0.35*7 + 0.25*6 = 3.2+2.45+1.5 = 7.15
        composite_confidence = 0.40*7 + 0.35*8 + 0.25*6 = 2.8+2.8+1.5 = 7.1
        """
        pm = _make_pm(weights=(0.40, 0.35, 0.25))
        signals = _make_signals_for_stock("AAPL", news=(7, 8), fund=(8, 7), tech=(6, 6))
        scores = pm.compute_composite_scores(signals)

        assert len(scores) == 1
        s = scores[0]
        assert s.symbol == "AAPL"
        assert s.composite_score == pytest.approx(7.15, abs=1e-9)
        assert s.composite_confidence == pytest.approx(7.1, abs=1e-9)

    def test_conviction_is_score_times_confidence(self):
        pm = _make_pm()
        signals = _make_signals_for_stock("AAPL", news=(7, 8), fund=(8, 7), tech=(6, 6))
        scores = pm.compute_composite_scores(signals)

        s = scores[0]
        assert s.conviction == pytest.approx(s.composite_score * s.composite_confidence, abs=1e-9)

    def test_multiple_stocks_scored_independently(self):
        pm = _make_pm()
        signals = (
            _make_signals_for_stock("AAPL", news=(7, 8), fund=(8, 7), tech=(6, 6))
            + _make_signals_for_stock("MSFT", news=(9, 9), fund=(9, 9), tech=(9, 9))
            + _make_signals_for_stock("GOOG", news=(3, 3), fund=(3, 3), tech=(3, 3))
        )
        scores = pm.compute_composite_scores(signals)
        by_sym = {s.symbol: s for s in scores}

        assert len(by_sym) == 3
        assert by_sym["MSFT"].composite_score == pytest.approx(9.0)
        assert by_sym["MSFT"].composite_confidence == pytest.approx(9.0)
        assert by_sym["GOOG"].composite_score == pytest.approx(3.0)
        assert by_sym["GOOG"].composite_confidence == pytest.approx(3.0)

    def test_custom_weights(self):
        """Override weights to 0.5/0.3/0.2 (fund/news/tech)."""
        pm = _make_pm(weights=(0.5, 0.3, 0.2))
        signals = _make_signals_for_stock("AAPL", news=(7, 8), fund=(8, 7), tech=(6, 6))
        scores = pm.compute_composite_scores(signals)

        s = scores[0]
        # score = 0.5*8 + 0.3*7 + 0.2*6 = 4.0+2.1+1.2 = 7.3
        assert s.composite_score == pytest.approx(7.3)
        # confidence = 0.5*7 + 0.3*8 + 0.2*6 = 3.5+2.4+1.2 = 7.1
        assert s.composite_confidence == pytest.approx(7.1)

    def test_missing_analyst_produces_partial_score(self):
        """Only 2 of 3 signals for a stock -> partial weighted sum
        (missing analyst contributes 0)."""
        pm = _make_pm(weights=(0.40, 0.35, 0.25))
        signals = [
            _make_signal("AAPL", "news", 7, 8),
            _make_signal("AAPL", "fundamentals", 8, 7),
            # technical is missing
        ]
        scores = pm.compute_composite_scores(signals)

        s = scores[0]
        # score = 0.40*8 + 0.35*7 + 0.25*0 = 3.2+2.45+0 = 5.65
        assert s.composite_score == pytest.approx(5.65)


# ---------------------------------------------------------------------------
# TestStockSelection
# ---------------------------------------------------------------------------


class TestStockSelection:
    def _make_score(self, symbol, composite_score=7.0, conviction=50.0):
        return CompositeScore(
            symbol=symbol,
            composite_score=composite_score,
            composite_confidence=(conviction / composite_score if composite_score else 1.0),
            conviction=conviction,
        )

    def test_selects_top_n_by_conviction(self):
        """30 stocks, target=20 -> picks top 20 by conviction."""
        pm = _make_pm()
        scores = [self._make_score(f"S{i:02d}", conviction=100 - i) for i in range(30)]
        selected = pm.select_stocks(scores)

        assert len(selected) == 20
        assert selected[0].symbol == "S00"
        assert selected[-1].symbol == "S19"

    def test_enforces_max_holdings_guardrail(self):
        """35 pass threshold, max=30 -> takes top 30."""
        pc = PortfolioConfig(target_holdings=40, max_holdings=30, min_composite_score=1.0)
        pm = _make_pm(portfolio_config=pc)
        scores = [self._make_score(f"S{i:02d}", conviction=100 - i) for i in range(35)]
        selected = pm.select_stocks(scores)

        assert len(selected) == 30

    def test_filters_below_min_composite_score(self):
        """Some stocks below 4.0 excluded."""
        pm = _make_pm()
        scores = [
            self._make_score("HIGH", composite_score=8.0, conviction=60.0),
            self._make_score("MID", composite_score=5.0, conviction=40.0),
            self._make_score("LOW", composite_score=3.0, conviction=30.0),
        ]
        selected = pm.select_stocks(scores)
        symbols = {s.symbol for s in selected}

        assert "HIGH" in symbols
        assert "MID" in symbols
        assert "LOW" not in symbols

    def test_all_above_threshold_with_fewer_than_target(self):
        """Only 15 pass, target=20 -> returns all 15."""
        pm = _make_pm()
        scores = [self._make_score(f"S{i:02d}", conviction=50 + i) for i in range(15)]
        selected = pm.select_stocks(scores)

        assert len(selected) == 15

    def test_respects_min_holdings(self):
        """Only 8 stocks pass min_score; we return all 8
        (even below min_holdings=10)."""
        pc = PortfolioConfig(min_holdings=10, min_composite_score=5.0)
        pm = _make_pm(portfolio_config=pc)
        scores = [self._make_score(f"S{i:02d}", composite_score=6.0, conviction=40 + i) for i in range(8)]
        selected = pm.select_stocks(scores)

        assert len(selected) == 8


# ---------------------------------------------------------------------------
# TestPositionSizing
# ---------------------------------------------------------------------------


class TestPositionSizing:
    def _make_selected(self, n, conviction_fn=None):
        """Create n CompositeScore objects with configurable convictions."""
        results = []
        for i in range(n):
            conv = conviction_fn(i) if conviction_fn else 50.0
            results.append(
                CompositeScore(
                    symbol=f"S{i:02d}",
                    composite_score=7.0,
                    composite_confidence=conv / 7.0,
                    conviction=conv,
                )
            )
        return results

    def test_weights_sum_to_one(self):
        pm = _make_pm()
        selected = self._make_selected(20)
        sized = pm.size_positions(selected)

        total = sum(s.target_weight for s in sized)
        assert total == pytest.approx(1.0)

    def test_conviction_weighted_proportional(self):
        """Stock with 2x conviction gets ~2x weight."""
        pc = PortfolioConfig(max_position_weight=0.80)
        pm = _make_pm(portfolio_config=pc)
        selected = [
            CompositeScore(
                symbol="A",
                composite_score=7.0,
                composite_confidence=10.0,
                conviction=100.0,
            ),
            CompositeScore(
                symbol="B",
                composite_score=7.0,
                composite_confidence=5.0,
                conviction=50.0,
            ),
        ]
        sized = pm.size_positions(selected)
        by_sym = {s.symbol: s for s in sized}

        assert by_sym["A"].target_weight == pytest.approx(2.0 * by_sym["B"].target_weight, rel=0.01)

    def test_50_percent_cap_enforced(self):
        """One stock has extreme conviction; its weight is capped at 0.50."""
        pc = PortfolioConfig(max_position_weight=0.50)
        pm = _make_pm(portfolio_config=pc)
        selected = [
            CompositeScore(
                symbol="BIG",
                composite_score=10.0,
                composite_confidence=10.0,
                conviction=1000.0,
            ),
            CompositeScore(
                symbol="SMALL",
                composite_score=5.0,
                composite_confidence=1.0,
                conviction=5.0,
            ),
        ]
        sized = pm.size_positions(selected)
        by_sym = {s.symbol: s for s in sized}

        assert by_sym["BIG"].target_weight <= 0.50 + 1e-9

    def test_excess_redistributed_pro_rata(self):
        """After capping at 50%, remaining stocks get extra proportionally.
        Sum still == 1.0."""
        pc = PortfolioConfig(max_position_weight=0.50)
        pm = _make_pm(portfolio_config=pc)
        selected = [
            CompositeScore(
                symbol="BIG",
                composite_score=10.0,
                composite_confidence=10.0,
                conviction=900.0,
            ),
            CompositeScore(
                symbol="MED",
                composite_score=7.0,
                composite_confidence=5.0,
                conviction=50.0,
            ),
            CompositeScore(
                symbol="SML",
                composite_score=5.0,
                composite_confidence=5.0,
                conviction=50.0,
            ),
        ]
        sized = pm.size_positions(selected)

        total = sum(s.target_weight for s in sized)
        assert total == pytest.approx(1.0)

    def test_all_equal_conviction(self):
        """20 stocks with same conviction -> equal weights (0.05 each)."""
        pm = _make_pm()
        selected = self._make_selected(20, conviction_fn=lambda _: 50.0)
        sized = pm.size_positions(selected)

        for s in sized:
            assert s.target_weight == pytest.approx(0.05, abs=1e-9)

    def test_two_stocks_one_gets_capped(self):
        """2 stocks; one has 80% of total conviction -> capped at 50%,
        other gets 50%."""
        pc = PortfolioConfig(max_position_weight=0.50)
        pm = _make_pm(portfolio_config=pc)
        selected = [
            CompositeScore(
                symbol="BIG",
                composite_score=8.0,
                composite_confidence=10.0,
                conviction=80.0,
            ),
            CompositeScore(
                symbol="SML",
                composite_score=5.0,
                composite_confidence=4.0,
                conviction=20.0,
            ),
        ]
        sized = pm.size_positions(selected)
        by_sym = {s.symbol: s for s in sized}

        assert by_sym["BIG"].target_weight == pytest.approx(0.50, abs=1e-2)
        assert by_sym["SML"].target_weight == pytest.approx(0.50, abs=1e-2)
        total = sum(s.target_weight for s in sized)
        assert total == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# TestOrderGeneration
# ---------------------------------------------------------------------------


class TestOrderGeneration:
    def test_new_position_generates_buy(self):
        """Target has AAPL at 10%, current has no AAPL -> BUY.
        quantity = floor(0.10 * 1_000_000 / 150) = 666
        """
        pm = _make_pm()
        target = [
            CompositeScore(
                symbol="AAPL",
                composite_score=7.0,
                composite_confidence=7.0,
                conviction=49.0,
                target_weight=0.10,
            ),
        ]
        orders = pm.generate_orders(
            target=target,
            current_positions={},
            nav=1_000_000,
            prices={"AAPL": 150.0},
        )

        assert len(orders) == 1
        o = orders[0]
        assert o.symbol == "AAPL"
        assert o.side == "buy"
        assert o.quantity == pytest.approx(666.6667)
        assert o.reason == "new_position"

    def test_removed_position_generates_sell(self):
        """Current has MSFT at 10% weight, target does not have MSFT -> SELL.
        quantity = round(0.10 * 1_000_000 / 300, 4) = 333.3333
        """
        pm = _make_pm()
        orders = pm.generate_orders(
            target=[],
            current_positions={"MSFT": 0.10},
            nav=1_000_000,
            prices={"MSFT": 300.0},
        )

        assert len(orders) == 1
        o = orders[0]
        assert o.symbol == "MSFT"
        assert o.side == "sell"
        assert o.quantity == pytest.approx(333.3333)
        assert o.reason == "removed_position"

    def test_weight_increase_generates_buy(self):
        """Current AAPL at 5% weight, target at 10% -> BUY delta=5%.
        quantity = round(0.05 * 1_000_000 / 150, 4) = 333.3333
        """
        pm = _make_pm()
        target = [
            CompositeScore(
                symbol="AAPL",
                composite_score=7.0,
                composite_confidence=7.0,
                conviction=49.0,
                target_weight=0.10,
            ),
        ]
        orders = pm.generate_orders(
            target=target,
            current_positions={"AAPL": 0.05},
            nav=1_000_000,
            prices={"AAPL": 150.0},
        )

        assert len(orders) == 1
        o = orders[0]
        assert o.side == "buy"
        assert o.quantity == pytest.approx(333.3333)
        assert o.reason == "weight_adjustment"

    def test_weight_decrease_generates_sell(self):
        """Current AAPL at 15% weight, target at 10% -> SELL delta=5%.
        quantity = round(0.05 * 1_000_000 / 150, 4) = 333.3333
        """
        pm = _make_pm()
        target = [
            CompositeScore(
                symbol="AAPL",
                composite_score=7.0,
                composite_confidence=7.0,
                conviction=49.0,
                target_weight=0.10,
            ),
        ]
        orders = pm.generate_orders(
            target=target,
            current_positions={"AAPL": 0.15},
            nav=1_000_000,
            prices={"AAPL": 150.0},
        )

        assert len(orders) == 1
        o = orders[0]
        assert o.side == "sell"
        assert o.quantity == pytest.approx(333.3333)
        assert o.reason == "weight_adjustment"

    def test_min_trade_threshold_skips_small_adjustment(self):
        """Delta < 2% of NAV -> no order generated
        (default min_rebalance_threshold=0.02)."""
        pm = _make_pm()
        target = [
            CompositeScore(
                symbol="AAPL",
                composite_score=7.0,
                composite_confidence=7.0,
                conviction=49.0,
                target_weight=0.10,
            ),
        ]
        orders = pm.generate_orders(
            target=target,
            current_positions={"AAPL": 0.091},  # delta = 0.009 < 0.02
            nav=1_000_000,
            prices={"AAPL": 150.0},
        )

        assert len(orders) == 0

    def test_correct_share_calculation(self):
        """target_weight=0.10, NAV=1_000_000, price=150 -> quantity=666.6667."""
        pm = _make_pm()
        target = [
            CompositeScore(
                symbol="AAPL",
                composite_score=7.0,
                composite_confidence=7.0,
                conviction=49.0,
                target_weight=0.10,
            ),
        ]
        orders = pm.generate_orders(
            target=target,
            current_positions={},
            nav=1_000_000,
            prices={"AAPL": 150.0},
        )

        assert orders[0].quantity == pytest.approx(round(0.10 * 1_000_000 / 150.0, 4))
        assert orders[0].quantity == pytest.approx(666.6667)

    def test_empty_target_sells_everything(self):
        """No target positions, current has 3 positions -> 3 SELL orders."""
        pm = _make_pm()
        orders = pm.generate_orders(
            target=[],
            current_positions={
                "AAPL": 0.10,
                "MSFT": 0.15,
                "GOOG": 0.05,
            },
            nav=1_000_000,
            prices={"AAPL": 150.0, "MSFT": 300.0, "GOOG": 100.0},
        )

        assert len(orders) == 3
        for o in orders:
            assert o.side == "sell"
            assert o.reason == "removed_position"

    def test_empty_current_buys_everything(self):
        """No current positions, target has 3 -> 3 BUY orders."""
        pm = _make_pm()
        target = [
            CompositeScore(
                symbol="AAPL",
                composite_score=7.0,
                composite_confidence=7.0,
                conviction=49.0,
                target_weight=0.10,
            ),
            CompositeScore(
                symbol="MSFT",
                composite_score=7.0,
                composite_confidence=7.0,
                conviction=49.0,
                target_weight=0.15,
            ),
            CompositeScore(
                symbol="GOOG",
                composite_score=7.0,
                composite_confidence=7.0,
                conviction=49.0,
                target_weight=0.05,
            ),
        ]
        orders = pm.generate_orders(
            target=target,
            current_positions={},
            nav=1_000_000,
            prices={"AAPL": 150.0, "MSFT": 300.0, "GOOG": 100.0},
        )

        assert len(orders) == 3
        for o in orders:
            assert o.side == "buy"
            assert o.reason == "new_position"

    def test_no_change_no_orders(self):
        """Current matches target exactly -> no orders."""
        pm = _make_pm()
        target = [
            CompositeScore(
                symbol="AAPL",
                composite_score=7.0,
                composite_confidence=7.0,
                conviction=49.0,
                target_weight=0.10,
            ),
        ]
        orders = pm.generate_orders(
            target=target,
            current_positions={"AAPL": 0.10},
            nav=1_000_000,
            prices={"AAPL": 150.0},
        )

        assert len(orders) == 0

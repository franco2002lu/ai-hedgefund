"""tests/unit/equities/test_attribution.py"""

from datetime import date

import pytest

from app.modules.equities.attribution import (
    AttributionReport,
    compute_report,
    resolve_weights,
    spearman,
)

D0 = date(2026, 6, 1)
D1 = date(2026, 6, 8)


def _series(*closes, start=D0):
    """Build [(date, close)] with consecutive days starting at `start`."""
    from datetime import timedelta

    return [(start + timedelta(days=i), c) for i, c in enumerate(closes)]


class TestSpearman:
    def test_perfect_positive(self):
        assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)

    def test_perfect_negative(self):
        assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)

    def test_ties_average_ranks(self):
        # xs has ties; known spearman for ([1,2,2,4],[1,2,3,4]) = 0.9486832...
        assert spearman([1, 2, 2, 4], [1, 2, 3, 4]) == pytest.approx(0.9487, abs=1e-4)

    def test_constant_input_returns_none(self):
        assert spearman([5, 5, 5], [1, 2, 3]) is None


class TestResolveWeights:
    def test_uses_target_holdings_when_nonzero(self):
        w = resolve_weights(
            target_holdings={"A": 0.6, "B": 0.4},
            composite_scores={"A": {"score": 9, "confidence": 9}},
            buy_symbols=["A", "B"],
        )
        assert w == {"A": 0.6, "B": 0.4}

    def test_falls_back_to_conviction_when_targets_zero(self):
        w = resolve_weights(
            target_holdings={"A": 0, "B": 0},
            composite_scores={
                "A": {"score": 8.0, "confidence": 5.0},  # conviction 40
                "B": {"score": 6.0, "confidence": 10.0},  # conviction 60
            },
            buy_symbols=["A", "B"],
        )
        assert w["A"] == pytest.approx(0.4)
        assert w["B"] == pytest.approx(0.6)

    def test_empty_when_no_data(self):
        assert resolve_weights(target_holdings={}, composite_scores={}, buy_symbols=[]) == {}


class TestComputeReport:
    def test_basket_and_benchmark_returns(self):
        prices = {
            "A": _series(100, 101, 102, 103, 104, 105, 106, 110),  # +10%
            "B": _series(50, 50, 50, 50, 50, 50, 50, 45),  # -10%
            "VOOG": _series(200, 200, 200, 200, 200, 200, 200, 204),  # +2%
            "SPY": _series(400, 400, 400, 400, 400, 400, 400, 396),  # -1%
        }
        report = compute_report(
            branch_name="growth",
            decision_date=D0,
            as_of=D1,
            weights={"A": 0.75, "B": 0.25},
            signals=[],
            prices=prices,
            benchmark_symbol="VOOG",
        )
        assert isinstance(report, AttributionReport)
        assert report.basket_return_conviction == pytest.approx(0.75 * 0.10 + 0.25 * -0.10)
        assert report.basket_return_equal == pytest.approx((0.10 - 0.10) / 2)
        assert report.benchmark_return == pytest.approx(0.02)
        assert report.spy_return == pytest.approx(-0.01)
        assert report.n_holdings == 2
        assert report.n_holdings_priced == 2

    def test_missing_price_symbol_dropped_and_renormalized(self):
        prices = {
            "A": _series(100, 110),  # +10%; B has no prices at all
            "VOOG": _series(200, 202),
            "SPY": _series(400, 404),
        }
        report = compute_report(
            branch_name="growth",
            decision_date=D0,
            as_of=D1,
            weights={"A": 0.5, "B": 0.5},
            signals=[],
            prices=prices,
            benchmark_symbol="VOOG",
        )
        assert report.basket_return_conviction == pytest.approx(0.10)
        assert report.n_holdings == 2
        assert report.n_holdings_priced == 1

    def test_analyst_ics_computed_per_type_with_min_n(self):
        # 5 symbols, fundamentals scores perfectly rank-aligned with returns
        prices = {f"S{i}": _series(100, 100 + i) for i in range(5)}  # returns 0..4%
        prices["VOOG"] = _series(200, 200)
        prices["SPY"] = _series(400, 400)
        signals = [{"symbol": f"S{i}", "analyst_type": "fundamentals", "bullish_score": i + 1} for i in range(5)] + [
            # only 2 news signals -> below min_n of 5 -> None
            {"symbol": "S0", "analyst_type": "news", "bullish_score": 9},
            {"symbol": "S1", "analyst_type": "news", "bullish_score": 1},
        ]
        report = compute_report(
            branch_name="growth",
            decision_date=D0,
            as_of=D1,
            weights={f"S{i}": 0.2 for i in range(5)},
            signals=signals,
            prices=prices,
            benchmark_symbol="VOOG",
        )
        assert report.analyst_ics["fundamentals"] == pytest.approx(1.0)
        assert report.analyst_ics["news"] is None


class TestCompositeIC:
    def test_composite_ic_computed_from_composite_scores(self):
        prices = {
            s: _series(100.0, px) for s, px in [("A", 110.0), ("B", 108.0), ("C", 106.0), ("D", 104.0), ("E", 102.0)]
        }
        composite_scores = {
            "A": {"score": 9.0, "confidence": 9.0},
            "B": {"score": 8.0, "confidence": 8.0},
            "C": {"score": 7.0, "confidence": 7.0},
            "D": {"score": 6.0, "confidence": 6.0},
            "E": {"score": 5.0, "confidence": 5.0},
        }
        report = compute_report(
            branch_name="growth",
            decision_date=D0,
            as_of=D1,
            weights={"A": 1.0},
            signals=[],
            prices=prices,
            benchmark_symbol="VOOG",
            composite_scores=composite_scores,
        )
        # conviction 81..25 perfectly rank-aligned with returns 10%..2%
        assert report.analyst_ics["composite"] == pytest.approx(1.0)

    def test_composite_ic_requires_min_samples(self):
        prices = {s: _series(100.0, 101.0) for s in ("A", "B")}
        report = compute_report(
            branch_name="growth",
            decision_date=D0,
            as_of=D1,
            weights={"A": 1.0},
            signals=[],
            prices=prices,
            benchmark_symbol="VOOG",
            composite_scores={"A": {"score": 9, "confidence": 9}, "B": {"score": 5, "confidence": 5}},
        )
        assert report.analyst_ics["composite"] is None

    def test_no_composite_scores_no_composite_key(self):
        report = compute_report(
            branch_name="growth",
            decision_date=D0,
            as_of=D1,
            weights={},
            signals=[],
            prices={},
            benchmark_symbol="VOOG",
        )
        assert "composite" not in report.analyst_ics

    def test_composite_scores_do_not_clobber_basket_return(self):
        """Regression: the composite-IC loop must not shadow the conviction
        basket return (a bug here once persisted score*confidence as the
        basket return)."""
        prices = {
            s: _series(100.0, px) for s, px in [("A", 110.0), ("B", 108.0), ("C", 106.0), ("D", 104.0), ("E", 102.0)]
        }
        composite_scores = {s: {"score": 9.0, "confidence": 9.0} for s in "ABCDE"}
        report = compute_report(
            branch_name="growth",
            decision_date=D0,
            as_of=D1,
            weights={"A": 1.0},
            signals=[],
            prices=prices,
            benchmark_symbol="VOOG",
            composite_scores=composite_scores,
        )
        assert report.basket_return_conviction == pytest.approx(0.10)  # A: 100 -> 110
        assert report.basket_return_conviction < 1.0  # never score*confidence

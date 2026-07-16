"""Adaptive analyst weights: pure policy math and the DB loader."""

import math
import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.modules.equities.adaptive_weights import (
    ANALYSTS,
    alert_streaks,
    compute_adaptive_weights,
    ewma,
    project_weights,
    resolve_analyst_weights,
    static_weights,
    tilt_targets,
)
from app.modules.equities.config import AgentsConfig

STATIC = {"fundamentals": 0.60, "news": 0.20, "technical": 0.20}


def _cfg(**adaptive_overrides) -> AgentsConfig:
    base = AgentsConfig()
    adaptive = base.adaptive.model_copy(update=adaptive_overrides)
    return base.model_copy(update={"adaptive": adaptive})


class TestEwma:
    def test_single_value_is_itself(self):
        assert ewma([0.3], half_life=4.0) == 0.3

    def test_known_answer_two_values(self):
        # lam = 0.5 ** (1/1) = 0.5; freshest first: (1.0*0.4 + 0.5*0.2)/1.5
        assert math.isclose(ewma([0.4, 0.2], half_life=1.0), 0.5 / 1.5)

    def test_none_skipped_but_lag_decay_kept(self):
        lam = 0.5 ** (1 / 4.0)
        # freshest is None -> only value contributes, EWIC is that value
        assert math.isclose(ewma([None, 0.4], half_life=4.0), 0.4)
        # mixed: lag-0 value keeps weight 1, lag-2 value decays by lam**2
        expected = (0.4 + lam**2 * 0.1) / (1 + lam**2)
        assert math.isclose(ewma([0.4, None, 0.1], half_life=4.0), expected)

    def test_all_none_or_empty(self):
        assert ewma([], half_life=4.0) is None
        assert ewma([None, None], half_life=4.0) is None


class TestTiltTargets:
    def test_zero_ewic_reproduces_static(self):
        t = tilt_targets({a: 0.0 for a in ANALYSTS}, STATIC, tau=0.4)
        for a in ANALYSTS:
            assert math.isclose(t[a], STATIC[a])

    def test_known_answer(self):
        ewics = {"fundamentals": 0.4, "news": 0.0, "technical": -0.4}
        raw = {a: STATIC[a] * math.exp(ewics[a] / 0.4) for a in ANALYSTS}
        total = sum(raw.values())
        t = tilt_targets(ewics, STATIC, tau=0.4)
        for a in ANALYSTS:
            assert math.isclose(t[a], raw[a] / total)
        assert math.isclose(sum(t.values()), 1.0)


class TestProjectWeights:
    def test_within_bounds_passthrough_and_exact_sum(self):
        prev = dict(STATIC)
        target = {"fundamentals": 0.62, "news": 0.21, "technical": 0.17}
        w = project_weights(target, prev, floor=0.10, max_shift=0.05)
        assert math.isclose(sum(w.values()), 1.0)
        for a in ANALYSTS:
            assert abs(w[a] - prev[a]) <= 0.05 + 1e-9
            assert w[a] >= 0.10 - 1e-9

    def test_shift_cap_binds_and_deficit_redistributes(self):
        prev = dict(STATIC)
        # target pulls fund up hard; fund capped at 0.65, others absorb
        target = {"fundamentals": 0.80, "news": 0.10, "technical": 0.10}
        w = project_weights(target, prev, floor=0.10, max_shift=0.05)
        assert math.isclose(w["fundamentals"], 0.65)
        assert math.isclose(sum(w.values()), 1.0)
        for a in ("news", "technical"):
            assert prev[a] - 0.05 - 1e-9 <= w[a] <= prev[a] + 0.05 + 1e-9

    def test_floor_binds(self):
        prev = {"fundamentals": 0.75, "news": 0.13, "technical": 0.12}
        target = {"fundamentals": 0.85, "news": 0.05, "technical": 0.10}
        w = project_weights(target, prev, floor=0.10, max_shift=0.05)
        assert w["news"] >= 0.10 - 1e-9
        assert math.isclose(sum(w.values()), 1.0)

    def test_infeasible_bounds_fall_back_to_floor_projection(self):
        # floor raised above what prev+shift allows -> one-time jump mode
        prev = {"fundamentals": 0.80, "news": 0.10, "technical": 0.10}
        target = {"fundamentals": 0.50, "news": 0.25, "technical": 0.25}
        w = project_weights(target, prev, floor=0.30, max_shift=0.05)
        assert math.isclose(sum(w.values()), 1.0)
        for a in ANALYSTS:
            assert w[a] >= 0.30 - 1e-9

    def test_output_rounded_and_sums_exactly_one(self):
        prev = dict(STATIC)
        target = {"fundamentals": 1 / 3, "news": 1 / 3, "technical": 1 / 3}
        w = project_weights(target, prev, floor=0.10, max_shift=0.05)
        assert sum(w.values()) == 1.0  # exact after rounding policy
        for v in w.values():
            assert round(v, 6) == v


class TestAlertStreaks:
    def test_streak_counts_only_trustworthy_windows(self):
        # 9 obs all negative, min_history 6 -> evaluations at lags 0..3 -> streak 4
        ics = {"fundamentals": [0.1] * 9, "news": [0.1] * 9, "technical": [-0.2] * 9}
        alerts = alert_streaks(ics, half_life=4.0, min_history=6, streak_threshold=4)
        assert len(alerts) == 1
        a = alerts[0]
        assert a["analyst"] == "technical"
        assert a["streak"] == 4
        assert a["ewic"] < 0

    def test_positive_recent_week_breaks_streak(self):
        # freshest window EWIC > 0 -> no alert
        ics = {
            "fundamentals": [0.1] * 9,
            "news": [0.1] * 9,
            "technical": [0.9, -0.2, -0.2, -0.2, -0.2, -0.2, -0.2, -0.2, -0.2],
        }
        assert alert_streaks(ics, half_life=4.0, min_history=6, streak_threshold=4) == []

    def test_below_min_history_never_alerts(self):
        ics = {"fundamentals": [-0.5] * 4, "news": [-0.5] * 4, "technical": [-0.5] * 4}
        assert alert_streaks(ics, half_life=4.0, min_history=6, streak_threshold=4) == []

    def test_streak_below_threshold_not_reported(self):
        # only 7 obs -> evaluations at lags 0..1 -> streak 2 < 4
        ics = {"fundamentals": [0.1] * 7, "news": [0.1] * 7, "technical": [-0.2] * 7}
        assert alert_streaks(ics, half_life=4.0, min_history=6, streak_threshold=4) == []


class TestComputeAdaptiveWeights:
    def test_insufficient_history_falls_back_static(self):
        ics = {a: [0.2] * 5 for a in ANALYSTS}  # 5 < 6
        report = compute_adaptive_weights(ic_series=ics, prev_weights=dict(STATIC), agents_config=_cfg())
        assert report.mode == "static"
        assert report.reason == "insufficient_history"
        assert report.weights == static_weights(_cfg())
        assert report.valid_weeks == {a: 5 for a in ANALYSTS}

    def test_none_ics_do_not_count_toward_history(self):
        ics = {a: [0.2, None, 0.2, None, 0.2, 0.2, 0.2] for a in ANALYSTS}  # 5 valid
        report = compute_adaptive_weights(ic_series=ics, prev_weights=dict(STATIC), agents_config=_cfg())
        assert report.reason == "insufficient_history"

    def test_adaptive_moves_toward_evidence_within_cap(self):
        # 10 obs so the technical <=0 streak has 5 trustworthy windows (>= threshold 4)
        ics = {
            "fundamentals": [0.4] * 10,
            "news": [0.0] * 10,
            "technical": [-0.4] * 10,
        }
        report = compute_adaptive_weights(ic_series=ics, prev_weights=dict(STATIC), agents_config=_cfg())
        assert report.mode == "adaptive"
        assert report.reason == "ok"
        w = report.weights
        # Known answer: targets clip to (0.65, 0.15, 0.15); the 0.05 deficit
        # redistributes to news/tech's equal headroom -> 0.175 each.
        assert math.isclose(w["fundamentals"], 0.65)  # +cap binds
        assert math.isclose(w["news"], 0.175)
        assert math.isclose(w["technical"], 0.175)
        assert math.isclose(sum(w.values()), 1.0)
        assert report.ewics["fundamentals"] > 0.39
        # constant negative series alerts
        assert any(a["analyst"] == "technical" for a in report.alerts)

    def test_prev_weights_anchor_progression(self):
        ics = {"fundamentals": [0.4] * 8, "news": [0.0] * 8, "technical": [-0.4] * 8}
        prev = {"fundamentals": 0.65, "news": 0.188253, "technical": 0.161747}
        report = compute_adaptive_weights(ic_series=ics, prev_weights=prev, agents_config=_cfg())
        assert report.weights["fundamentals"] <= 0.70 + 1e-9  # cap from new anchor


class TestReplayRealHistory:
    """Pin the policy against the real prod IC history (2026-05-11..2026-07-06
    decisions, freshest first). Guards against silent policy drift."""

    GROWTH = {
        "fundamentals": [0.3072, 0.2872, -0.1982, 0.0380, 0.0614, -0.2961, 0.2524, -0.1022, 0.2812],
        "news": [0.1563, -0.1538, -0.1982, 0.4369, -0.1806, -0.1373, 0.2259, -0.1098, -0.1150],
        "technical": [-0.0340, -0.5223, -0.3308, 0.7035, 0.0269, -0.0915, -0.0149, -0.0275, -0.2418],
    }

    def test_growth_first_adaptive_week_from_static(self):
        report = compute_adaptive_weights(
            ic_series=self.GROWTH, prev_weights=static_weights(_cfg()), agents_config=_cfg()
        )
        assert report.mode == "adaptive"
        w = report.weights
        # fundamentals EWIC ~ +0.09 -> capped one-week move up to 0.65
        assert math.isclose(w["fundamentals"], 0.65)
        # news EWIC ~ 0, technical ~ -0.08: both give up weight, tech more
        assert w["technical"] < w["news"] < 0.20 + 1e-9
        assert math.isclose(sum(w.values()), 1.0)
        assert min(report.valid_weeks.values()) == 9


BRANCH_ID = str(uuid.uuid4())


def _report_row(decision_date: date, ics: dict):
    row = MagicMock()
    row.decision_date = decision_date
    row.analyst_ics = ics
    return row


def _decision_row_with_weights(weights_payload):
    row = MagicMock()
    row.analyst_weights = weights_payload
    return row


def _loader_session(report_rows, decision_row):
    """Mock AsyncSession: first execute() returns attribution rows, second the
    latest decision row."""
    session = MagicMock()
    r1 = MagicMock()
    r1.scalars.return_value.all.return_value = report_rows
    r2 = MagicMock()
    r2.scalar_one_or_none.return_value = decision_row
    session.execute = AsyncMock(side_effect=[r1, r2])
    return session


class TestResolveAnalystWeights:
    async def test_disabled_short_circuits(self):
        cfg = _cfg(enabled=False)
        report = await resolve_analyst_weights(session=MagicMock(), branch_id=BRANCH_ID, agents_config=cfg)
        assert (report.mode, report.reason) == ("static", "disabled")

    async def test_no_session_short_circuits(self):
        report = await resolve_analyst_weights(session=None, branch_id=BRANCH_ID, agents_config=_cfg())
        assert (report.mode, report.reason) == ("static", "no_session")

    async def test_query_error_falls_back(self):
        session = MagicMock()
        session.execute = AsyncMock(side_effect=RuntimeError("db down"))
        report = await resolve_analyst_weights(session=session, branch_id=BRANCH_ID, agents_config=_cfg())
        assert (report.mode, report.reason) == ("static", "error")

    async def test_adaptive_path_reads_reports_and_prev_weights(self):
        rows = [
            _report_row(
                date(2026, 7, 6) - timedelta(weeks=i),
                {"fundamentals": 0.4, "news": 0.0, "technical": -0.4, "composite": 0.9},
            )
            for i in range(8)
        ]
        prev = {"weights": {"fundamentals": 0.65, "news": 0.19, "technical": 0.16}, "mode": "adaptive"}
        session = _loader_session(rows, _decision_row_with_weights(prev))
        report = await resolve_analyst_weights(session=session, branch_id=BRANCH_ID, agents_config=_cfg())
        assert report.mode == "adaptive"
        # anchored to prev weights: fund can reach at most 0.70
        assert report.weights["fundamentals"] <= 0.70 + 1e-9
        assert report.weights["fundamentals"] > 0.65  # still pushing up
        # the "composite" key must be ignored
        assert set(report.ewics) == set(ANALYSTS)

    async def test_missing_prev_weights_anchor_to_static(self):
        rows = [_report_row(date(2026, 7, 6), {"fundamentals": 0.4, "news": 0.0, "technical": -0.4}) for _ in range(8)]
        session = _loader_session(rows, _decision_row_with_weights(None))
        report = await resolve_analyst_weights(session=session, branch_id=BRANCH_ID, agents_config=_cfg())
        assert report.weights["fundamentals"] <= 0.65 + 1e-9

    async def test_missing_ic_keys_treated_as_none(self):
        rows = [_report_row(date(2026, 7, 6), {"fundamentals": 0.4}) for _ in range(8)]
        session = _loader_session(rows, None)
        report = await resolve_analyst_weights(session=session, branch_id=BRANCH_ID, agents_config=_cfg())
        assert (report.mode, report.reason) == ("static", "insufficient_history")
        assert report.valid_weeks["news"] == 0


class TestProjectWeightsEmptyInterval:
    def test_raised_floor_with_empty_component_interval_jumps_to_floor(self):
        """Regression: floor raised above prev+shift for one component makes
        that interval empty while the sums still look feasible; must take the
        one-time-jump path, never emit a weight below the floor."""
        prev = {"fundamentals": 0.70, "news": 0.20, "technical": 0.10}
        target = {"fundamentals": 0.60, "news": 0.24, "technical": 0.16}
        w = project_weights(target, prev, floor=0.16, max_shift=0.05)
        assert math.isclose(sum(w.values()), 1.0)
        for a in ANALYSTS:
            assert w[a] >= 0.16 - 1e-9


class TestResolvePrevWeightsSanity:
    async def test_non_normalized_prev_weights_fall_back_to_static_anchor(self):
        rows = [_report_row(date(2026, 7, 6), {"fundamentals": 0.4, "news": 0.0, "technical": -0.4}) for _ in range(8)]
        corrupt = {"weights": {"fundamentals": 0.9, "news": 0.9, "technical": 0.9}}
        session = _loader_session(rows, _decision_row_with_weights(corrupt))
        report = await resolve_analyst_weights(session=session, branch_id=BRANCH_ID, agents_config=_cfg())
        # anchored to STATIC (0.60), not the corrupt 0.9s: fund capped at 0.65
        assert report.weights["fundamentals"] <= 0.65 + 1e-9

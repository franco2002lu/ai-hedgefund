"""Post-hoc attribution of weekly portfolio decisions (Phase D).

Pure computation lives in compute_report(); DB/price orchestration is in
AttributionEngine (added in a later task). Spearman is implemented locally to
avoid a scipy dependency.
"""

from __future__ import annotations

import logging
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.db.models import AgentSignalModel, AttributionReportModel, PortfolioDecisionModel

logger = logging.getLogger(__name__)

MIN_IC_SAMPLES = 5
BENCHMARK_MAP = {"growth": "VOOG", "value": "VOOV"}


@dataclass(frozen=True)
class AttributionReport:
    branch_name: str
    decision_date: date
    as_of_date: date
    basket_return_conviction: float
    basket_return_equal: float
    benchmark_return: float | None
    benchmark_symbol: str
    spy_return: float | None
    analyst_ics: dict[str, float | None] = field(default_factory=dict)
    n_holdings: int = 0
    n_holdings_priced: int = 0


def _ranks(xs: list[float]) -> list[float]:
    """Average ranks (1-based) with ties sharing the mean rank."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation; None if either input is constant or n < 2."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    rx, ry = _ranks(list(xs)), _ranks(list(ys))
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return None
    return cov / (vx**0.5 * vy**0.5)


def resolve_weights(
    *,
    target_holdings: dict,
    composite_scores: dict,
    buy_symbols: list[str],
) -> dict[str, float]:
    """Decision weights: prefer stored target_holdings; reconstruct from
    conviction (score x confidence over buy symbols) when targets are all zero
    (historical rows predating commit d5123bb)."""
    nonzero = {s: float(w) for s, w in target_holdings.items() if float(w) > 0}
    if nonzero:
        return nonzero
    conv = {}
    for sym in buy_symbols:
        cs = composite_scores.get(sym)
        if cs:
            conv[sym] = float(cs.get("score", 0)) * float(cs.get("confidence", 0))
    total = sum(conv.values())
    if total <= 0:
        return {}
    return {s: c / total for s, c in conv.items()}


def _window_return(series: list[tuple[date, float]], d0: date, d1: date) -> float | None:
    """Return from first close on/after d0 to last close on/before d1.

    Precondition: series must be sorted ascending by date.
    """
    on_or_after = [c for d, c in series if d >= d0]
    in_window = [c for d, c in series if d0 <= d <= d1]
    if not on_or_after or not in_window:
        return None
    first, last = on_or_after[0], in_window[-1]
    if first <= 0:
        return None
    return last / first - 1


def compute_report(
    *,
    branch_name: str,
    decision_date: date,
    as_of: date,
    weights: dict[str, float],
    signals: list[dict],
    prices: dict[str, list[tuple[date, float]]],
    benchmark_symbol: str,
    composite_scores: dict | None = None,
) -> AttributionReport:
    returns: dict[str, float] = {}
    for sym in weights:
        r = _window_return(prices.get(sym, []), decision_date, as_of)
        if r is not None:
            returns[sym] = r

    priced_w = {s: weights[s] for s in returns}
    wsum = sum(priced_w.values())
    conviction = sum(priced_w[s] * returns[s] for s in returns) / wsum if wsum > 0 else 0.0
    equal = sum(returns.values()) / len(returns) if returns else 0.0

    analyst_ics: dict[str, float | None] = {}
    by_type: dict[str, list[tuple[float, float]]] = {}
    for sig in signals:
        r = _window_return(prices.get(sig["symbol"], []), decision_date, as_of)
        if r is not None:
            by_type.setdefault(sig["analyst_type"], []).append((float(sig["bullish_score"]), r))
    for a_type, pairs in by_type.items():
        if len(pairs) < MIN_IC_SAMPLES:
            analyst_ics[a_type] = None
        else:
            analyst_ics[a_type] = spearman([p[0] for p in pairs], [p[1] for p in pairs])

    # Composite conviction IC (measurement for the adaptive-weights loop —
    # never read back by the weights policy, which uses the analyst keys only).
    if composite_scores:
        pairs = []
        for sym, cs in composite_scores.items():
            r = _window_return(prices.get(sym, []), decision_date, as_of)
            if r is None or not cs:
                continue
            comp_conviction = float(cs.get("score", 0)) * float(cs.get("confidence", 0))
            pairs.append((comp_conviction, r))
        if len(pairs) < MIN_IC_SAMPLES:
            analyst_ics["composite"] = None
        else:
            analyst_ics["composite"] = spearman([p[0] for p in pairs], [p[1] for p in pairs])

    return AttributionReport(
        branch_name=branch_name,
        decision_date=decision_date,
        as_of_date=as_of,
        basket_return_conviction=conviction,
        basket_return_equal=equal,
        benchmark_return=_window_return(prices.get(benchmark_symbol, []), decision_date, as_of),
        benchmark_symbol=benchmark_symbol,
        spy_return=_window_return(prices.get("SPY", []), decision_date, as_of),
        analyst_ics=analyst_ics,
        n_holdings=len(weights),
        n_holdings_priced=len(returns),
    )


# ============================================================
# AttributionEngine — DB orchestration + price fetch + upsert
# ============================================================

MAX_DECISION_AGE_DAYS = 14
# Window deliberately starts at the decision date: _window_return takes the first close on/after it.
_PRICE_LOOKBACK_PAD_DAYS = 0


class AttributionEngine:
    """Loads the prior decision + signals, fetches prices, computes and persists."""

    def __init__(self, data_service) -> None:
        self.data_service = data_service

    async def compute_and_persist(
        self, session, *, branch_id: str, branch_name: str, as_of: date
    ) -> AttributionReport | None:
        bid = _uuid.UUID(branch_id) if isinstance(branch_id, str) else branch_id

        stmt = (
            select(PortfolioDecisionModel)
            .where(
                PortfolioDecisionModel.branch_id == bid,
                PortfolioDecisionModel.decided_at < datetime(as_of.year, as_of.month, as_of.day),
            )
            .order_by(PortfolioDecisionModel.decided_at.desc())
            .limit(1)
        )
        decision = (await session.execute(stmt)).scalar_one_or_none()
        if decision is None:
            logger.info("Attribution: no prior decision for %s", branch_name)
            return None
        decision_date = decision.decided_at.date()
        if (as_of - decision_date).days > MAX_DECISION_AGE_DAYS:
            logger.info(
                "Attribution: latest decision %s is stale (> %d days) — skipping",
                decision_date,
                MAX_DECISION_AGE_DAYS,
            )
            return None

        sig_stmt = select(AgentSignalModel).where(AgentSignalModel.screening_run_id == decision.screening_run_id)
        signal_rows = (await session.execute(sig_stmt)).scalars().all()
        signals = [
            {"symbol": r.symbol, "analyst_type": r.analyst_type, "bullish_score": r.bullish_score} for r in signal_rows
        ]

        buy_symbols = [o["symbol"] for o in (decision.orders_generated or []) if o.get("side") == "buy"]
        weights = resolve_weights(
            target_holdings=decision.target_holdings or {},
            composite_scores=decision.composite_scores or {},
            buy_symbols=buy_symbols,
        )

        benchmark = BENCHMARK_MAP["growth" if "growth" in branch_name.lower() else "value"]
        composite_scores = decision.composite_scores or {}
        symbols = set(weights) | {s["symbol"] for s in signals} | set(composite_scores) | {benchmark, "SPY"}
        prices = await self._fetch_prices(sorted(symbols), decision_date, as_of)

        report = compute_report(
            branch_name=branch_name,
            decision_date=decision_date,
            as_of=as_of,
            weights=weights,
            signals=signals,
            prices=prices,
            benchmark_symbol=benchmark,
            composite_scores=composite_scores,
        )
        if weights and report.n_holdings_priced == 0:
            logger.warning(
                "Attribution: no holdings priced for %s (decision %s) — refusing to persist a zeroed report",
                branch_name,
                decision_date,
            )
            return None
        await self._upsert(session, bid, report)
        return report

    async def _fetch_prices(self, symbols: list[str], d0: date, d1: date) -> dict[str, list[tuple[date, float]]]:
        out: dict[str, list[tuple[date, float]]] = {}
        start = d0 - timedelta(days=_PRICE_LOOKBACK_PAD_DAYS)
        # yfinance end dates are exclusive — pad by one day so the close ON d1
        # is included; _window_return clips at <= d1 so the pad can't leak.
        end = d1 + timedelta(days=1)
        for sym in symbols:
            try:
                result = await self.data_service.get_prices(sym, start, end)
                series = []
                for bar in result.get("bars", []):
                    ts = bar["timestamp"]
                    # Check datetime before date: datetime is a subclass of date.
                    if isinstance(ts, str):
                        d = date.fromisoformat(ts[:10])
                    elif isinstance(ts, datetime):
                        d = ts.date()
                    else:
                        d = ts  # already a date
                    series.append((d, float(bar["close"])))
                out[sym] = sorted(series)
            except Exception:
                logger.warning("Attribution: price fetch failed for %s", sym, exc_info=True)
        return out

    async def _upsert(self, session, branch_id, report: AttributionReport) -> None:
        stmt = select(AttributionReportModel).where(
            AttributionReportModel.branch_id == branch_id,
            AttributionReportModel.decision_date == report.decision_date,
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        values = {
            "branch_name": report.branch_name,
            "as_of_date": report.as_of_date,
            "basket_return_conviction": report.basket_return_conviction,
            "basket_return_equal": report.basket_return_equal,
            "benchmark_return": report.benchmark_return,
            "benchmark_symbol": report.benchmark_symbol,
            "spy_return": report.spy_return,
            "analyst_ics": report.analyst_ics,
            "n_holdings": report.n_holdings,
            "n_holdings_priced": report.n_holdings_priced,
        }
        if existing is not None:
            for k, v in values.items():
                setattr(existing, k, v)
        else:
            session.add(AttributionReportModel(branch_id=branch_id, decision_date=report.decision_date, **values))
        await session.flush()

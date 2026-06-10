"""Post-hoc attribution of weekly portfolio decisions (Phase D).

Pure computation lives in compute_report(); DB/price orchestration is in
AttributionEngine (added in a later task). Spearman is implemented locally to
avoid a scipy dependency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

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
    """Return from first close on/after d0 to last close on/before d1."""
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

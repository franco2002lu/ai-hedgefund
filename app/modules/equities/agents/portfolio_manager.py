from __future__ import annotations

from collections import defaultdict

from app.modules.equities.config import AgentsConfig, PortfolioConfig
from app.modules.equities.models import CompositeScore, RebalanceOrder, StockSignal


class PortfolioManager:
    """Deterministic portfolio construction from analyst signals."""

    def __init__(
        self,
        agents_config: AgentsConfig,
        portfolio_config: PortfolioConfig,
    ) -> None:
        self.agents_config = agents_config
        self.portfolio_config = portfolio_config

    def compute_composite_scores(
        self,
        signals: list[StockSignal],
    ) -> list[CompositeScore]:
        """Compute weighted composite score per stock from analyst signals."""
        if not signals:
            return []
        by_symbol: dict[str, dict[str, StockSignal]] = defaultdict(dict)
        for sig in signals:
            by_symbol[sig.symbol][sig.analyst_type] = sig
        weight_map = {
            "fundamentals": self.agents_config.weight_fundamentals,
            "news": self.agents_config.weight_news,
            "technical": self.agents_config.weight_technical,
        }
        scores = []
        for symbol, analyst_signals in by_symbol.items():
            composite_score = 0.0
            composite_confidence = 0.0
            for analyst_type, weight in weight_map.items():
                sig = analyst_signals.get(analyst_type)
                if sig:
                    composite_score += weight * sig.bullish_score
                    composite_confidence += weight * sig.confidence
            conviction = composite_score * composite_confidence
            scores.append(
                CompositeScore(
                    symbol=symbol,
                    composite_score=composite_score,
                    composite_confidence=composite_confidence,
                    conviction=conviction,
                )
            )
        return scores

    def select_stocks(
        self,
        scores: list[CompositeScore],
    ) -> list[CompositeScore]:
        """Select top stocks by conviction, enforcing min/max guardrails."""
        if not scores:
            return []
        cfg = self.portfolio_config
        eligible = [s for s in scores if s.composite_score >= cfg.min_composite_score]
        eligible.sort(key=lambda s: s.conviction, reverse=True)
        return eligible[: min(cfg.target_holdings, cfg.max_holdings)]

    def size_positions(
        self,
        selected: list[CompositeScore],
    ) -> list[CompositeScore]:
        """Assign conviction-weighted target weights with 50% cap."""
        if not selected:
            return []
        cap = self.portfolio_config.max_position_weight
        total_conviction = sum(s.conviction for s in selected)
        if total_conviction == 0:
            equal = 1.0 / len(selected)
            return [
                CompositeScore(
                    symbol=s.symbol,
                    composite_score=s.composite_score,
                    composite_confidence=s.composite_confidence,
                    conviction=s.conviction,
                    target_weight=min(equal, cap),
                )
                for s in selected
            ]
        weights = {s.symbol: s.conviction / total_conviction for s in selected}
        for _ in range(10):
            capped: dict[str, float] = {}
            excess = 0.0
            uncapped_total = 0.0
            for sym, w in weights.items():
                if w > cap:
                    capped[sym] = cap
                    excess += w - cap
                else:
                    capped[sym] = w
                    uncapped_total += w
            if excess == 0:
                break
            if uncapped_total > 0:
                for sym in capped:
                    if capped[sym] < cap:
                        capped[sym] += excess * (capped[sym] / uncapped_total)
            weights = capped
        return [
            CompositeScore(
                symbol=s.symbol,
                composite_score=s.composite_score,
                composite_confidence=s.composite_confidence,
                conviction=s.conviction,
                target_weight=weights[s.symbol],
            )
            for s in selected
        ]

    def generate_orders(
        self,
        target: list[CompositeScore],
        current_positions: dict[str, float],
        nav: float,
        prices: dict[str, float],
    ) -> list[RebalanceOrder]:
        """Generate BUY/SELL orders by diffing target vs current portfolio."""
        orders = []
        threshold = self.portfolio_config.min_rebalance_threshold
        target_map = {s.symbol: s.target_weight for s in target}
        all_symbols = set(target_map.keys()) | set(current_positions.keys())
        for symbol in all_symbols:
            target_weight = target_map.get(symbol, 0.0)
            current_weight = current_positions.get(symbol, 0.0)
            delta = target_weight - current_weight
            if abs(delta) < threshold:
                continue
            price = prices.get(symbol)
            if not price or price <= 0:
                continue
            quantity = int(abs(delta * nav) / price)
            if quantity == 0:
                continue
            if delta > 0:
                side = "buy"
                reason = "new_position" if current_weight == 0.0 else "weight_adjustment"
            else:
                side = "sell"
                reason = "removed_position" if target_weight == 0.0 else "weight_adjustment"
            orders.append(
                RebalanceOrder(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    reason=reason,
                )
            )
        return orders

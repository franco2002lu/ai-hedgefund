from __future__ import annotations

import logging
from collections import defaultdict

from app.common.enums import OrderSide
from app.modules.equities.config import AgentsConfig, PortfolioConfig
from app.modules.equities.models import CompositeScore, RebalanceOrder, StockSignal

logger = logging.getLogger(__name__)


class PortfolioManager:
    """Deterministic portfolio construction from analyst signals."""

    def __init__(
        self,
        agents_config: AgentsConfig,
        portfolio_config: PortfolioConfig,
        analyst_weights: dict[str, float] | None = None,
    ) -> None:
        self.agents_config = agents_config
        self.portfolio_config = portfolio_config
        # Adaptive weights (2026-07-16 spec) override the static config when
        # provided; both are keyed fundamentals/news/technical and sum to 1.
        self.analyst_weights = analyst_weights or {
            "fundamentals": agents_config.weight_fundamentals,
            "news": agents_config.weight_news,
            "technical": agents_config.weight_technical,
        }

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
        weight_map = self.analyst_weights
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
        current_holdings: set[str] | None = None,
    ) -> list[CompositeScore]:
        """Top-N by conviction, with hysteresis for currently held names.

        New entries must rank in the top target_holdings. A held name is kept
        while its score >= exit_score_threshold and it ranks within
        max_holdings — held names near the rank boundary don't churn weekly.
        """
        if not scores:
            return []
        cfg = self.portfolio_config
        held = current_holdings or set()
        eligible = [s for s in scores if s.composite_score >= cfg.min_composite_score]
        eligible.sort(key=lambda s: s.conviction, reverse=True)
        top_n = min(cfg.target_holdings, cfg.max_holdings)
        selected = eligible[:top_n]
        keeps = [
            s
            for i, s in enumerate(eligible)
            if i >= top_n
            and s.symbol in held
            and s.composite_score >= cfg.exit_score_threshold
            and i < cfg.max_holdings
        ]
        # defensive cap; keeps are bounded by the rank window so this slice is normally a no-op
        return (selected + keeps)[: cfg.max_holdings]

    def size_positions(
        self,
        selected: list[CompositeScore],
    ) -> list[CompositeScore]:
        """Assign conviction-weighted target weights with 50% cap.

        Target weights sum to 1 - cash_buffer_pct: the buffer is deliberate
        headroom so buy fills (slippage, decision-to-fill drift) can't
        overdraw cash. See generate_orders for the sells-first complement.

        The realized max weight for any single position is therefore
        max_position_weight * (1 - cash_buffer_pct), not max_position_weight
        outright — the gap between the two is the buffer and is deliberately
        NOT redistributed back onto the capped position.
        """
        if not selected:
            return []
        cap = self.portfolio_config.max_position_weight
        buffer_scale = 1.0 - self.portfolio_config.cash_buffer_pct
        total_conviction = sum(s.conviction for s in selected)
        if total_conviction == 0:
            equal = 1.0 / len(selected)
            return [
                CompositeScore(
                    symbol=s.symbol,
                    composite_score=s.composite_score,
                    composite_confidence=s.composite_confidence,
                    conviction=s.conviction,
                    target_weight=min(equal, cap) * buffer_scale,
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
        weights = {sym: w * buffer_scale for sym, w in weights.items()}
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
        current_quantities: dict[str, float] | None = None,
    ) -> list[RebalanceOrder]:
        """Generate BUY/SELL orders by diffing target vs current portfolio.

        current_quantities (symbol -> held share count) enables full exits: a
        held name with target weight 0 sells its ENTIRE held quantity,
        bypassing the rebalance threshold — no fractional dust survives an
        exit. Callers that omit it get the legacy delta-only behavior.
        """
        orders = []
        target_map = {s.symbol: s.target_weight for s in target}
        # sorted() is required: iterating a set of symbol strings produces
        # hash-randomized order (PYTHONHASHSEED), which causes the resulting
        # orders list to vary across runs. Downstream execution consumes cash
        # and participation budget in list order, so this flips trade outcomes.
        all_symbols = sorted(set(target_map.keys()) | set(current_positions.keys()))
        for symbol in all_symbols:
            target_weight = target_map.get(symbol, 0.0)
            current_weight = current_positions.get(symbol, 0.0)
            price = prices.get(symbol)
            held = (current_quantities or {}).get(symbol, 0.0)
            if target_weight == 0.0 and current_weight > 0.0 and held > 0.0:
                # Full exit: sell exactly what is held. Unpriced names are
                # still skipped — execution could not fill them anyway and
                # they stay visible via the digest 'unpriced' warning.
                if not price or price <= 0:
                    continue
                orders.append(
                    RebalanceOrder(
                        symbol=symbol,
                        side="sell",
                        quantity=held,
                        reason="removed_position",
                    )
                )
                continue
            delta = target_weight - current_weight
            is_entry = current_weight == 0.0
            threshold = (
                self.portfolio_config.min_entry_weight if is_entry else self.portfolio_config.min_rebalance_threshold
            )
            if abs(delta) < threshold:
                if is_entry and target_weight > 0.0:
                    logger.info(
                        "Skipping sub-threshold entry %s: target %.3f%% < %.3f%%",
                        symbol,
                        target_weight * 100,
                        threshold * 100,
                    )
                continue
            if not price or price <= 0:
                continue
            quantity = round(abs(delta * nav) / price, 4)
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
        # Sells first so proceeds fund the buys; alphabetical within side keeps
        # the deterministic ordering downstream execution depends on.
        orders.sort(key=lambda o: (0 if o.side == OrderSide.SELL else 1, o.symbol))
        return orders

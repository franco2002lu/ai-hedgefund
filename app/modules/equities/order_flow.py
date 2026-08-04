"""Order-flow accounting: reconcile generated orders against execution results.

Pure logic (no I/O) so the tally is unit-testable and identical for live and
backtest paths. The counts must reconcile: generated = persisted + dropped,
persisted = filled + rejected. `dropped` > 0 means an order left no DB row —
the silent-loss mode behind the 2026-07-20/27 starved buys. Each entry in
`rejections` carries a `kind` of "dropped" or "rejected" so downstream
consumers (e.g. the Task 9 risk checks) can tell silent losses apart from
ordinary broker rejections instead of conflating their counts.
"""

from __future__ import annotations

from app.modules.equities.models import RebalanceOrder


def build_order_flow(
    orders: list[RebalanceOrder],
    order_results: list[dict | None],
    skips: list[dict],
) -> dict:
    """Tally execute-node result dicts (submit_order returns) per order.

    order_results[i] corresponds to orders[i]; None (or a missing tail entry)
    means the order never produced a result — dropped before submission.
    """
    filled = rejected = dropped = 0
    rejections: list[dict] = []
    for i, order in enumerate(orders):
        res = order_results[i] if i < len(order_results) else None
        if not isinstance(res, dict) or res.get("order_id") is None:
            dropped += 1
            reason = (
                res.get("message", "no message")
                if isinstance(res, dict)
                else "never submitted (missing instrument id or execution exception — see logs)"
            )
            rejections.append({"symbol": order.symbol, "side": str(order.side), "kind": "dropped", "reason": reason})
        elif res.get("status") == "filled":
            filled += 1
        else:
            rejected += 1
            rejections.append(
                {
                    "symbol": order.symbol,
                    "side": str(order.side),
                    "kind": "rejected",
                    "reason": res.get("message", "unknown"),
                }
            )
    return {
        "generated": len(orders),
        "persisted": len(orders) - dropped,
        "filled": filled,
        "rejected": rejected,
        "dropped": dropped,
        "skipped_unpriced": sum(1 for s in skips if s.get("reason") == "unpriced"),
        "skipped_below_entry": sum(1 for s in skips if s.get("reason") == "below_entry_threshold"),
        "rejections": rejections,
        "skips": list(skips),
    }

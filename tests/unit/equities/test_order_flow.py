"""Order-flow accounting: reconcile generated orders vs execution results."""

from app.modules.equities.models import RebalanceOrder
from app.modules.equities.order_flow import build_order_flow


def _order(symbol: str, side: str = "buy") -> RebalanceOrder:
    return RebalanceOrder(symbol=symbol, side=side, quantity=10.0, reason="weight_adjustment")


def test_all_filled():
    orders = [_order("AAA"), _order("BBB")]
    results = [
        {"success": True, "order_id": "1", "status": "filled"},
        {"success": True, "order_id": "2", "status": "filled"},
    ]
    flow = build_order_flow(orders, results, [])
    assert flow["generated"] == 2
    assert flow["persisted"] == 2
    assert flow["filled"] == 2
    assert flow["rejected"] == 0
    assert flow["dropped"] == 0
    assert flow["rejections"] == []


def test_rejection_with_reason_is_counted_and_listed():
    orders = [_order("AAA")]
    results = [
        {"success": False, "order_id": "1", "status": "rejected", "message": "Insufficient cash: cost 5 > available 1"}
    ]
    flow = build_order_flow(orders, results, [])
    assert flow["rejected"] == 1
    assert flow["persisted"] == 1
    assert flow["rejections"] == [
        {"symbol": "AAA", "side": "buy", "kind": "rejected", "reason": "Insufficient cash: cost 5 > available 1"}
    ]


def test_none_result_is_dropped():
    # e.g. missing instrument_id or an exception swallowed by _execute_trade
    orders = [_order("AAA", side="sell")]
    flow = build_order_flow(orders, [None], [])
    assert flow["dropped"] == 1
    assert flow["persisted"] == 0
    assert flow["rejections"][0]["symbol"] == "AAA"
    assert flow["rejections"][0]["kind"] == "dropped"
    assert "never submitted" in flow["rejections"][0]["reason"]


def test_order_id_none_result_is_dropped_with_its_message():
    # legacy validation-drop shape (pre hot-path fix)
    orders = [_order("BLK", side="sell")]
    results = [
        {
            "success": False,
            "order_id": None,
            "status": "rejected",
            "message": "Insufficient position: hold 74.0804 BLK, tried to sell 74.0814",
        }
    ]
    flow = build_order_flow(orders, results, [])
    assert flow["dropped"] == 1
    assert flow["rejections"][0]["kind"] == "dropped"
    assert "Insufficient position" in flow["rejections"][0]["reason"]


def test_rejections_distinguish_dropped_from_rejected():
    # Mixed run: two silent losses (no result at all, then order_id=None)
    # plus one ordinary broker rejection (has an order_id). Kinds must line
    # up 1:1 with rejections in order and must not be conflated in the counts.
    orders = [_order("AAA"), _order("BBB"), _order("CCC")]
    results = [
        None,
        {"success": False, "order_id": None, "status": "rejected", "message": "no instrument_id"},
        {"success": False, "order_id": "3", "status": "rejected", "message": "Insufficient cash"},
    ]
    flow = build_order_flow(orders, results, [])
    assert flow["dropped"] == 2
    assert flow["rejected"] == 1
    assert [r["kind"] for r in flow["rejections"]] == ["dropped", "dropped", "rejected"]


def test_skips_are_tallied():
    skips = [
        {"symbol": "AAPL", "reason": "unpriced", "is_exit": True},
        {"symbol": "TINY", "reason": "below_entry_threshold", "is_exit": False},
    ]
    flow = build_order_flow([], [], skips)
    assert flow["skipped_unpriced"] == 1
    assert flow["skipped_below_entry"] == 1
    assert flow["skips"] == skips


def test_missing_results_tail_counts_as_dropped():
    # execution loop crashed midway: fewer results than orders
    orders = [_order("AAA"), _order("BBB")]
    results = [{"success": True, "order_id": "1", "status": "filled"}]
    flow = build_order_flow(orders, results, [])
    assert flow["generated"] == 2
    assert flow["filled"] == 1
    assert flow["dropped"] == 1

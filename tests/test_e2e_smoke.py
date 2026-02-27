"""
End-to-end smoke test — full Phase 1 trade lifecycle.
Matches Section 14.8 of the architecture doc.

Requires: app running on localhost:8000 with seeded fund+branch+instrument.
"""

import asyncio

import httpx

BASE = "http://localhost:8000"
FUND_ID = "11111111-1111-1111-1111-111111111111"
BRANCH_ID = "22222222-2222-2222-2222-222222222222"


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as c:
        # 1. Health check
        r = await c.get("/health")
        assert r.status_code == 200
        print("1.  App healthy")

        # 2-3. Fund + branch already seeded. Create portfolio with $100K.
        r = await c.post(
            "/api/v1/portfolios",
            json={
                "branch_id": BRANCH_ID,
                "branch_type": "equities",
                "initial_cash": 100000.0,
                "margin_requirement": 0.5,
            },
        )
        assert r.status_code == 200
        print("2-3. Portfolio created with $100K cash")

        # 4. Upsert instrument AAPL
        r = await c.put(
            "/api/v1/instruments",
            json={
                "symbol": "AAPL",
                "name": "Apple Inc.",
                "asset_class": "equity",
                "exchange": "NASDAQ",
            },
        )
        assert r.status_code == 200
        instrument_id = r.json()["instrument_id"]
        print(f"4.  Instrument AAPL upserted: {instrument_id}")

        # 5. Fetch AAPL price
        r = await c.get(
            "/api/v1/prices/AAPL",
            params={
                "start_date": "2025-01-01",
                "end_date": "2025-01-10",
            },
        )
        assert r.status_code == 200
        bars = r.json()["bars"]
        assert len(bars) > 0
        latest_price = bars[-1]["close"]
        print(f"5.  AAPL price fetched: {len(bars)} bars, latest close=${latest_price}")

        # 6. Submit BUY order for 10 shares of AAPL
        r = await c.post(
            "/api/v1/orders",
            json={
                "branch_id": BRANCH_ID,
                "instrument_id": instrument_id,
                "symbol": "AAPL",
                "side": "buy",
                "order_type": "market",
                "quantity": 10.0,
                "confidence": 85.0,
                "reasoning": "Strong fundamentals, good entry point",
            },
        )
        assert r.status_code == 200, f"BUY failed: {r.text}"
        buy_result = r.json()
        assert buy_result["success"] is True
        fill_price = buy_result["fill_price"]
        order_id = buy_result["order_id"]
        _ = buy_result["trade_id"]  # verify key exists
        print(f"6.  BUY 10 AAPL filled @ ${fill_price:.2f}")

        # 7. Verify order status = FILLED
        r = await c.get(f"/api/v1/orders/{order_id}")
        assert r.status_code == 200
        assert r.json()["status"] == "filled"
        print("7.  Order status = filled")

        # 8. Verify position exists
        r = await c.get(f"/api/v1/portfolios/{BRANCH_ID}/positions/AAPL")
        assert r.status_code == 200
        pos = r.json()
        assert pos["long_quantity"] == 10.0
        assert pos["long_cost_basis"] > 0
        print(f"8.  Position: qty={pos['long_quantity']}, cost_basis=${pos['long_cost_basis']:.2f}")

        # 9. Verify portfolio cash decreased
        r = await c.get(f"/api/v1/portfolios/{BRANCH_ID}")
        assert r.status_code == 200
        portfolio = r.json()
        assert portfolio["cash"] < 100000.0
        print(f"9.  Portfolio: cash=${portfolio['cash']:.2f}, nav=${portfolio['nav']:.2f}")

        # 10. Verify event log
        r = await c.get("/api/v1/events", params={"limit": 20})
        assert r.status_code == 200
        events = r.json()
        event_types = [e["event_type"] for e in events]
        assert "trade.requested" in event_types
        assert "trade.executed" in event_types
        assert "portfolio.updated" in event_types
        print(f"10. Events: {event_types}")

        # 11. Take a portfolio snapshot
        r = await c.post(f"/api/v1/portfolios/{BRANCH_ID}/snapshots")
        assert r.status_code == 200
        print(f"11. Snapshot taken: nav=${r.json()['nav']:.2f}")

        # 12. Submit SELL order for 5 shares
        r = await c.post(
            "/api/v1/orders",
            json={
                "branch_id": BRANCH_ID,
                "instrument_id": instrument_id,
                "symbol": "AAPL",
                "side": "sell",
                "order_type": "market",
                "quantity": 5.0,
            },
        )
        assert r.status_code == 200, f"SELL failed: {r.text}"
        sell_result = r.json()
        assert sell_result["success"] is True
        sell_price = sell_result["fill_price"]
        print(f"12. SELL 5 AAPL filled @ ${sell_price:.2f}")

        # 13. Verify position reduced, realized P&L computed
        r = await c.get(f"/api/v1/portfolios/{BRANCH_ID}/positions/AAPL")
        assert r.status_code == 200
        pos = r.json()
        assert pos["long_quantity"] == 5.0
        print(f"13. Position: qty={pos['long_quantity']}, realized_pnl=${pos['realized_pnl_long']:.2f}")

        # 14. Verify portfolio cash increased
        r = await c.get(f"/api/v1/portfolios/{BRANCH_ID}")
        portfolio_after = r.json()
        assert portfolio_after["cash"] > portfolio["cash"]
        print(f"14. Cash after sell: ${portfolio_after['cash']:.2f}")

        # 15. Fetch portfolio summary — all fields consistent
        assert portfolio_after["total_long_exposure"] > 0
        print(
            f"15. Portfolio summary: long_exp=${portfolio_after['total_long_exposure']:.2f}, "
            f"realized_pnl=${portfolio_after['realized_pnl']:.2f}"
        )

        # 16. Fund summary
        r = await c.get(f"/api/v1/fund/{FUND_ID}/summary")
        assert r.status_code == 200
        fund = r.json()
        print(f"16. Fund: total_nav=${fund['total_nav']:.2f}, branches={len(fund['branches'])}")

        # 17. List orders and trades
        r = await c.get("/api/v1/orders", params={"branch_id": BRANCH_ID})
        assert r.status_code == 200
        orders = r.json()
        assert orders["total"] == 2
        print(f"17. Orders: {orders['total']} total")

        r = await c.get("/api/v1/trades", params={"branch_id": BRANCH_ID})
        assert r.status_code == 200
        trades = r.json()
        assert trades["total"] == 2
        print(f"    Trades: {trades['total']} total")

        print("\n" + "=" * 60)
        print("  ALL END-TO-END SMOKE TESTS PASSED")
        print("  Phase 1 infrastructure is ready for branch modules!")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

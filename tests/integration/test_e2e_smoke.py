"""End-to-end smoke test — full Phase 1 trade lifecycle.

Matches Section 14.8 of the architecture doc.

Requires: Running app at localhost:8000, seeded DB (clean — no prior run).

Seeded UUIDs (from README seed script):
  - Fund:   11111111-1111-1111-1111-111111111111
  - Branch: 22222222-2222-2222-2222-222222222222  (name: "US Equities")

Run: pytest tests/integration/test_e2e_smoke.py -m e2e -v
"""

import httpx
import pytest

pytestmark = pytest.mark.e2e

BASE_URL = "http://localhost:8000"
FUND_ID = "11111111-1111-1111-1111-111111111111"
BRANCH_ID = "22222222-2222-2222-2222-222222222222"


class TestE2ESmokeTradeLifecycle:
    """Full 17-step trade lifecycle verification."""

    async def test_full_trade_lifecycle(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
            # 1. Health check
            r = await client.get("/health")
            assert r.status_code == 200

            # 2-3. Create portfolio with $100K
            r = await client.post(
                "/api/v1/portfolios",
                json={
                    "branch_id": BRANCH_ID,
                    "branch_type": "equities",
                    "initial_cash": 100000.0,
                    "margin_requirement": 0.5,
                },
            )
            assert r.status_code == 200

            # 4. Upsert instrument AAPL
            r = await client.put(
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

            # 5. Fetch AAPL price
            r = await client.get(
                "/api/v1/prices/AAPL",
                params={"start_date": "2025-01-01", "end_date": "2025-01-10"},
            )
            assert r.status_code == 200
            bars = r.json()["bars"]
            assert len(bars) > 0

            # 6. Submit BUY order for 10 shares of AAPL
            r = await client.post(
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
            assert buy_result["fill_price"] > 0
            order_id = buy_result["order_id"]
            assert "trade_id" in buy_result

            # 7. Verify order status = FILLED
            r = await client.get(f"/api/v1/orders/{order_id}")
            assert r.status_code == 200
            assert r.json()["status"] == "filled"

            # 8. Verify position exists
            r = await client.get(f"/api/v1/portfolios/{BRANCH_ID}/positions/AAPL")
            assert r.status_code == 200
            pos = r.json()
            assert pos["long_quantity"] == 10.0
            assert pos["long_cost_basis"] > 0

            # 9. Verify portfolio cash decreased
            r = await client.get(f"/api/v1/portfolios/{BRANCH_ID}")
            assert r.status_code == 200
            portfolio = r.json()
            assert portfolio["cash"] < 100000.0

            # 10. Verify event log
            r = await client.get("/api/v1/events", params={"limit": 20})
            assert r.status_code == 200
            events = r.json()
            event_types = [e["event_type"] for e in events]
            assert "trade.requested" in event_types
            assert "trade.executed" in event_types
            assert "portfolio.updated" in event_types

            # 11. Take a portfolio snapshot
            r = await client.post(f"/api/v1/portfolios/{BRANCH_ID}/snapshots")
            assert r.status_code == 200

            # 12. Submit SELL order for 5 shares
            r = await client.post(
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

            # 13. Verify position reduced
            r = await client.get(f"/api/v1/portfolios/{BRANCH_ID}/positions/AAPL")
            assert r.status_code == 200
            pos = r.json()
            assert pos["long_quantity"] == 5.0

            # 14. Verify portfolio cash increased
            r = await client.get(f"/api/v1/portfolios/{BRANCH_ID}")
            portfolio_after = r.json()
            assert portfolio_after["cash"] > portfolio["cash"]

            # 15. Portfolio summary — all fields consistent
            assert portfolio_after["total_long_exposure"] > 0

            # 16. Fund summary
            r = await client.get(f"/api/v1/fund/{FUND_ID}/summary")
            assert r.status_code == 200
            fund = r.json()
            assert len(fund["branches"]) == 1

            # 17. List orders and trades
            r = await client.get("/api/v1/orders", params={"branch_id": BRANCH_ID})
            assert r.status_code == 200
            orders = r.json()
            assert orders["total"] == 2

            r = await client.get("/api/v1/trades", params={"branch_id": BRANCH_ID})
            assert r.status_code == 200
            trades = r.json()
            assert trades["total"] == 2

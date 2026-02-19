"""Checkpoint 2: Portfolio module + Event Log integration test."""

import asyncio

import httpx

BASE = "http://localhost:8000"
FUND_ID = "11111111-1111-1111-1111-111111111111"
BRANCH_ID = "22222222-2222-2222-2222-222222222222"


async def main():
    async with httpx.AsyncClient(base_url=BASE) as client:
        # 1. Health check
        r = await client.get("/health")
        assert r.status_code == 200, f"Health check failed: {r.text}"
        print("1. Health check OK")

        # 2. Create portfolio
        r = await client.post("/api/v1/portfolios", json={
            "branch_id": BRANCH_ID,
            "branch_type": "equities",
            "initial_cash": 100000.0,
            "margin_requirement": 0.5,
        })
        assert r.status_code == 200, f"Create portfolio failed: {r.text}"
        data = r.json()
        assert data["cash"] == 100000.0
        print(f"2. Portfolio created: {data['portfolio_id']}")

        # 3. Get portfolio
        r = await client.get(f"/api/v1/portfolios/{BRANCH_ID}")
        assert r.status_code == 200, f"Get portfolio failed: {r.text}"
        data = r.json()
        assert data["cash"] == 100000.0
        assert data["nav"] == 100000.0
        assert data["branch_type"] == "equities"
        print(f"3. Portfolio retrieved: cash={data['cash']}, nav={data['nav']}")

        # 4. Adjust cash +5000
        r = await client.put(f"/api/v1/portfolios/{BRANCH_ID}/cash", json={
            "amount": 5000.0,
            "reason": "allocation_increase",
        })
        assert r.status_code == 200, f"Adjust cash failed: {r.text}"
        data = r.json()
        assert data["cash"] == 105000.0
        print(f"4. Cash adjusted: cash={data['cash']}")

        # 5. Reject negative overdraft
        r = await client.put(f"/api/v1/portfolios/{BRANCH_ID}/cash", json={
            "amount": -200000.0,
            "reason": "test_overdraft",
        })
        assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"
        print("5. Overdraft correctly rejected")

        # 6. Take snapshot
        r = await client.post(f"/api/v1/portfolios/{BRANCH_ID}/snapshots")
        assert r.status_code == 200, f"Take snapshot failed: {r.text}"
        snap = r.json()
        assert snap["nav"] == 105000.0
        print(f"6. Snapshot taken: nav={snap['nav']}")

        # 7. List snapshots
        r = await client.get(f"/api/v1/portfolios/{BRANCH_ID}/snapshots")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        print(f"7. Snapshots listed: total={data['total']}")

        # 8. Fund summary
        r = await client.get(f"/api/v1/fund/{FUND_ID}/summary")
        assert r.status_code == 200, f"Fund summary failed: {r.text}"
        data = r.json()
        assert data["total_nav"] == 105000.0
        assert len(data["branches"]) == 1
        print(f"8. Fund summary: total_nav={data['total_nav']}, branches={len(data['branches'])}")

        # 9. Event log
        r = await client.get("/api/v1/events", params={"limit": 10})
        assert r.status_code == 200
        events = r.json()
        event_types = [e["event_type"] for e in events]
        assert "portfolio.updated" in event_types, f"Expected portfolio.updated in {event_types}"
        assert "portfolio.snapshot" in event_types, f"Expected portfolio.snapshot in {event_types}"
        print(f"9. Event log: {len(events)} events, types={event_types}")

        print("\n=== ALL CHECKPOINT 2 TESTS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())

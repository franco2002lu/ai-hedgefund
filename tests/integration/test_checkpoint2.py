"""Checkpoint 2: Portfolio module + Event Log integration test.

Requires: Running app at localhost:8000, seeded DB.

Seeded UUIDs (from README seed script):
  - Fund:   11111111-1111-1111-1111-111111111111
  - Branch: 22222222-2222-2222-2222-222222222222  (name: "US Equities")

Run: pytest tests/integration/test_checkpoint2.py -m e2e -v
"""

import httpx
import pytest

pytestmark = pytest.mark.e2e

BASE_URL = "http://localhost:8000"
FUND_ID = "11111111-1111-1111-1111-111111111111"
BRANCH_ID = "22222222-2222-2222-2222-222222222222"


class TestCheckpoint2:
    """Portfolio + Event Log 9-step verification."""

    async def test_full_portfolio_lifecycle(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
            # 1. Health check
            r = await client.get("/health")
            assert r.status_code == 200, f"Health check failed: {r.text}"

            # 2. Create portfolio
            r = await client.post(
                "/api/v1/portfolios",
                json={
                    "branch_id": BRANCH_ID,
                    "branch_type": "equities",
                    "initial_cash": 100000.0,
                    "margin_requirement": 0.5,
                },
            )
            assert r.status_code == 200, f"Create portfolio failed: {r.text}"
            data = r.json()
            assert data["cash"] == 100000.0

            # 3. Get portfolio
            r = await client.get(f"/api/v1/portfolios/{BRANCH_ID}")
            assert r.status_code == 200, f"Get portfolio failed: {r.text}"
            data = r.json()
            assert data["cash"] == 100000.0
            assert data["nav"] == 100000.0
            assert data["branch_type"] == "equities"

            # 4. Adjust cash +5000
            r = await client.put(
                f"/api/v1/portfolios/{BRANCH_ID}/cash",
                json={"amount": 5000.0, "reason": "allocation_increase"},
            )
            assert r.status_code == 200, f"Adjust cash failed: {r.text}"
            data = r.json()
            assert data["cash"] == 105000.0

            # 5. Reject negative overdraft
            r = await client.put(
                f"/api/v1/portfolios/{BRANCH_ID}/cash",
                json={"amount": -200000.0, "reason": "test_overdraft"},
            )
            assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"

            # 6. Take snapshot
            r = await client.post(f"/api/v1/portfolios/{BRANCH_ID}/snapshots")
            assert r.status_code == 200, f"Take snapshot failed: {r.text}"
            snap = r.json()
            assert snap["nav"] == 105000.0

            # 7. List snapshots
            r = await client.get(f"/api/v1/portfolios/{BRANCH_ID}/snapshots")
            assert r.status_code == 200
            data = r.json()
            assert data["total"] == 1

            # 8. Fund summary
            r = await client.get(f"/api/v1/fund/{FUND_ID}/summary")
            assert r.status_code == 200, f"Fund summary failed: {r.text}"
            data = r.json()
            assert data["total_nav"] == 105000.0
            assert len(data["branches"]) == 1

            # 9. Event log
            r = await client.get("/api/v1/events", params={"limit": 10})
            assert r.status_code == 200
            events = r.json()
            event_types = [e["event_type"] for e in events]
            assert "portfolio.updated" in event_types, f"Expected portfolio.updated in {event_types}"
            assert "portfolio.snapshot" in event_types, f"Expected portfolio.snapshot in {event_types}"

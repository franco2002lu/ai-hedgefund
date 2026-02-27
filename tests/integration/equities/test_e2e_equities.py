"""Tier 3: End-to-end smoke test for the equities branch pipeline.

Requires:
  - Running app at localhost:8000
  - DB seeded with fund/branch rows (see README seed script)
  - ANTHROPIC_API_KEY set in the server environment

Seeded UUIDs (from README seed script):
  - Fund:          11111111-1111-1111-1111-111111111111
  - Growth branch: 33333333-3333-3333-3333-333333333333  (name: "Equities Growth")
  - Value branch:  44444444-4444-4444-4444-444444444444  (name: "Equities Value")

Run: pytest tests/integration/equities/test_e2e_equities.py -m e2e -v
"""

import httpx
import pytest

pytestmark = pytest.mark.e2e

BASE_URL = "http://localhost:8000"

# Seeded branch IDs — must match README seed script
GROWTH_BRANCH_ID = "33333333-3333-3333-3333-333333333333"
PORTFOLIO_INITIAL_CASH = 1_000_000.0


class TestEquitiesE2EHealthCheck:
    """Verify the app is reachable before attempting pipeline tests."""

    async def test_health_endpoint(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
            r = await client.get("/health")
            assert r.status_code == 200, f"Health check failed: {r.text}"
            assert r.json()["status"] == "ok"


class TestFullEquitiesBranchRun:
    """Full 15-step verification of the equities growth pipeline.

    Uses pre-seeded fund/branch UUIDs. Creates a portfolio, triggers the
    pipeline, then asserts every stage produced valid output.
    """

    async def test_full_equities_growth_run(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=300.0) as client:
            # ------------------------------------------------------------------
            # Step 1: Health check
            # ------------------------------------------------------------------
            r = await client.get("/health")
            assert r.status_code == 200, f"Health check failed: {r.text}"

            # ------------------------------------------------------------------
            # Step 2: Create portfolio for the seeded growth branch
            # Accepts 400 if portfolio already exists from a prior run.
            # ------------------------------------------------------------------
            r = await client.post(
                "/api/v1/portfolios",
                json={
                    "branch_id": GROWTH_BRANCH_ID,
                    "branch_type": "equities",
                    "initial_cash": PORTFOLIO_INITIAL_CASH,
                },
            )
            # 200 = created, 500 = IntegrityError on duplicate branch_id (re-run)
            assert r.status_code in (200, 500), f"Create portfolio returned unexpected status {r.status_code}: {r.text}"

            # ------------------------------------------------------------------
            # Step 3: Get equities config (validates config endpoint)
            # ------------------------------------------------------------------
            r = await client.get("/api/v1/equities/config")
            assert r.status_code == 200, f"Config endpoint failed: {r.text}"
            config = r.json()
            assert "screening" in config, f"Config missing 'screening' key: {config}"

            # ------------------------------------------------------------------
            # Step 4: Trigger pipeline run
            # URL uses 'growth' — matches ilike('%growth%') against "Equities Growth"
            # ------------------------------------------------------------------
            r = await client.post("/api/v1/equities/growth/run")
            assert r.status_code == 200, f"Pipeline run failed (status={r.status_code}): {r.text}"
            result = r.json()

            # ------------------------------------------------------------------
            # Step 5: Verify universe fetched
            # ------------------------------------------------------------------
            assert result["universe_count"] > 0, (
                f"Universe is empty — ETF fetch likely failed. universe_count={result['universe_count']}"
            )

            # ------------------------------------------------------------------
            # Step 6: Verify screening reduced the universe
            # ------------------------------------------------------------------
            assert result["screened_count"] <= result["universe_count"], (
                f"screened_count ({result['screened_count']}) exceeds universe_count ({result['universe_count']})"
            )
            assert result["screened_count"] > 0, "Screener passed zero stocks — check filter thresholds."

            # ------------------------------------------------------------------
            # Step 7: Verify signals produced (3 analysts x screened stocks)
            # ------------------------------------------------------------------
            signals = result.get("signals", [])
            assert len(signals) > 0, (
                f"No signals produced. screened_count={result['screened_count']}. "
                "Check analyst agents and ANTHROPIC_API_KEY."
            )
            # Validate signal structure
            first_signal = signals[0]
            assert {"symbol", "analyst_type", "bullish_score", "confidence", "summary"} <= first_signal.keys(), (
                f"Signal missing expected keys: {first_signal}"
            )
            assert 1 <= first_signal["bullish_score"] <= 10
            assert 1 <= first_signal["confidence"] <= 10

            # ------------------------------------------------------------------
            # Step 8: Verify composite scores
            # ------------------------------------------------------------------
            scores = result.get("composite_scores", [])
            assert len(scores) > 0, f"No composite scores. signals={len(signals)}."
            first_score = scores[0]
            assert {"symbol", "composite_score", "composite_confidence", "conviction"} <= first_score.keys(), (
                f"Score missing expected keys: {first_score}"
            )

            # ------------------------------------------------------------------
            # Step 9: Verify orders generated (may be 0 if prices unavailable)
            # ------------------------------------------------------------------
            orders = result.get("orders", [])
            trades_executed = result.get("trades_executed", 0)

            # Orders require real-time prices from Yahoo Finance. If YF is
            # rate-limited or slow, get_current_price returns None and the
            # portfolio manager can't compute share quantities → 0 orders.
            # We assert the pipeline didn't error; trade assertions are
            # conditional on orders actually being generated.
            if len(orders) > 0:
                # ------------------------------------------------------------------
                # Step 10: Verify trades executed (primary bug assertion)
                # ------------------------------------------------------------------
                assert trades_executed > 0, (
                    f"trades_executed=0 after {len(orders)} orders generated. "
                    "Instrument FK bug may not be fixed."
                )

                # ------------------------------------------------------------------
                # Step 11: Verify portfolio now has positions
                # ------------------------------------------------------------------
                r = await client.get(f"/api/v1/portfolios/{GROWTH_BRANCH_ID}")
                assert r.status_code == 200, f"GET portfolio failed: {r.text}"
                portfolio = r.json()
                positions = portfolio.get("positions", [])
                assert len(positions) > 0, (
                    f"Portfolio has no positions after {trades_executed} trades."
                )

                # ------------------------------------------------------------------
                # Step 12: Verify cash was spent
                # ------------------------------------------------------------------
                cash = float(portfolio.get("cash", PORTFOLIO_INITIAL_CASH))
                assert cash < PORTFOLIO_INITIAL_CASH, (
                    f"Portfolio cash unchanged at {cash} after trades."
                )

            # ------------------------------------------------------------------
            # Step 13: Verify events were logged
            # ------------------------------------------------------------------
            r = await client.get(
                "/api/v1/events",
                params={"branch_id": GROWTH_BRANCH_ID, "limit": 100},
            )
            assert r.status_code == 200, f"GET events failed: {r.text}"
            events = r.json()
            assert isinstance(events, list), f"Expected events list, got {type(events).__name__}"
            assert len(events) > 0, "No events logged for branch."

            # ------------------------------------------------------------------
            # Step 14: Verify screening run persisted to DB
            # ------------------------------------------------------------------
            r = await client.get("/api/v1/equities/growth/screening-runs")
            assert r.status_code == 200, f"GET screening-runs failed: {r.text}"
            screening_runs = r.json()
            assert len(screening_runs) > 0, "No screening runs in DB."
            latest = screening_runs[0]
            assert latest["universe_count"] == result["universe_count"]
            assert latest["passed_count"] == result["screened_count"]

            # ------------------------------------------------------------------
            # Step 15: Verify signals and decisions endpoints return data
            # ------------------------------------------------------------------
            r = await client.get("/api/v1/equities/growth/signals")
            assert r.status_code == 200, f"GET signals failed: {r.text}"
            assert len(r.json()) > 0, "No signals in DB after pipeline run."

            r = await client.get("/api/v1/equities/growth/decisions")
            assert r.status_code == 200, f"GET decisions failed: {r.text}"
            assert len(r.json()) > 0, "No decisions in DB after pipeline run."


class TestEquitiesReadEndpoints:
    """Independent read endpoint verification.

    These tests can run standalone as long as the app is running and the DB
    has been seeded. They do not require a prior pipeline run.
    """

    async def test_config_endpoint(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
            r = await client.get("/api/v1/equities/config")
            assert r.status_code == 200
            config = r.json()
            assert "screening" in config
            assert "portfolio" in config
            assert "agents" in config

    async def test_signals_endpoint_returns_list(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
            r = await client.get("/api/v1/equities/growth/signals")
            assert r.status_code == 200
            assert isinstance(r.json(), list)

    async def test_screening_runs_endpoint_returns_list(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
            r = await client.get("/api/v1/equities/growth/screening-runs")
            assert r.status_code == 200
            assert isinstance(r.json(), list)

    async def test_decisions_endpoint_returns_list(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
            r = await client.get("/api/v1/equities/growth/decisions")
            assert r.status_code == 200
            assert isinstance(r.json(), list)

    async def test_404_for_unknown_branch(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
            r = await client.post("/api/v1/equities/nonexistent-branch-xyz/run")
            assert r.status_code == 404

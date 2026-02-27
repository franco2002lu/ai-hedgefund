"""Tests for enriched instrument upsert during the equities pipeline.

Verifies that run_pipeline() passes full company facts (sector, industry,
country, exchange, currency, metadata) to upsert_instrument() — not just
symbol + name + asset_class.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.modules.equities.config import EquitiesConfig
from app.modules.equities.models import UniverseStock
from app.modules.equities.service import EquitiesBranchService


def _make_universe_stocks() -> list[UniverseStock]:
    return [
        UniverseStock(symbol="AAPL", company_name="Apple Inc.", sector="Technology", industry="Consumer Electronics"),
        UniverseStock(symbol="MSFT", company_name="Microsoft Corp.", sector="Technology", industry="Software"),
    ]


def _make_company_facts(symbol: str) -> dict:
    facts_map = {
        "AAPL": {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "country": "United States",
            "exchange": "NMS",
            "currency": "USD",
            "description": "Apple designs consumer electronics.",
            "employees": 164000,
            "website": "https://apple.com",
        },
        "MSFT": {
            "symbol": "MSFT",
            "name": "Microsoft Corporation",
            "sector": "Technology",
            "industry": "Software—Infrastructure",
            "country": "United States",
            "exchange": "NMS",
            "currency": "USD",
            "description": "Microsoft develops software.",
            "employees": 221000,
            "website": "https://microsoft.com",
        },
    }
    return facts_map[symbol]


class TestEnrichedInstrumentUpsert:
    async def test_upsert_passes_full_company_facts(self):
        """Instrument upsert should include sector, industry, country, exchange, currency, and metadata."""
        config = EquitiesConfig()
        universe_stocks = _make_universe_stocks()

        # Mock data_service
        data_service = AsyncMock()
        data_service.get_company_facts = AsyncMock(side_effect=_make_company_facts)
        data_service.upsert_instrument = AsyncMock(
            return_value={"instrument_id": "fake-uuid", "symbol": "AAPL"}
        )

        # Mock universe_provider
        universe_provider = AsyncMock()
        universe_provider.get_holdings = AsyncMock(return_value=universe_stocks)

        # Mock session
        session = AsyncMock()

        svc = EquitiesBranchService(
            config=config,
            data_service=data_service,
            universe_provider=universe_provider,
        )

        # We only care about the instrument upsert, so mock the graph to skip it
        with patch(
            "app.modules.equities.service.build_equities_graph"
        ) as mock_graph_builder:
            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(
                return_value={
                    "universe": universe_stocks,
                    "screened": [],
                    "signals": [],
                    "scores": [],
                    "orders": [],
                    "trades": [],
                }
            )
            mock_graph_builder.return_value = mock_graph

            await svc.run_pipeline(
                branch_name="growth",
                branch_id="11111111-1111-1111-1111-111111111111",
                session=session,
            )

        # Verify upsert_instrument was called for each stock
        assert data_service.upsert_instrument.call_count == 2

        # Check the first upsert call (AAPL)
        first_call_args = data_service.upsert_instrument.call_args_list[0]
        instrument_data = first_call_args[0][1]  # second positional arg

        assert instrument_data["symbol"] == "AAPL"
        assert instrument_data["asset_class"] == "equity"
        assert instrument_data["sector"] == "Technology"
        assert instrument_data["industry"] == "Consumer Electronics"
        assert instrument_data["country"] == "United States"
        assert instrument_data["exchange"] == "NMS"
        assert instrument_data["currency"] == "USD"
        assert "metadata" in instrument_data
        assert instrument_data["metadata"]["description"] == "Apple designs consumer electronics."
        assert instrument_data["metadata"]["employees"] == 164000

    async def test_upsert_handles_facts_failure_gracefully(self):
        """If get_company_facts fails, upsert should still work with basic data."""
        config = EquitiesConfig()
        universe_stocks = _make_universe_stocks()

        data_service = AsyncMock()
        data_service.get_company_facts = AsyncMock(side_effect=Exception("API down"))
        data_service.upsert_instrument = AsyncMock(
            return_value={"instrument_id": "fake-uuid", "symbol": "AAPL"}
        )

        universe_provider = AsyncMock()
        universe_provider.get_holdings = AsyncMock(return_value=universe_stocks)

        session = AsyncMock()

        svc = EquitiesBranchService(
            config=config,
            data_service=data_service,
            universe_provider=universe_provider,
        )

        with patch(
            "app.modules.equities.service.build_equities_graph"
        ) as mock_graph_builder:
            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(
                return_value={
                    "universe": universe_stocks,
                    "screened": [],
                    "signals": [],
                    "scores": [],
                    "orders": [],
                    "trades": [],
                }
            )
            mock_graph_builder.return_value = mock_graph

            # Should not raise — graceful degradation
            await svc.run_pipeline(
                branch_name="growth",
                branch_id="11111111-1111-1111-1111-111111111111",
                session=session,
            )

        # Pipeline should complete without raising — the outer except at service.py:189
        # catches upsert failures and logs a warning, so instrument_ids will be empty
        # and all trade orders will be skipped. Verify the pipeline didn't crash.
        assert data_service.upsert_instrument.call_count == 2

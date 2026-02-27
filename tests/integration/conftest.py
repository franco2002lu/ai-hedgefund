"""Shared fixtures for Tier 2 integration tests.

These fixtures provide real external services (Yahoo Finance, SEC EDGAR)
and a real LLM client (Anthropic Claude) for integration testing. They are
scoped to the session to avoid redundant API calls across test classes.

Required environment variable: ANTHROPIC_API_KEY
"""

import os

import pytest

from app.modules.data_platform.adapters.sec_edgar import SECEdgarAdapter
from app.modules.data_platform.adapters.yahoo_finance import YahooFinanceAdapter
from app.modules.data_platform.cache import DataCache
from app.modules.data_platform.rate_limiter import RateLimiter
from app.modules.data_platform.service import DataPlatformService

# ---------------------------------------------------------------------------
# LLM client wrapper
# ---------------------------------------------------------------------------


class AnthropicAnalystClient:
    """Thin wrapper around the Anthropic SDK that returns a dict.

    Mirrors the interface expected by analyst agents:
        result = await llm_client.invoke(prompt)
    where result is a dict with keys: bullish_score, confidence, summary.
    """

    def __init__(self, model: str = "claude-sonnet-4-6", temperature: float = 0.3):
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            pytest.skip("ANTHROPIC_API_KEY not set -- skipping integration test")

        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model
        self.temperature = temperature

    async def invoke(self, prompt: str) -> dict:
        """Send prompt to Claude and parse a structured analyst response."""
        import json

        system_msg = (
            "You are a financial analyst assistant. Respond ONLY with a JSON object "
            "containing exactly three keys:\n"
            '  "bullish_score": integer 1-10 (1=very bearish, 10=very bullish)\n'
            '  "confidence": integer 1-10 (1=very uncertain, 10=very certain)\n'
            '  "summary": string (1-2 sentence analysis summary)\n'
            "Do not include any text outside the JSON object."
        )

        response = await self._client.messages.create(
            model=self.model,
            max_tokens=256,
            temperature=self.temperature,
            system=system_msg,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()

        # Strip markdown code fences if present
        backtick3 = chr(96) * 3
        if text.startswith(backtick3):
            text_lines = text.split("\n")
            text_lines = [ln for ln in text_lines if not ln.strip().startswith(backtick3)]
            text = "\n".join(text_lines).strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = {
                "bullish_score": 5,
                "confidence": 5,
                "summary": text[:200] if text else "Unable to parse LLM response.",
            }

        # Clamp scores to valid range
        bs = parsed.get("bullish_score", 5)
        conf = parsed.get("confidence", 5)
        parsed["bullish_score"] = max(1, min(10, int(bs)))
        parsed["confidence"] = max(1, min(10, int(conf)))
        if "summary" not in parsed or not parsed["summary"]:
            parsed["summary"] = "No analysis available."

        return parsed


# ---------------------------------------------------------------------------
# Stub SEC EDGAR adapter (real EDGAR not yet implemented)
# ---------------------------------------------------------------------------


class StubSECEdgarAdapter(SECEdgarAdapter):
    """Returns plausible stub data since SEC EDGAR adapter is not yet implemented.

    This allows the FundamentalsAnalyst to run end-to-end without real EDGAR.
    """

    async def get_recent_filings(self, symbol, filing_types=None, limit=4):
        return []

    async def get_earnings_data(self, symbol, quarters=4):
        from app.modules.data_platform.adapters.sec_edgar import QuarterlyEarnings

        stubs = {
            "AAPL": [
                QuarterlyEarnings(symbol="AAPL", fiscal_quarter="Q1 2025", revenue=124_300_000_000, eps=2.40),
                QuarterlyEarnings(symbol="AAPL", fiscal_quarter="Q4 2024", revenue=94_930_000_000, eps=1.64),
                QuarterlyEarnings(symbol="AAPL", fiscal_quarter="Q3 2024", revenue=85_777_000_000, eps=1.40),
                QuarterlyEarnings(symbol="AAPL", fiscal_quarter="Q2 2024", revenue=90_753_000_000, eps=1.53),
            ],
            "JPM": [
                QuarterlyEarnings(symbol="JPM", fiscal_quarter="Q1 2025", revenue=44_200_000_000, eps=4.81),
                QuarterlyEarnings(symbol="JPM", fiscal_quarter="Q4 2024", revenue=42_800_000_000, eps=4.33),
                QuarterlyEarnings(symbol="JPM", fiscal_quarter="Q3 2024", revenue=43_300_000_000, eps=4.37),
                QuarterlyEarnings(symbol="JPM", fiscal_quarter="Q2 2024", revenue=41_300_000_000, eps=4.26),
            ],
            "JNJ": [
                QuarterlyEarnings(symbol="JNJ", fiscal_quarter="Q1 2025", revenue=21_900_000_000, eps=2.77),
                QuarterlyEarnings(symbol="JNJ", fiscal_quarter="Q4 2024", revenue=22_500_000_000, eps=2.31),
                QuarterlyEarnings(symbol="JNJ", fiscal_quarter="Q3 2024", revenue=22_500_000_000, eps=2.42),
                QuarterlyEarnings(symbol="JNJ", fiscal_quarter="Q2 2024", revenue=22_400_000_000, eps=2.82),
            ],
        }
        return stubs.get(
            symbol,
            [
                QuarterlyEarnings(symbol=symbol, fiscal_quarter=f"Q{i} 2024", revenue=1_000_000_000, eps=1.00)
                for i in range(1, min(quarters, 4) + 1)
            ],
        )[:quarters]


# ---------------------------------------------------------------------------
# Fixtures
#
# real_llm_client is function-scoped because AsyncAnthropic uses an internal
# httpx connection pool that binds to the active event loop.  pytest-asyncio
# creates a new loop per test function by default, so a session-scoped client
# accumulates stale connections from closed loops and crashes with
# "RuntimeError: Event loop is closed".
#
# real_data_service and real_sec_edgar stay session-scoped — they don't hold
# async connections between calls (yfinance uses sync requests internally).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def real_data_service():
    """Create a real DataPlatformService backed by Yahoo Finance."""
    yahoo = YahooFinanceAdapter()
    registry = {
        "prices": {"equity": [yahoo], "all": [yahoo]},
        "fundamentals": {"equity": [yahoo]},
        "news": {"all": [yahoo]},
    }
    return DataPlatformService(
        adapter_registry=registry,
        cache=DataCache(),
        rate_limiter=RateLimiter(),
    )


@pytest.fixture
def real_llm_client():
    """Create a real Anthropic LLM client for analyst agents.

    Function-scoped so its httpx connections match the current event loop.
    """
    return AnthropicAnalystClient()


@pytest.fixture(scope="session")
def real_sec_edgar():
    """Create a stub SEC EDGAR adapter (real implementation pending)."""
    return StubSECEdgarAdapter()

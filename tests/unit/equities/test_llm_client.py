"""Unit tests for AnthropicAnalystClient — JSON parsing, clamping, and error handling."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.equities.agents.llm_client import AnthropicAnalystClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(text: str) -> MagicMock:
    """Build a mock Anthropic API response with the given text content."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


def _make_client_with_mock(response_text: str) -> tuple[AnthropicAnalystClient, AsyncMock]:
    """Create an AnthropicAnalystClient with a mocked _client."""
    client = AnthropicAnalystClient.__new__(AnthropicAnalystClient)
    client.model = "claude-sonnet-4-6"
    client.temperature = 0.3
    mock_api = AsyncMock()
    mock_api.messages.create = AsyncMock(return_value=_make_response(response_text))
    client._client = mock_api
    return client, mock_api


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAnthropicAnalystClient:
    async def test_parses_valid_json_response(self):
        payload = json.dumps({"bullish_score": 7, "confidence": 8, "summary": "Strong outlook"})
        client, _ = _make_client_with_mock(payload)

        result = await client.invoke("Analyze AAPL")

        assert result["bullish_score"] == 7
        assert result["confidence"] == 8
        assert result["summary"] == "Strong outlook"

    async def test_strips_markdown_code_fences(self):
        payload = '```json\n{"bullish_score": 6, "confidence": 9, "summary": "Bullish"}\n```'
        client, _ = _make_client_with_mock(payload)

        result = await client.invoke("Analyze AAPL")

        assert result["bullish_score"] == 6
        assert result["confidence"] == 9
        assert result["summary"] == "Bullish"

    async def test_handles_malformed_json_with_defaults(self):
        client, _ = _make_client_with_mock("This is not valid JSON at all")

        result = await client.invoke("Analyze AAPL")

        assert result["bullish_score"] == 5
        assert result["confidence"] == 5
        assert "This is not valid JSON" in result["summary"]

    async def test_clamps_bullish_score_to_range(self):
        payload = json.dumps({"bullish_score": 15, "confidence": -3, "summary": "Out of range"})
        client, _ = _make_client_with_mock(payload)

        result = await client.invoke("Analyze AAPL")

        assert result["bullish_score"] == 10  # clamped from 15
        assert result["confidence"] == 1  # clamped from -3

    async def test_clamps_confidence_to_range(self):
        payload = json.dumps({"bullish_score": 0, "confidence": 100, "summary": "Extreme"})
        client, _ = _make_client_with_mock(payload)

        result = await client.invoke("Analyze AAPL")

        assert result["bullish_score"] == 1  # clamped from 0
        assert result["confidence"] == 10  # clamped from 100

    async def test_missing_summary_gets_default(self):
        payload = json.dumps({"bullish_score": 5, "confidence": 5, "summary": ""})
        client, _ = _make_client_with_mock(payload)

        result = await client.invoke("Analyze AAPL")

        assert result["summary"] == "No analysis available."

    async def test_raises_when_no_api_key(self):
        client = AnthropicAnalystClient.__new__(AnthropicAnalystClient)
        client._client = None

        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY not set"):
            await client.invoke("Analyze AAPL")

    async def test_handles_float_scores(self):
        payload = json.dumps({"bullish_score": 7.5, "confidence": 3.9, "summary": "Fractional"})
        client, _ = _make_client_with_mock(payload)

        result = await client.invoke("Analyze AAPL")

        assert result["bullish_score"] == 7  # int(7.5) = 7
        assert result["confidence"] == 3  # int(3.9) = 3

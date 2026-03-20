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

    async def test_custom_system_prompt_replaces_default(self):
        """When system_prompt is provided, it replaces the default system message."""
        payload = json.dumps({"bullish_score": 8, "confidence": 9, "summary": "Skilled"})
        client, mock_api = _make_client_with_mock(payload)

        custom_prompt = "You are a growth analyst. Evaluate momentum."
        await client.invoke("Analyze NVDA", system_prompt=custom_prompt)

        call_kwargs = mock_api.messages.create.call_args.kwargs
        # System should be list-based with cache_control
        system_arg = call_kwargs["system"]
        assert isinstance(system_arg, list)
        assert system_arg[0]["text"] == custom_prompt
        assert system_arg[0]["cache_control"] == {"type": "ephemeral"}

    async def test_none_system_prompt_uses_default(self):
        """When system_prompt is None, uses the legacy default system message."""
        payload = json.dumps({"bullish_score": 5, "confidence": 5, "summary": "Default"})
        client, mock_api = _make_client_with_mock(payload)

        await client.invoke("Analyze AAPL")

        call_kwargs = mock_api.messages.create.call_args.kwargs
        system_arg = call_kwargs["system"]
        # Default should be a plain string (backward compatible)
        assert isinstance(system_arg, str)
        assert "financial analyst" in system_arg.lower()

    async def test_max_tokens_is_512(self):
        """max_tokens should be 512 to allow richer summaries."""
        payload = json.dumps({"bullish_score": 5, "confidence": 5, "summary": "Test"})
        client, mock_api = _make_client_with_mock(payload)

        await client.invoke("Analyze AAPL")

        call_kwargs = mock_api.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 512

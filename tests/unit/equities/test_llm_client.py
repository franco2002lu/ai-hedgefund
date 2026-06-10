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
    client._response_cache = None
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


# ---------------------------------------------------------------------------
# response_cache integration tests
# ---------------------------------------------------------------------------


class TestAnthropicAnalystClientResponseCache:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_api_call(self, tmp_path):
        from unittest.mock import AsyncMock

        from app.modules.backtest.llm_response_cache import LLMResponseCache
        from app.modules.equities.agents.llm_client import AnthropicAnalystClient

        cache = LLMResponseCache(tmp_path / "cache.db")
        try:
            cache.put(
                system_prompt="sys",
                user_prompt="usr",
                model="claude-sonnet-4-6",
                temperature=0.0,
                response={"bullish_score": 9, "confidence": 8, "summary": "cached"},
            )
            client = AnthropicAnalystClient(
                model="claude-sonnet-4-6",
                temperature=0.0,
                response_cache=cache,
            )
            # Swap in a mock that would FAIL the test if called
            client._client = AsyncMock()
            client._client.messages.create.side_effect = AssertionError("should not hit API on cache hit")

            result = await client.invoke("usr", system_prompt="sys")
            assert result == {"bullish_score": 9, "confidence": 8, "summary": "cached"}
            assert cache.hits == 1
            assert cache.misses == 0
        finally:
            cache.close()

    @pytest.mark.asyncio
    async def test_cache_miss_calls_api_and_stores_result(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock

        from app.modules.backtest.llm_response_cache import LLMResponseCache
        from app.modules.equities.agents.llm_client import AnthropicAnalystClient

        cache = LLMResponseCache(tmp_path / "cache.db")
        try:
            client = AnthropicAnalystClient(
                model="claude-sonnet-4-6",
                temperature=0.0,
                response_cache=cache,
            )
            # Mock the API response
            fake_response = MagicMock()
            fake_response.content = [MagicMock(text='{"bullish_score": 6, "confidence": 5, "summary": "mock"}')]
            client._client = AsyncMock()
            client._client.messages.create = AsyncMock(return_value=fake_response)

            result = await client.invoke("usr", system_prompt="sys")
            assert result == {"bullish_score": 6, "confidence": 5, "summary": "mock"}
            assert cache.misses == 1
            # Second call should hit cache, not API
            client._client.messages.create.reset_mock()
            client._client.messages.create.side_effect = AssertionError("should be cached now")
            result2 = await client.invoke("usr", system_prompt="sys")
            assert result2 == result
            assert cache.hits == 1
        finally:
            cache.close()

    @pytest.mark.asyncio
    async def test_no_cache_when_response_cache_is_none(self):
        from unittest.mock import AsyncMock, MagicMock

        from app.modules.equities.agents.llm_client import AnthropicAnalystClient

        client = AnthropicAnalystClient(
            model="claude-sonnet-4-6",
            temperature=0.0,
            response_cache=None,
        )
        fake_response = MagicMock()
        fake_response.content = [MagicMock(text='{"bullish_score": 5, "confidence": 5, "summary": "no cache"}')]
        client._client = AsyncMock()
        client._client.messages.create = AsyncMock(return_value=fake_response)

        result = await client.invoke("usr", system_prompt="sys")
        assert result["bullish_score"] == 5
        # Second call should hit API again (no cache)
        result2 = await client.invoke("usr", system_prompt="sys")
        assert result2["bullish_score"] == 5
        assert client._client.messages.create.call_count == 2


# ---------------------------------------------------------------------------
# invoke_raw tests
# ---------------------------------------------------------------------------


async def test_invoke_raw_returns_text_without_parsing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = AnthropicAnalystClient()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='{"ranking": ["A", "B"]}')]
    client._client = MagicMock()
    client._client.messages.create = AsyncMock(return_value=fake_response)

    text = await client.invoke_raw("rank these", system_prompt="you are a ranker")

    assert text == '{"ranking": ["A", "B"]}'
    kwargs = client._client.messages.create.call_args.kwargs
    assert kwargs["max_tokens"] == 2048


async def test_invoke_raw_uses_response_cache(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cache = MagicMock()
    cache.get.return_value = {"raw_text": "cached!"}
    client = AnthropicAnalystClient(response_cache=cache)
    client._client = MagicMock()
    client._client.messages.create = AsyncMock()

    text = await client.invoke_raw("p", system_prompt="s")

    assert text == "cached!"
    client._client.messages.create.assert_not_called()


async def test_invoke_raw_stores_to_cache_on_miss(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cache = MagicMock()
    cache.get.return_value = None
    client = AnthropicAnalystClient(response_cache=cache)
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text="raw output")]
    client._client = MagicMock()
    client._client.messages.create = AsyncMock(return_value=fake_response)

    text = await client.invoke_raw("p", system_prompt="s")

    assert text == "raw output"
    cache.put.assert_called_once_with("s", "p", client.model, client.temperature, {"raw_text": "raw output"})
